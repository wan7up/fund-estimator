from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time as datetime_time, timedelta
from typing import Any
from zoneinfo import ZoneInfo
from urllib.parse import quote

import httpx
from lxml import html

from fund_estimator.data_sources.eastmoney import DEFAULT_HEADERS, infer_market, to_eastmoney_secid
from fund_estimator.models.lof import (
    LofMarketQuote,
    LofOpportunityResponse,
    LofPremiumItem,
    LofProxyMove,
    LofTradingStatus,
    LofWatchlistItem,
)
from fund_estimator.models.schema import FundProfile, FundSearchResult
from fund_estimator.services.cache import SQLiteCache
from fund_estimator.services.exceptions import AppError, DataSourceError
from fund_estimator.services.http_settings import http_trust_env
from fund_estimator.services.lof_config import (
    CORE_CROSS_BORDER_LOFS,
    CORE_LOF_BY_CODE,
    CoreLof,
    looks_like_lof_code,
    looks_like_lof_fund,
    looks_like_lof_name,
)


LOF_QUOTE_TTL_SECONDS = 15
LOF_STATUS_TTL_SECONDS = 10 * 60
LOF_SCAN_TTL_SECONDS = 15
LOF_DISCOVERY_TTL_SECONDS = 60
LOF_LATEST_NAV_TTL_SECONDS = 30 * 60
PROXY_QUOTE_TTL_SECONDS = 10 * 60
DEFAULT_MIN_TURNOVER_YUAN = 3_000_000
DEFAULT_MIN_DOMESTIC_TURNOVER_RATE_PCT = 10.0
DEFAULT_NORMAL_THRESHOLD_PCT = 2.0
DEFAULT_STRONG_THRESHOLD_PCT = 5.0
DEFAULT_HIGH_SINGLE_DAY_SIGNAL_PCT = 8.0
DISCOVERY_MAX_CODES = 500
DISCOVERY_DEEP_PROFILE_MAX_CODES = 80
DISCOVERY_PREOPEN_DEEP_PROFILE_MAX_CODES = 120
TRADING_PAUSED = "\u6682\u505c"
MARKET_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class HaoEtfSnapshot:
    code: str
    estimated_nav: float | None
    estimated_premium_pct: float | None
    exchange_price: float | None
    exchange_change_pct: float | None
    purchase_status: str | None
    proxy_symbol: str | None
    proxy_change_pct: float | None
    source: str = "haoetf"


@dataclass(frozen=True)
class LatestFundNav:
    nav_date: date
    nav: float


def _parse_float(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    if isinstance(value, str):
        value = value.replace(",", "").replace("%", "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _classify_signal(
    premium_pct: float | None,
    *,
    normal_threshold_pct: float,
    strong_threshold_pct: float,
) -> tuple[str, str, bool]:
    if premium_pct is None:
        return "unknown", "none", False
    abs_value = abs(premium_pct)
    if abs_value < normal_threshold_pct:
        return "neutral", "none", False
    direction = "premium" if premium_pct > 0 else "discount"
    level = "strong" if abs_value >= strong_threshold_pct else "normal"
    return direction, level, True


def _is_trade_leg_paused(direction: str, status: LofTradingStatus) -> bool:
    return (direction == "premium" and status.purchase_status == TRADING_PAUSED) or (
        direction == "discount" and status.redemption_status == TRADING_PAUSED
    )


class EastmoneyLofMarketDataSource:
    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    async def get_quotes(self, codes: list[str]) -> dict[str, LofMarketQuote]:
        secids = [to_eastmoney_secid(code) for code in codes]
        secids = [secid for secid in secids if secid]
        if not secids:
            return {}
        url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        params = {
            "fltt": "2",
            "secids": ",".join(secids),
            "fields": "f12,f14,f2,f3,f18,f6,f8",
            "_": str(int(time.time() * 1000)),
        }
        headers = {**DEFAULT_HEADERS, "Referer": "https://quote.eastmoney.com/"}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, trust_env=http_trust_env()) as client:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise DataSourceError(
                    "LOF_QUOTE_FETCH_FAILED",
                    "LOF 场内实时行情获取失败",
                    details={"codes": codes, "error": str(exc)},
                ) from exc
        rows = (response.json().get("data") or {}).get("diff") or []
        if isinstance(rows, dict):
            rows = rows.values()
        quote_time = datetime.now(UTC)
        quotes: dict[str, LofMarketQuote] = {}
        for row in rows:
            quote = self._quote_from_row(row, quote_time=quote_time)
            if quote is not None:
                quotes[quote.code] = quote
        return quotes

    async def get_all_quotes(self) -> dict[str, LofMarketQuote]:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1",
            "pz": "100",
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "wbp2u": "|0|0|0|web",
            "fid": "f3",
            "fs": "b:MK0404,b:MK0405,b:MK0406,b:MK0407",
            "fields": "f12,f13,f14,f2,f3,f18,f6,f8",
            "_": str(int(time.time() * 1000)),
        }
        headers = {**DEFAULT_HEADERS, "Referer": "https://quote.eastmoney.com/center/gridlist.html#fund_lof"}
        quotes: dict[str, LofMarketQuote] = {}
        quote_time = datetime.now(UTC)
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, trust_env=http_trust_env()) as client:
            total = None
            page = 1
            while total is None or len(quotes) < total:
                params["pn"] = str(page)
                try:
                    response = await client.get(url, params=params)
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise DataSourceError(
                        "LOF_DISCOVERY_FETCH_FAILED",
                        "LOF 全市场行情列表获取失败",
                        details={"page": page, "error": str(exc)},
                    ) from exc
                payload = response.json().get("data") or {}
                rows = payload.get("diff") or []
                if isinstance(rows, dict):
                    rows = list(rows.values())
                if total is None:
                    total = int(payload.get("total") or len(rows))
                if not rows:
                    break
                for row in rows:
                    quote = self._quote_from_row(row, quote_time=quote_time)
                    if quote is not None:
                        quotes[quote.code] = quote
                page += 1
                if page > 20:
                    break
        return quotes

    @staticmethod
    def _quote_from_row(row: Any, *, quote_time: datetime) -> LofMarketQuote | None:
        code = str(row.get("f12") or "")
        if not code:
            return None
        market = infer_market(code)
        market_id = str(row.get("f13") or "")
        if market_id == "1":
            market = "SH"
        elif market_id == "0":
            market = "SZ"
        latest = _parse_float(row.get("f2"))
        change_pct = _parse_float(row.get("f3"))
        previous_close = _parse_float(row.get("f18"))
        if latest is None and previous_close is not None:
            latest = previous_close
        turnover_yuan = _parse_float(row.get("f6"))
        turnover_rate_pct = _parse_float(row.get("f8"))
        return LofMarketQuote(
            code=code,
            name=str(row.get("f14") or code),
            latest_price=latest,
            previous_close=previous_close,
            change_pct=change_pct,
            turnover_yuan=turnover_yuan,
            turnover_rate_pct=turnover_rate_pct,
            quote_time=quote_time,
            market=market if market in {"SH", "SZ"} else "UNKNOWN",
            source="eastmoney_lof_list",
        )


class EastmoneyLofTradingStatusDataSource:
    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    async def get_status(self, code: str, profile: FundProfile) -> LofTradingStatus:
        url = f"https://fundf10.eastmoney.com/jjfl_{code}.html"
        async with httpx.AsyncClient(timeout=self.timeout, headers=DEFAULT_HEADERS, trust_env=http_trust_env()) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPError:
                return self._fallback_from_profile(profile, warning="申购/赎回状态页面不可用")
        return self._parse_status(response.text, profile)

    def _fallback_from_profile(self, profile: FundProfile, *, warning: str | None = None) -> LofTradingStatus:
        return LofTradingStatus(
            purchase_status="unknown",
            redemption_status="unknown",
            daily_purchase_limit_yuan=None,
            fee_rate_pct=profile.details.trading.current_rate_pct,
            source=profile.source,
            warning=warning or "申购/赎回状态未能解析",
        )

    def _parse_status(self, text: str, profile: FundProfile) -> LofTradingStatus:
        doc = html.fromstring(text)
        visible_text = " ".join(part.strip() for part in doc.xpath("//text()") if part.strip())
        compact = re.sub(r"\s+", "", visible_text)
        trading_purchase, trading_redemption = self._extract_trading_statuses(compact)
        purchase = trading_purchase or self._extract_status(compact, ("申购状态", "申购"))
        redemption = trading_redemption or self._extract_status(compact, ("赎回状态", "赎回"))
        limit = self._extract_limit_yuan(compact)
        warning = None
        if purchase == "unknown" and redemption == "unknown":
            warning = "申购/赎回状态未能解析"
        return LofTradingStatus(
            purchase_status=purchase,
            redemption_status=redemption,
            daily_purchase_limit_yuan=limit,
            fee_rate_pct=profile.details.trading.current_rate_pct,
            source="eastmoney_f10",
            warning=warning,
        )

    @staticmethod
    def _extract_trading_statuses(text: str) -> tuple[str | None, str | None]:
        index = text.find("交易状态")
        if index < 0:
            return None, None
        window = text[index : index + 160]
        purchase = None
        redemption = None
        if "暂停申购" in window:
            purchase = "暂停"
        elif "限制大额申购" in window or "限大额申购" in window or "限大额" in window:
            purchase = "限制大额"
        elif "开放申购" in window or "可申购" in window:
            purchase = "开放"
        if "暂停赎回" in window:
            redemption = "暂停"
        elif "开放赎回" in window or "可赎回" in window:
            redemption = "开放"
        return purchase, redemption

    @staticmethod
    def _extract_status(text: str, keys: tuple[str, ...]) -> str:
        window = ""
        for key in keys:
            index = text.find(key)
            if index >= 0:
                window = text[index : index + 80]
                break
        if not window:
            window = text
        checks = (
            ("暂停", "暂停"),
            ("限制大额", "限制大额"),
            ("限大额", "限制大额"),
            ("开放", "开放"),
            ("可申购", "开放"),
            ("可赎回", "开放"),
        )
        for token, label in checks:
            if token in window:
                return label
        return "unknown"

    @staticmethod
    def _extract_limit_yuan(text: str) -> float | None:
        limit_keys = r"(?:日累计(?:申购限额|购买上限)|单日累计(?:申购限额|购买上限)|单日(?:申购限额|购买上限)|每日(?:申购限额|购买上限)|申购上限|购买上限|限购)"
        if re.search(rf"{limit_keys}[^0-9]{{0,20}}(?:无限额|不限|不限制)", text):
            return None
        min_purchase_terms = r"(?:最低|起购|起点|申购起点|购买起点|定投起点|首次申购|追加申购|首次购买|追加购买|最小)"
        forward_match = re.search(rf"{limit_keys}[^0-9]{{0,20}}(\d+(?:\.\d+)?)(万|元)", text)
        if forward_match:
            value = float(forward_match.group(1))
            unit = forward_match.group(2)
            return value * 10_000 if unit == "万" else value
        reverse_match = re.search(rf"(\d+(?:\.\d+)?)(万|元)[^，。；;]{{0,20}}{limit_keys}", text)
        if reverse_match:
            context = text[max(0, reverse_match.start() - 20) : reverse_match.end() + 20]
            if re.search(min_purchase_terms, context):
                return None
            value = float(reverse_match.group(1))
            unit = reverse_match.group(2)
            return value * 10_000 if unit == "万" else value
        return None


class EastmoneyLofLatestNavDataSource:
    def __init__(self, timeout: float = 6.0) -> None:
        self.timeout = timeout

    async def get_latest_nav(self, code: str) -> LatestFundNav | None:
        url = "https://fundf10.eastmoney.com/F10DataApi.aspx"
        params = {
            "type": "lsjz",
            "code": code,
            "page": "1",
            "per": "1",
            "sdate": "",
            "edate": "",
            "rt": "0",
        }
        headers = {**DEFAULT_HEADERS, "Referer": f"https://fundf10.eastmoney.com/jjjz_{code}.html"}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, trust_env=http_trust_env()) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
        return self._parse_latest_nav(response.text)

    @staticmethod
    def _parse_latest_nav(text: str) -> LatestFundNav | None:
        match = re.search(
            r"<tr>\s*<td>(\d{4}-\d{2}-\d{2})</td>\s*<td[^>]*>(\d+(?:\.\d+)?)</td>",
            text,
        )
        if not match:
            return None
        try:
            return LatestFundNav(nav_date=date.fromisoformat(match.group(1)), nav=float(match.group(2)))
        except ValueError:
            return None


class YahooProxyDataSource:
    def __init__(self, timeout: float = 6.0) -> None:
        self.timeout = timeout

    async def get_changes(self, symbols: list[str], *, base_date: date | None = None) -> dict[str, float]:
        unique_symbols = list(dict.fromkeys(symbols))
        if not unique_symbols:
            return {}
        headers = {"User-Agent": "Mozilla/5.0 fund-estimator/0.1"}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, trust_env=http_trust_env()) as client:
            rows = await asyncio.gather(
                *(self._get_chart_change(client, symbol, base_date=base_date) for symbol in unique_symbols),
                return_exceptions=True,
            )
        result: dict[str, float] = {}
        errors: list[str] = []
        for symbol, row in zip(unique_symbols, rows, strict=False):
            if isinstance(row, Exception):
                errors.append(f"{symbol}: {row}")
            elif row is not None:
                result[symbol] = row
        if not result and errors:
            raise DataSourceError("PROXY_QUOTE_FETCH_FAILED", "海外代理行情获取失败", details={"errors": errors[:5]})
        return result

    async def _get_chart_change(self, client: httpx.AsyncClient, symbol: str, *, base_date: date | None = None) -> float | None:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
        params = {"range": "10d", "interval": "1d"}
        if base_date is not None:
            period1 = int(datetime.combine(base_date - timedelta(days=5), datetime_time.min, tzinfo=UTC).timestamp())
            period2 = int((datetime.now(UTC) + timedelta(days=1)).timestamp())
            params = {"period1": str(period1), "period2": str(period2), "interval": "1d"}
        response = await client.get(url, params=params)
        response.raise_for_status()
        result = ((response.json().get("chart") or {}).get("result") or [None])[0]
        if not result:
            return None
        timestamps = result.get("timestamp") or []
        closes = (((result.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
        points: list[tuple[date, float]] = []
        for timestamp, close in zip(timestamps, closes, strict=False):
            if close is None:
                continue
            try:
                points.append((datetime.fromtimestamp(int(timestamp), tz=UTC).date(), float(close)))
            except (TypeError, ValueError, OSError):
                continue
        if len(points) < 2:
            return None
        latest = _parse_float((result.get("meta") or {}).get("regularMarketPrice")) or points[-1][1]
        if base_date is None:
            base = points[-2][1]
        else:
            base_candidates = [close for point_date, close in points if point_date <= base_date]
            base = base_candidates[-1] if base_candidates else points[0][1]
        if base == 0:
            return None
        return (latest / base - 1) * 100


class HaoEtfDataSource:
    def __init__(self, timeout: float = 6.0) -> None:
        self.timeout = timeout

    async def get_snapshots(self, codes: list[str]) -> dict[str, HaoEtfSnapshot]:
        headers = {"User-Agent": "Mozilla/5.0 fund-estimator/0.1"}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, trust_env=http_trust_env()) as client:
            rows = await asyncio.gather(
                *(self._get_snapshot(client, code) for code in codes),
                return_exceptions=True,
            )
        snapshots: dict[str, HaoEtfSnapshot] = {}
        for code, row in zip(codes, rows, strict=False):
            if isinstance(row, HaoEtfSnapshot):
                snapshots[code] = row
        return snapshots

    async def _get_snapshot(self, client: httpx.AsyncClient, code: str) -> HaoEtfSnapshot | None:
        response = await client.get(f"https://www.haoetf.com/qdii/{code}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        text = " ".join(part.strip() for part in html.fromstring(response.text).xpath("//text()") if part.strip())
        row_match = re.search(
            rf"\b{re.escape(code)}\b\s+\S+\s+"
            r"(?P<estimated>\d+(?:\.\d+)?)\s+"
            r"(?P<premium>[-+]?\d+(?:\.\d+)?)%\s+"
            r"\d+(?:\.\d+)?\s+[-+]?\d+(?:\.\d+)?%\s+"
            r"\d{2}-\d{2}\s+"
            r"(?P<price>\d+(?:\.\d+)?)\s+"
            r"(?P<exchange_change>[-+]?\d+(?:\.\d+)?)%",
            text,
        )
        futures_match = re.search(
            r"相关期货.*?\b(?P<symbol>[A-Z]{1,6})\b\s+[\u4e00-\u9fffA-Za-z0-9（）() -]+?\s+"
            r"\d+(?:\.\d+)?\s+(?P<change>[-+]?\d+(?:\.\d+)?)%",
            text,
        )
        return HaoEtfSnapshot(
            code=code,
            estimated_nav=_parse_float(row_match.group("estimated")) if row_match else None,
            estimated_premium_pct=_parse_float(row_match.group("premium")) if row_match else None,
            exchange_price=_parse_float(row_match.group("price")) if row_match else None,
            exchange_change_pct=_parse_float(row_match.group("exchange_change")) if row_match else None,
            purchase_status=self._parse_purchase_status(text),
            proxy_symbol=futures_match.group("symbol") if futures_match else None,
            proxy_change_pct=_parse_float(futures_match.group("change")) if futures_match else None,
        )

    @staticmethod
    def _parse_purchase_status(text: str) -> str | None:
        if "暂停申购" in text:
            return "暂停"
        if "限制大额申购" in text or "限大额" in text:
            return "限制大额"
        if "开放申购" in text:
            return "开放"
        return None


class LofMonitorService:
    def __init__(
        self,
        *,
        estimator: Any,
        cache: SQLiteCache,
        market_source: Any | None = None,
        status_source: Any | None = None,
        latest_nav_source: Any | None = None,
        proxy_source: Any | None = None,
        haoetf_source: Any | None = None,
        discovery_market_source: Any | None = None,
        notice_cooldown_reader: Callable[[], set[str]] | None = None,
        notice_signal_history_reader: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.estimator = estimator
        self.cache = cache
        self.market_source = market_source or EastmoneyLofMarketDataSource()
        self.status_source = status_source or EastmoneyLofTradingStatusDataSource()
        self.latest_nav_source = latest_nav_source or EastmoneyLofLatestNavDataSource()
        self.proxy_source = proxy_source or YahooProxyDataSource()
        self.haoetf_source = haoetf_source or HaoEtfDataSource()
        self.discovery_market_source = discovery_market_source or (
            self.market_source if hasattr(self.market_source, "get_all_quotes") else EastmoneyLofMarketDataSource()
        )
        self.notice_cooldown_reader = notice_cooldown_reader
        self.notice_signal_history_reader = notice_signal_history_reader

    async def search_lofs(self, query: str) -> list[FundSearchResult]:
        results = await self.estimator.search_funds(query)
        return [item for item in results if looks_like_lof_fund(item.code, item.name, item.fund_type)][:30]

    def list_watchlist(self, device_id: str) -> list[LofWatchlistItem]:
        return [LofWatchlistItem(**dict(row)) for row in self.cache.list_lof_watchlist(device_id)]

    async def add_watchlist(self, code: str, device_id: str) -> LofWatchlistItem:
        profile = await self.estimator.get_profile(code)
        if not looks_like_lof_fund(profile.code, profile.name, profile.fund_type):
            raise AppError("NOT_LOF_FUND", "当前基金不像 LOF/QDII 场内监控标的", status_code=422, details={"code": code})
        self.cache.add_lof_watchlist(profile.code, profile.name, device_id)
        return next(item for item in self.list_watchlist(device_id) if item.code == profile.code)

    def delete_watchlist(self, code: str, device_id: str) -> bool:
        return self.cache.delete_lof_watchlist(code, device_id)

    def reorder_watchlist(self, codes: list[str], device_id: str) -> list[LofWatchlistItem]:
        if not self.cache.reorder_lof_watchlist(codes, device_id):
            raise AppError("INVALID_WATCHLIST_ORDER", "LOF 自选排序列表必须与当前自选完全一致", status_code=422)
        return self.list_watchlist(device_id)

    @staticmethod
    def _cache_number(value: float | int) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if number.is_integer():
            return str(int(number))
        return f"{number:g}"

    async def get_opportunities(
        self,
        *,
        device_id: str = "default",
        normal_threshold_pct: float = DEFAULT_NORMAL_THRESHOLD_PCT,
        strong_threshold_pct: float = DEFAULT_STRONG_THRESHOLD_PCT,
        min_turnover_yuan: float = DEFAULT_MIN_TURNOVER_YUAN,
        limit: int = 80,
        refresh: bool = True,
    ) -> LofOpportunityResponse:
        min_turnover_key = self._cache_number(min_turnover_yuan)
        cache_key = f"v7:{device_id}:{normal_threshold_pct}:{strong_threshold_pct}:{min_turnover_key}"
        if not refresh:
            fallback_key = f"v7:default:{normal_threshold_pct}:{strong_threshold_pct}:{min_turnover_key}"
            cache_keys = [cache_key]
            if fallback_key != cache_key:
                cache_keys.append(fallback_key)
            for candidate_key in cache_keys:
                cached_scan = self.cache.get("lof_opportunity_scan", candidate_key, include_expired=True)
                if cached_scan:
                    cached_response = LofOpportunityResponse.model_validate(cached_scan)
                    return cached_response.model_copy(
                        update={
                            "items": cached_response.items[:limit],
                            "watchlist_count": len(self.list_watchlist(device_id)),
                        }
                    )
            # Cache misses fall through to a live scan so cache-version bumps do not show an empty monitor pool.
        watchlist = self.list_watchlist(device_id)
        scanned_at = datetime.now(UTC)
        errors: list[str] = []
        base_codes = list(dict.fromkeys([item.code for item in CORE_CROSS_BORDER_LOFS] + [item.code for item in watchlist]))
        discovery_quote_map = await self._get_discovery_quotes(errors)
        discovered_codes = await self._discover_lof_codes(
            quote_map=discovery_quote_map,
            base_codes=base_codes,
            errors=errors,
        )
        codes = list(dict.fromkeys(base_codes + discovered_codes))
        deep_profile_limit = self._deep_profile_limit(discovery_quote_map)
        deep_codes = list(dict.fromkeys(base_codes + discovered_codes[:deep_profile_limit]))
        quote_map = await self._get_market_quotes(codes, errors, preloaded=discovery_quote_map)
        profile_results = await asyncio.gather(*(self._get_profile(code) for code in deep_codes), return_exceptions=True)
        profile_result_map = dict(zip(deep_codes, profile_results, strict=False))
        profile_map = {
            code: profile
            for code, profile in profile_result_map.items()
            if isinstance(profile, FundProfile)
        }
        status_results = await asyncio.gather(
            *(
                self._get_status(code, profile if isinstance(profile, FundProfile) else None)
                for code, profile in profile_result_map.items()
            ),
            return_exceptions=True,
        )
        status_result_map = dict(zip(deep_codes, status_results, strict=False))
        haoetf_map = await self._get_haoetf_snapshots(deep_codes, errors)
        proxy_map = await self._get_proxy_changes(deep_codes, errors, profiles=profile_map)
        cooldown_keys = self.notice_cooldown_reader() if self.notice_cooldown_reader else set()
        signal_history = self.notice_signal_history_reader() if self.notice_signal_history_reader else {}
        items: list[LofPremiumItem] = []
        for code in codes:
            profile_result = profile_result_map.get(code)
            status_result = status_result_map.get(code)
            if profile_result is None:
                quote = quote_map.get(code)
                if quote is not None and self._is_displayable_lof(code, quote=quote):
                    items.append(
                        self._build_quote_only_item(
                            quote=quote,
                            min_turnover_yuan=min_turnover_yuan,
                            now=scanned_at,
                        )
                    )
                continue
            if isinstance(profile_result, Exception):
                errors.append(f"{code}: {profile_result}")
                quote = quote_map.get(code)
                if self._is_displayable_lof(code, quote=quote):
                    items.append(
                        self._build_unavailable_item(
                            code=code,
                            quote=quote,
                            error=str(profile_result),
                            normal_threshold_pct=normal_threshold_pct,
                            strong_threshold_pct=strong_threshold_pct,
                            min_turnover_yuan=min_turnover_yuan,
                            now=scanned_at,
                        )
                )
                continue
            profile = profile_result
            quote = quote_map.get(code)
            if not self._is_displayable_lof(code, profile=profile, quote=quote):
                continue
            status = status_result if isinstance(status_result, LofTradingStatus) else LofTradingStatus(warning=str(status_result))
            item = self._build_item(
                code=code,
                profile=profile,
                quote=quote,
                status=status,
                haoetf_snapshot=haoetf_map.get(code),
                proxy_changes=proxy_map,
                normal_threshold_pct=normal_threshold_pct,
                strong_threshold_pct=strong_threshold_pct,
                min_turnover_yuan=min_turnover_yuan,
                cooldown_keys=cooldown_keys,
                signal_history=signal_history,
                now=scanned_at,
            )
            items.append(item)
        items.sort(key=self._sort_key)
        response = LofOpportunityResponse(
            scanned_at=scanned_at,
            normal_threshold_pct=normal_threshold_pct,
            strong_threshold_pct=strong_threshold_pct,
            min_turnover_yuan=min_turnover_yuan,
            core_count=len(CORE_CROSS_BORDER_LOFS),
            watchlist_count=len(watchlist),
            items=items,
            errors=errors[:20],
        )
        self.cache.set("lof_opportunity_scan", cache_key, response.model_dump(mode="json"), LOF_SCAN_TTL_SECONDS)
        return response.model_copy(update={"items": response.items[:limit]})

    def _build_quote_only_item(
        self,
        *,
        quote: LofMarketQuote,
        min_turnover_yuan: float,
        now: datetime,
    ) -> LofPremiumItem:
        risks: list[str] = ["基金资料待补充"]
        if quote.latest_price is None:
            risks.append("场内行情缺失")
        elif quote.turnover_yuan is None:
            risks.append("成交额未知")
        elif (quote.turnover_yuan or 0) < min_turnover_yuan:
            risks.append("成交额不足")
        return LofPremiumItem(
            code=quote.code,
            name=quote.name,
            exchange_price=quote.latest_price,
            exchange_change_pct=quote.change_pct,
            exchange_turnover_yuan=quote.turnover_yuan,
            exchange_turnover_rate_pct=quote.turnover_rate_pct,
            signal_basis="none",
            direction="unknown",
            level="none",
            is_opportunity=False,
            actionable=False,
            purchase_status="unknown",
            redemption_status="unknown",
            risks=list(dict.fromkeys(risks)),
            data_source=f"profile:pending, quote:{quote.source}",
            updated_at=now,
        )

    def _build_unavailable_item(
        self,
        *,
        code: str,
        quote: LofMarketQuote | None,
        error: str,
        normal_threshold_pct: float,
        strong_threshold_pct: float,
        min_turnover_yuan: float,
        now: datetime,
    ) -> LofPremiumItem:
        config = CORE_LOF_BY_CODE.get(code)
        risks = ["基金资料缺失"]
        if quote is None or quote.latest_price is None:
            risks.append("场内行情缺失")
        elif quote.turnover_yuan is None:
            risks.append("成交额未知")
        elif quote.turnover_yuan < min_turnover_yuan:
            risks.append("成交额不足")
        if config is not None:
            risks.append("QDII/跨市场时间差")
        return LofPremiumItem(
            code=code,
            name=quote.name if quote else f"核心LOF{code}",
            fund_type="QDII-LOF" if config else "LOF",
            theme=config.theme if config else None,
            is_qdii=config is not None,
            exchange_price=quote.latest_price if quote else None,
            exchange_change_pct=quote.change_pct if quote else None,
            exchange_turnover_yuan=quote.turnover_yuan if quote else None,
            exchange_turnover_rate_pct=quote.turnover_rate_pct if quote else None,
            direction="unknown",
            level="none",
            is_opportunity=False,
            actionable=False,
            purchase_status="unknown",
            redemption_status="unknown",
            risks=list(dict.fromkeys(risks)),
            proxy_moves=[
                LofProxyMove(
                    symbol=leg.symbol,
                    label=leg.label,
                    weight=leg.weight,
                    source="unknown",
                    warning="基金资料缺失，暂不估算",
                )
                for leg in (config.proxies if config else ())
            ],
            data_source=f"profile:missing, quote:{quote.source if quote else 'missing'}, error:{error[:80]}",
            updated_at=now,
        )

    async def get_item(self, code: str, *, device_id: str = "default") -> LofPremiumItem:
        profile = await self._get_profile(code)
        quote_map = await self._get_market_quotes([code], [])
        haoetf_map = await self._get_haoetf_snapshots([code], [])
        proxy_map = await self._get_proxy_changes([code], [], profiles={code: profile})
        status = await self._get_status(code, profile)
        now = datetime.now(UTC)
        return self._build_item(
            code=code,
            profile=profile,
            quote=quote_map.get(code),
            status=status,
            haoetf_snapshot=haoetf_map.get(code),
            proxy_changes=proxy_map,
            normal_threshold_pct=DEFAULT_NORMAL_THRESHOLD_PCT,
            strong_threshold_pct=DEFAULT_STRONG_THRESHOLD_PCT,
            min_turnover_yuan=DEFAULT_MIN_TURNOVER_YUAN,
            cooldown_keys=set(),
            signal_history=self.notice_signal_history_reader() if self.notice_signal_history_reader else {},
            now=now,
        )

    async def _get_profile(self, code: str) -> FundProfile:
        profile = await self.estimator.get_profile(code)
        latest_nav = await self._get_latest_nav(code)
        if latest_nav is None or latest_nav.nav_date <= profile.nav_date:
            return profile
        previous_nav = profile.last_nav
        actual_change_pct = None
        if previous_nav:
            actual_change_pct = (latest_nav.nav / previous_nav - 1) * 100
        return profile.model_copy(
            update={
                "nav_date": latest_nav.nav_date,
                "last_nav": latest_nav.nav,
                "previous_nav_date": profile.nav_date,
                "previous_nav": previous_nav,
                "actual_change_pct": actual_change_pct,
            }
        )

    async def _get_latest_nav(self, code: str) -> LatestFundNav | None:
        cached = self.cache.get("lof_latest_nav", code)
        if cached:
            try:
                return LatestFundNav(nav_date=date.fromisoformat(str(cached["nav_date"])), nav=float(cached["nav"]))
            except (KeyError, TypeError, ValueError):
                pass
        try:
            latest_nav = await self.latest_nav_source.get_latest_nav(code)
        except Exception:
            return None
        if latest_nav is not None:
            self.cache.set(
                "lof_latest_nav",
                code,
                {"nav_date": latest_nav.nav_date.isoformat(), "nav": latest_nav.nav},
                LOF_LATEST_NAV_TTL_SECONDS,
            )
        return latest_nav

    async def _get_status(self, code: str, profile: FundProfile | None) -> LofTradingStatus:
        if profile is None:
            return LofTradingStatus(warning="基金基础信息不可用")
        cached = self.cache.get("lof_trading_status_v2", code)
        if cached:
            return LofTradingStatus.model_validate(cached)
        status = await self.status_source.get_status(code, profile)
        self.cache.set("lof_trading_status_v2", code, status.model_dump(mode="json"), LOF_STATUS_TTL_SECONDS)
        return status

    async def _get_discovery_quotes(self, errors: list[str]) -> dict[str, LofMarketQuote]:
        cached = self.cache.get("lof_discovery_quotes", "all")
        if cached:
            return self._parse_discovery_quote_cache(cached)
        stale_cached = self.cache.get("lof_discovery_quotes", "all", include_expired=True)
        stale_quotes = self._parse_discovery_quote_cache(stale_cached) if stale_cached else {}
        if not hasattr(self.discovery_market_source, "get_all_quotes"):
            fetched = await self._get_search_based_discovery_quotes(errors)
            if fetched:
                return fetched
            if stale_quotes:
                errors.append("全市场 LOF 行情为空，已使用过期发现池缓存")
            return stale_quotes
        primary_error = None
        try:
            fetched = await self.discovery_market_source.get_all_quotes()
        except AppError as exc:
            primary_error = exc.message
            fetched = {}
        if not fetched:
            fetched = await self._get_search_based_discovery_quotes(errors)
        if not fetched and stale_quotes:
            errors.append("全市场 LOF 行情为空，已使用过期发现池缓存")
            return stale_quotes
        if not fetched and primary_error:
            errors.append(primary_error)
        if fetched:
            self.cache.set(
                "lof_discovery_quotes",
                "all",
                {"quotes": [quote.model_dump(mode="json") for quote in fetched.values()]},
                LOF_DISCOVERY_TTL_SECONDS,
            )
        return fetched

    @staticmethod
    def _deep_profile_limit(quote_map: dict[str, LofMarketQuote]) -> int:
        if not quote_map:
            return DISCOVERY_DEEP_PROFILE_MAX_CODES
        priced_quotes = [quote for quote in quote_map.values() if quote.latest_price is not None]
        if not priced_quotes:
            return DISCOVERY_DEEP_PROFILE_MAX_CODES
        if len(priced_quotes) <= DISCOVERY_PREOPEN_DEEP_PROFILE_MAX_CODES:
            return DISCOVERY_PREOPEN_DEEP_PROFILE_MAX_CODES
        turnover_count = sum(1 for quote in priced_quotes if quote.turnover_yuan is not None)
        if turnover_count / len(priced_quotes) < 0.25:
            return DISCOVERY_PREOPEN_DEEP_PROFILE_MAX_CODES
        return DISCOVERY_DEEP_PROFILE_MAX_CODES

    @staticmethod
    def _parse_discovery_quote_cache(payload: dict[str, Any]) -> dict[str, LofMarketQuote]:
        rows = payload.get("quotes") or []
        return {
            quote.code: quote
            for quote in (LofMarketQuote.model_validate(row) for row in rows)
            if quote.latest_price is not None
        }

    async def _get_search_based_discovery_quotes(self, errors: list[str]) -> dict[str, LofMarketQuote]:
        if not hasattr(self.estimator, "list_funds"):
            return {}
        try:
            funds = await self.estimator.list_funds()
        except AppError as exc:
            errors.append(exc.message)
            return {}
        candidate_codes = [
            item.code
            for item in funds
            if looks_like_lof_fund(item.code, item.name, item.fund_type)
        ]
        if not candidate_codes:
            return {}
        try:
            return await self.market_source.get_quotes(list(dict.fromkeys(candidate_codes)))
        except AppError as exc:
            errors.append(f"LOF 搜索池行情发现失败：{exc.message}")
            return {}

    async def _discover_lof_codes(
        self,
        *,
        quote_map: dict[str, LofMarketQuote],
        base_codes: list[str],
        errors: list[str],
    ) -> list[str]:
        if not quote_map:
            return []
        cache_key = "v2"
        cached = self.cache.get("lof_discovered_lof_codes", cache_key)
        if cached and isinstance(cached.get("codes"), list):
            return [str(code) for code in cached["codes"]]

        base_set = set(base_codes)
        candidates = [
            quote
            for quote in quote_map.values()
            if quote.code not in base_set
            and quote.latest_price is not None
            and quote.latest_price > 0
            and (looks_like_lof_code(quote.code) or looks_like_lof_name(quote.name))
        ]
        candidates.sort(key=lambda quote: (-(quote.turnover_yuan or 0), quote.code))

        discovered: list[tuple[str, float]] = []
        for quote in candidates:
            discovered.append((quote.code, quote.turnover_yuan or 0))
        discovered.sort(key=lambda row: (-row[1], row[0]))
        codes = [code for code, _ in discovered[:DISCOVERY_MAX_CODES]]
        if codes:
            self.cache.set(
                "lof_discovered_lof_codes",
                cache_key,
                {"codes": codes},
                LOF_DISCOVERY_TTL_SECONDS,
            )
        if not codes and candidates:
            errors.append("全市场发现层未发现超过阈值的新增 LOF 溢价候选")
        return codes

    @staticmethod
    def _is_displayable_lof(
        code: str,
        *,
        profile: FundProfile | None = None,
        quote: LofMarketQuote | None = None,
    ) -> bool:
        if code in CORE_LOF_BY_CODE:
            return True
        if profile is not None:
            return looks_like_lof_fund(profile.code, profile.name, profile.fund_type)
        if quote is not None and looks_like_lof_name(quote.name):
            return True
        return False

    async def _get_market_quotes(
        self,
        codes: list[str],
        errors: list[str],
        *,
        preloaded: dict[str, LofMarketQuote] | None = None,
    ) -> dict[str, LofMarketQuote]:
        cached: dict[str, LofMarketQuote] = {}
        stale: dict[str, LofMarketQuote] = {}
        missing: list[str] = []
        for code in codes:
            if preloaded and code in preloaded:
                cached[code] = preloaded[code]
                self.cache.set("lof_market_quote", code, preloaded[code].model_dump(mode="json"), LOF_QUOTE_TTL_SECONDS)
                continue
            payload = self.cache.get("lof_market_quote", code)
            if payload:
                cached[code] = LofMarketQuote.model_validate(payload)
            else:
                missing.append(code)
                stale_payload = self.cache.get("lof_market_quote", code, include_expired=True)
                if stale_payload:
                    stale[code] = LofMarketQuote.model_validate(stale_payload)
        fetched: dict[str, LofMarketQuote] = {}
        if missing:
            try:
                fetched = await self.market_source.get_quotes(missing)
            except AppError as exc:
                if stale:
                    errors.append(f"{exc.message}，已使用缓存")
                elif not preloaded:
                    errors.append(exc.message)
            for code, quote in fetched.items():
                self.cache.set("lof_market_quote", code, quote.model_dump(mode="json"), LOF_QUOTE_TTL_SECONDS)
        return {**stale, **cached, **fetched}

    async def _get_proxy_changes(
        self,
        codes: list[str],
        errors: list[str],
        *,
        profiles: dict[str, FundProfile],
    ) -> dict[tuple[str, str], float]:
        symbols_by_period: dict[str, set[str]] = {}
        for code in codes:
            config = CORE_LOF_BY_CODE.get(code, CoreLof(code, "", ()))
            if not config.proxies:
                continue
            period_key = self._reference_period_start(profiles.get(code)) or "latest"
            symbols_by_period.setdefault(period_key, set()).update(leg.symbol for leg in config.proxies)
        if not symbols_by_period:
            return {}
        cached: dict[tuple[str, str], float] = {}
        stale: dict[tuple[str, str], float] = {}
        missing_by_period: dict[str, list[str]] = {}
        for period_key, symbols in symbols_by_period.items():
            for symbol in sorted(symbols):
                cache_key = f"{symbol}:{period_key}"
                payload = self.cache.get("lof_proxy_change", cache_key)
                result_key = (symbol, period_key)
                if payload and payload.get("change_pct") is not None:
                    cached[result_key] = float(payload["change_pct"])
                    continue
                missing_by_period.setdefault(period_key, []).append(symbol)
                stale_payload = self.cache.get("lof_proxy_change", cache_key, include_expired=True)
                if stale_payload and stale_payload.get("change_pct") is not None:
                    stale[result_key] = float(stale_payload["change_pct"])
        fetched: dict[tuple[str, str], float] = {}
        for period_key, missing in missing_by_period.items():
            if not missing:
                continue
            base_date = None if period_key == "latest" else date.fromisoformat(period_key)
            try:
                try:
                    period_changes = await self.proxy_source.get_changes(missing, base_date=base_date)
                except TypeError:
                    period_changes = await self.proxy_source.get_changes(missing)
            except AppError:
                if not stale:
                    message = "部分海外参考标的行情暂不可用，相关基金已标记风险"
                    if message not in errors:
                        errors.append(message)
                continue
            for symbol, change_pct in period_changes.items():
                cache_key = f"{symbol}:{period_key}"
                fetched[(symbol, period_key)] = change_pct
                self.cache.set("lof_proxy_change", cache_key, {"change_pct": change_pct}, PROXY_QUOTE_TTL_SECONDS)
        return {**stale, **cached, **fetched}

    @staticmethod
    def _reference_period_start(profile: FundProfile | None) -> str | None:
        if profile is None or profile.nav_date is None:
            return None
        return profile.nav_date.isoformat()

    async def _get_haoetf_snapshots(self, codes: list[str], errors: list[str]) -> dict[str, HaoEtfSnapshot]:
        wanted = [code for code in codes if code in CORE_LOF_BY_CODE]
        cached: dict[str, HaoEtfSnapshot] = {}
        stale: dict[str, HaoEtfSnapshot] = {}
        missing: list[str] = []
        for code in wanted:
            payload = self.cache.get("lof_haoetf_snapshot", code)
            if payload:
                data = {key: value for key, value in payload.items() if key != "_cache"}
                data.setdefault("exchange_price", None)
                data.setdefault("exchange_change_pct", None)
                cached[code] = HaoEtfSnapshot(**data)
            else:
                missing.append(code)
                stale_payload = self.cache.get("lof_haoetf_snapshot", code, include_expired=True)
                if stale_payload:
                    data = {key: value for key, value in stale_payload.items() if key != "_cache"}
                    data.setdefault("exchange_price", None)
                    data.setdefault("exchange_change_pct", None)
                    stale[code] = HaoEtfSnapshot(**data)
        fetched: dict[str, HaoEtfSnapshot] = {}
        if missing:
            try:
                fetched = await self.haoetf_source.get_snapshots(missing)
            except Exception as exc:
                suffix = "，已使用缓存" if stale else ""
                errors.append(f"HaoETF 估算源不可用：{exc}{suffix}")
            for code, snapshot in fetched.items():
                self.cache.set("lof_haoetf_snapshot", code, snapshot.__dict__, LOF_QUOTE_TTL_SECONDS)
        return {**stale, **cached, **fetched}

    def _build_item(
        self,
        *,
        code: str,
        profile: FundProfile,
        quote: LofMarketQuote | None,
        status: LofTradingStatus,
        haoetf_snapshot: HaoEtfSnapshot | None,
        proxy_changes: dict[tuple[str, str], float],
        normal_threshold_pct: float,
        strong_threshold_pct: float,
        min_turnover_yuan: float,
        cooldown_keys: set[str],
        signal_history: dict[str, Any] | None,
        now: datetime,
    ) -> LofPremiumItem:
        config = CORE_LOF_BY_CODE.get(code)
        proxy_moves: list[LofProxyMove] = []
        weighted_proxy_change = 0.0
        used_weight = 0.0
        reference_period_start = self._reference_period_start(profile)
        reference_period_key = reference_period_start or "latest"
        reference_period_end = now.date().isoformat()
        reference_basis = "official_nav_period" if reference_period_start else "latest_daily"
        if config is not None:
            for leg in config.proxies:
                change_pct = proxy_changes.get((leg.symbol, reference_period_key))
                proxy_moves.append(
                    LofProxyMove(
                        symbol=leg.symbol,
                        label=leg.label,
                        weight=leg.weight,
                        change_pct=_safe_round(change_pct),
                        period_start=reference_period_start,
                        period_end=reference_period_end,
                        change_basis=reference_basis,
                        source="yfinance" if change_pct is not None else "unknown",
                        warning=None if change_pct is not None else "代理行情缺失",
                    )
                )
                if change_pct is not None:
                    weighted_proxy_change += leg.weight * change_pct
                    used_weight += leg.weight
        reference_change = weighted_proxy_change / max(used_weight, 1e-9) if used_weight > 0 else None
        estimated_nav = None
        if profile.last_nav and used_weight > 0:
            estimated_nav = profile.last_nav * (1 + reference_change / 100)
        if haoetf_snapshot and haoetf_snapshot.estimated_nav is not None:
            estimated_nav = haoetf_snapshot.estimated_nav
            if profile.last_nav:
                reference_change = (estimated_nav / profile.last_nav - 1) * 100
                reference_basis = "haoetf_estimated_nav"
            if haoetf_snapshot.proxy_symbol:
                hao_move = (
                    LofProxyMove(
                        symbol=haoetf_snapshot.proxy_symbol,
                        label="HaoETF 相关期货",
                        weight=1.0,
                        change_pct=_safe_round(haoetf_snapshot.proxy_change_pct),
                        period_start=reference_period_start,
                        period_end=reference_period_end,
                        change_basis="haoetf_related_future",
                        source="haoetf",
                    )
                )
                proxy_moves = [hao_move, *[move for move in proxy_moves if move.change_pct is not None]]
        if status.purchase_status == "unknown" and haoetf_snapshot and haoetf_snapshot.purchase_status:
            status = status.model_copy(update={"purchase_status": haoetf_snapshot.purchase_status, "source": f"{status.source}+haoetf"})
        exchange_price = quote.latest_price if quote else (haoetf_snapshot.exchange_price if haoetf_snapshot else None)
        estimated_premium = self._premium_pct(exchange_price, estimated_nav)
        if haoetf_snapshot and haoetf_snapshot.estimated_premium_pct is not None:
            estimated_premium = haoetf_snapshot.estimated_premium_pct
        official_premium = self._premium_pct(exchange_price, profile.last_nav)
        signal_basis = "estimated" if estimated_premium is not None else "official" if official_premium is not None else "none"
        signal_value = estimated_premium if estimated_premium is not None else official_premium
        direction, level, is_opportunity = _classify_signal(
            signal_value,
            normal_threshold_pct=normal_threshold_pct,
            strong_threshold_pct=strong_threshold_pct,
        )
        if _is_trade_leg_paused(direction, status):
            level = "none"
            is_opportunity = False
        is_qdii = self._is_qdii_item(code=code, profile=profile, config=config)
        needs_cross_day_confirmation = bool(
            is_opportunity
            and self._needs_cross_day_confirmation(
                code=code,
                signal_value=signal_value,
                signal_basis=signal_basis,
                direction=direction,
                is_qdii=is_qdii,
                signal_history=signal_history or {},
                now=now,
            )
        )
        needs_domestic_turnover_confirmation = bool(
            is_opportunity
            and self._needs_domestic_turnover_confirmation(
                signal_basis=signal_basis,
                direction=direction,
                is_qdii=is_qdii,
                turnover_rate_pct=quote.turnover_rate_pct if quote else None,
            )
        )
        risks = self._risks(
            code=code,
            profile=profile,
            quote=quote,
            exchange_price=exchange_price,
            status=status,
            proxy_moves=proxy_moves,
            direction=direction,
            level=level,
            min_turnover_yuan=min_turnover_yuan,
            cooldown_keys=cooldown_keys,
            needs_cross_day_confirmation=needs_cross_day_confirmation,
            needs_domestic_turnover_confirmation=needs_domestic_turnover_confirmation,
            is_high_single_day_signal=self._is_high_single_day_signal(signal_value),
        )
        actionable = bool(
            is_opportunity
            and quote is not None
            and quote.latest_price is not None
            and (quote.turnover_yuan or 0) >= min_turnover_yuan
            and "代理行情缺失" not in risks
            and not needs_cross_day_confirmation
            and not needs_domestic_turnover_confirmation
            and not (direction == "premium" and status.purchase_status == "暂停")
            and "通知冷却中" not in risks
        )
        return LofPremiumItem(
            code=profile.code,
            name=profile.name,
            fund_type=profile.fund_type,
            theme=config.theme if config else None,
            is_qdii=is_qdii,
            official_nav=profile.last_nav,
            official_nav_date=profile.nav_date.isoformat() if profile.nav_date else None,
            estimated_nav=_safe_round(estimated_nav, 4),
            estimated_nav_time=now if estimated_nav is not None else None,
            exchange_price=exchange_price,
            exchange_change_pct=quote.change_pct if quote else (haoetf_snapshot.exchange_change_pct if haoetf_snapshot else None),
            exchange_turnover_yuan=quote.turnover_yuan if quote else None,
            exchange_turnover_rate_pct=quote.turnover_rate_pct if quote else None,
            reference_change_pct=_safe_round(reference_change),
            reference_period_start=reference_period_start,
            reference_period_end=reference_period_end,
            reference_basis=reference_basis,
            estimated_premium_pct=_safe_round(estimated_premium),
            official_premium_pct=_safe_round(official_premium),
            signal_basis=signal_basis,
            direction=direction,  # type: ignore[arg-type]
            level=level,  # type: ignore[arg-type]
            is_opportunity=is_opportunity,
            actionable=actionable,
            purchase_status=status.purchase_status,
            redemption_status=status.redemption_status,
            daily_purchase_limit_yuan=status.daily_purchase_limit_yuan,
            fee_rate_pct=status.fee_rate_pct,
            risks=risks,
            proxy_moves=proxy_moves,
            data_source=f"profile:{profile.source}, quote:{quote.source if quote else 'missing'}, status:{status.source}",
            updated_at=now,
        )

    @staticmethod
    def _is_qdii_item(*, code: str, profile: FundProfile, config: CoreLof | None) -> bool:
        if config is not None:
            return True
        text = f"{profile.name} {profile.fund_type or ''}".upper()
        return "QDII" in text

    @staticmethod
    def _needs_cross_day_confirmation(
        *,
        code: str,
        signal_value: float | None,
        signal_basis: str,
        direction: str,
        is_qdii: bool,
        signal_history: dict[str, Any],
        now: datetime,
    ) -> bool:
        if signal_basis != "official" or is_qdii:
            return False
        if LofMonitorService._is_shanghai_lof_discount(code=code, direction=direction):
            return False
        if LofMonitorService._is_high_single_day_signal(signal_value):
            return False
        return not LofMonitorService._has_prior_same_direction_signal(
            code=code,
            direction=direction,
            signal_history=signal_history,
            now=now,
        )

    @staticmethod
    def _is_shanghai_lof_discount(*, code: str, direction: str) -> bool:
        return direction == "discount" and code.startswith("5")

    @staticmethod
    def _is_high_single_day_signal(signal_value: float | None) -> bool:
        return signal_value is not None and abs(signal_value) >= DEFAULT_HIGH_SINGLE_DAY_SIGNAL_PCT

    @staticmethod
    def _has_prior_same_direction_signal(
        *,
        code: str,
        direction: str,
        signal_history: dict[str, Any],
        now: datetime,
    ) -> bool:
        local_date = now.astimezone(MARKET_TZ).date()
        for day_key, day_payload in signal_history.items():
            try:
                day = date.fromisoformat(str(day_key))
            except ValueError:
                continue
            if day >= local_date:
                continue
            day_items = day_payload.get("items") if isinstance(day_payload, dict) else {}
            prior = day_items.get(code) if isinstance(day_items, dict) else None
            if not isinstance(prior, dict):
                continue
            if prior.get("direction") == direction:
                return True
        return False

    @staticmethod
    def _needs_domestic_turnover_confirmation(
        *,
        signal_basis: str,
        direction: str,
        is_qdii: bool,
        turnover_rate_pct: float | None,
    ) -> bool:
        if signal_basis != "official" or is_qdii:
            return False
        return turnover_rate_pct is None or turnover_rate_pct < DEFAULT_MIN_DOMESTIC_TURNOVER_RATE_PCT

    @staticmethod
    def _premium_pct(price: float | None, nav: float | None) -> float | None:
        if price is None or nav is None or nav <= 0:
            return None
        return (price / nav - 1) * 100

    @staticmethod
    def _cooldown_key(code: str, direction: str, level: str) -> str:
        return f"{code}:{direction}:{level}"

    def _risks(
        self,
        *,
        code: str,
        profile: FundProfile,
        quote: LofMarketQuote | None,
        exchange_price: float | None,
        status: LofTradingStatus,
        proxy_moves: list[LofProxyMove],
        direction: str,
        level: str,
        min_turnover_yuan: float,
        cooldown_keys: set[str],
        needs_cross_day_confirmation: bool = False,
        needs_domestic_turnover_confirmation: bool = False,
        is_high_single_day_signal: bool = False,
    ) -> list[str]:
        risks: list[str] = []
        if exchange_price is None:
            risks.append("场内行情缺失")
        elif quote is None or quote.turnover_yuan is None:
            risks.append("成交额未知")
        elif (quote.turnover_yuan or 0) < min_turnover_yuan:
            risks.append("成交额不足")
        if proxy_moves and not any(move.change_pct is not None for move in proxy_moves):
            risks.append("代理行情缺失")
        if profile.stale:
            risks.append("净值过期缓存")
        if "QDII" in (profile.fund_type or "").upper() or CORE_LOF_BY_CODE.get(code) is not None:
            risks.append("QDII/跨市场时间差")
        if direction == "premium" and status.purchase_status in {"暂停", "限制大额"}:
            risks.append(f"申购{status.purchase_status}")
        if status.redemption_status == "暂停":
            risks.append("赎回暂停")
        if self._is_shanghai_lof_discount(code=code, direction=direction):
            risks.append("沪市LOF折价T日可赎回，需确认当日估算净值")
        elif is_high_single_day_signal and direction in {"premium", "discount"}:
            risks.append("单日高折溢价信号，需确认当日估算净值")
        if needs_domestic_turnover_confirmation:
            if quote is None or quote.turnover_rate_pct is None:
                risks.append("换手率未知")
            else:
                risks.append(f"换手率不足{DEFAULT_MIN_DOMESTIC_TURNOVER_RATE_PCT:.0f}%")
        if needs_cross_day_confirmation:
            if direction == "discount":
                risks.append("非QDII官方折价候选，等待跨日确认")
            elif direction == "premium":
                risks.append("非QDII官方溢价候选，等待跨日确认")
            else:
                risks.append("非QDII官方信号，等待跨日确认")
        if status.warning:
            risks.append(status.warning)
        if level != "none" and self._cooldown_key(code, direction, level) in cooldown_keys:
            risks.append("通知冷却中")
        return list(dict.fromkeys(risks))

    @staticmethod
    def _sort_key(item: LofPremiumItem) -> tuple[int, float, float]:
        premium = item.estimated_premium_pct
        if premium is None:
            premium = item.official_premium_pct
        return (
            0 if item.is_opportunity else 1,
            -abs(premium or 0),
            -(item.exchange_turnover_yuan or 0),
        )
