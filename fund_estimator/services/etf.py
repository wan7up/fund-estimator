from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from fund_estimator.data_sources.eastmoney import DEFAULT_HEADERS, infer_market
from fund_estimator.models.etf import EtfMarketQuote, EtfOpportunityResponse, EtfPremiumItem
from fund_estimator.models.lof import LofProxyMove, LofTradingStatus
from fund_estimator.models.schema import FundProfile
from fund_estimator.services.cache import SQLiteCache
from fund_estimator.services.etf_config import CORE_CROSS_BORDER_ETFS, CORE_ETF_BY_CODE, looks_like_cross_border_etf
from fund_estimator.services.exceptions import AppError, DataSourceError
from fund_estimator.services.lof import (
    DEFAULT_MIN_TURNOVER_YUAN,
    DEFAULT_NORMAL_THRESHOLD_PCT,
    DEFAULT_STRONG_THRESHOLD_PCT,
    LOF_QUOTE_TTL_SECONDS,
    PROXY_QUOTE_TTL_SECONDS,
    LOF_SCAN_TTL_SECONDS,
    LOF_STATUS_TTL_SECONDS,
    EastmoneyLofTradingStatusDataSource,
    YahooProxyDataSource,
    _classify_signal,
    _is_trade_leg_paused,
    _parse_float,
    _safe_round,
)
from fund_estimator.services.lof_config import ProxyLeg


ETF_PROFILE_CONCURRENCY = 20


class EastmoneyEtfMarketDataSource:
    def __init__(self, timeout: float = 6.0) -> None:
        self.timeout = timeout

    async def get_all_quotes(self) -> dict[str, EtfMarketQuote]:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1",
            "pz": "200",
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f12",
            "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827",
            "fields": "f12,f13,f14,f2,f3,f18,f6,f441,f124",
            "_": str(int(time.time() * 1000)),
        }
        headers = {**DEFAULT_HEADERS, "Referer": "https://quote.eastmoney.com/center/gridlist.html#fund_etf"}
        quotes: dict[str, EtfMarketQuote] = {}
        quote_time = datetime.now(UTC)
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, trust_env=False) as client:
            total = None
            page = 1
            while total is None or len(quotes) < total:
                params["pn"] = str(page)
                try:
                    response = await client.get(url, params=params)
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise DataSourceError(
                        "ETF_QUOTE_FETCH_FAILED",
                        "ETF 实时行情/IOPV 获取失败",
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
    def _quote_from_row(row: Any, *, quote_time: datetime) -> EtfMarketQuote | None:
        code = str(row.get("f12") or "")
        if not code:
            return None
        market = infer_market(code)
        market_id = str(row.get("f13") or "")
        if market_id == "1":
            market = "SH"
        elif market_id == "0":
            market = "SZ"
        timestamp = _parse_float(row.get("f124"))
        if timestamp:
            try:
                quote_time = datetime.fromtimestamp(timestamp, tz=UTC)
            except (OSError, ValueError):
                pass
        return EtfMarketQuote(
            code=code,
            name=str(row.get("f14") or code),
            latest_price=_parse_float(row.get("f2")),
            previous_close=_parse_float(row.get("f18")),
            change_pct=_parse_float(row.get("f3")),
            turnover_yuan=_parse_float(row.get("f6")),
            iopv=_parse_float(row.get("f441")),
            quote_time=quote_time,
            market=market if market in {"SH", "SZ"} else "UNKNOWN",
            source="eastmoney_etf_list",
        )


class EtfMonitorService:
    def __init__(
        self,
        *,
        estimator: Any,
        cache: SQLiteCache,
        market_source: Any | None = None,
        status_source: Any | None = None,
        proxy_source: Any | None = None,
    ) -> None:
        self.estimator = estimator
        self.cache = cache
        self.market_source = market_source or EastmoneyEtfMarketDataSource()
        self.status_source = status_source or EastmoneyLofTradingStatusDataSource()
        self.proxy_source = proxy_source or YahooProxyDataSource()

    async def get_opportunities(
        self,
        *,
        normal_threshold_pct: float = DEFAULT_NORMAL_THRESHOLD_PCT,
        strong_threshold_pct: float = DEFAULT_STRONG_THRESHOLD_PCT,
        min_turnover_yuan: float = DEFAULT_MIN_TURNOVER_YUAN,
        limit: int = 80,
        refresh: bool = True,
    ) -> EtfOpportunityResponse:
        cache_key = f"v2:{normal_threshold_pct}:{strong_threshold_pct}:{min_turnover_yuan}"
        if not refresh:
            cached = self.cache.get("etf_opportunity_scan", cache_key, include_expired=True)
            if cached:
                response = EtfOpportunityResponse.model_validate(cached)
                return response.model_copy(update={"items": response.items[:limit]})
            return EtfOpportunityResponse(
                scanned_at=datetime.now(UTC),
                normal_threshold_pct=normal_threshold_pct,
                strong_threshold_pct=strong_threshold_pct,
                min_turnover_yuan=min_turnover_yuan,
                core_count=len(CORE_CROSS_BORDER_ETFS),
                candidate_count=0,
                items=[],
                errors=["后台 ETF 扫描尚未完成，请稍后刷新"],
            )

        scanned_at = datetime.now(UTC)
        errors: list[str] = []
        quote_map = await self._get_quotes(errors)
        candidates = [
            quote
            for quote in quote_map.values()
            if looks_like_cross_border_etf(quote.code, quote.name)
            and quote.latest_price is not None
            and quote.latest_price > 0
        ]
        candidates.sort(key=lambda quote: (-(quote.turnover_yuan or 0), quote.code))
        profiles = await self._get_profiles([quote.code for quote in candidates])
        statuses = await self._get_statuses(profiles)
        proxy_changes = await self._get_proxy_changes([quote.code for quote in candidates], errors)
        items = [
            self._build_item(
                quote=quote,
                profile=profiles.get(quote.code),
                status=statuses.get(quote.code, LofTradingStatus()),
                proxy_changes=proxy_changes,
                normal_threshold_pct=normal_threshold_pct,
                strong_threshold_pct=strong_threshold_pct,
                min_turnover_yuan=min_turnover_yuan,
                now=scanned_at,
            )
            for quote in candidates
        ]
        items.sort(key=self._sort_key)
        response = EtfOpportunityResponse(
            scanned_at=scanned_at,
            normal_threshold_pct=normal_threshold_pct,
            strong_threshold_pct=strong_threshold_pct,
            min_turnover_yuan=min_turnover_yuan,
            core_count=len(CORE_CROSS_BORDER_ETFS),
            candidate_count=len(candidates),
            items=items,
            errors=errors[:20],
        )
        self.cache.set("etf_opportunity_scan", cache_key, response.model_dump(mode="json"), LOF_SCAN_TTL_SECONDS)
        return response.model_copy(update={"items": response.items[:limit]})

    async def _get_quotes(self, errors: list[str]) -> dict[str, EtfMarketQuote]:
        cached = self.cache.get("etf_market_quotes", "all")
        if cached:
            return {
                quote.code: quote
                for quote in (EtfMarketQuote.model_validate(row) for row in cached.get("quotes", []))
                if quote.latest_price is not None
            }
        try:
            fetched = await self.market_source.get_all_quotes()
        except AppError as exc:
            errors.append(exc.message)
            return {}
        if fetched:
            self.cache.set(
                "etf_market_quotes",
                "all",
                {"quotes": [quote.model_dump(mode="json") for quote in fetched.values()]},
                LOF_QUOTE_TTL_SECONDS,
            )
        return fetched

    async def _get_profiles(self, codes: list[str]) -> dict[str, FundProfile]:
        semaphore = asyncio.Semaphore(ETF_PROFILE_CONCURRENCY)

        async def fetch(code: str) -> tuple[str, FundProfile | None]:
            async with semaphore:
                cached = self.cache.get("etf_profile", code)
                if cached:
                    return code, FundProfile.model_validate(cached)
                try:
                    profile = await self.estimator.get_profile(code)
                except Exception:
                    return code, None
                self.cache.set("etf_profile", code, profile.model_dump(mode="json"), 10 * 60)
                return code, profile

        rows = await asyncio.gather(*(fetch(code) for code in codes), return_exceptions=True)
        return {
            code: profile
            for row in rows
            if not isinstance(row, Exception)
            for code, profile in [row]
            if profile is not None
        }

    async def _get_statuses(self, profiles: dict[str, FundProfile]) -> dict[str, LofTradingStatus]:
        async def fetch(code: str, profile: FundProfile) -> tuple[str, LofTradingStatus]:
            cached = self.cache.get("etf_trading_status_v2", code)
            if cached:
                return code, LofTradingStatus.model_validate(cached)
            try:
                status = await self.status_source.get_status(code, profile)
            except Exception as exc:
                status = LofTradingStatus(warning=f"申赎状态不可用：{exc}")
            self.cache.set("etf_trading_status_v2", code, status.model_dump(mode="json"), LOF_STATUS_TTL_SECONDS)
            return code, status

        rows = await asyncio.gather(*(fetch(code, profile) for code, profile in profiles.items()), return_exceptions=True)
        return {
            code: status
            for row in rows
            if not isinstance(row, Exception)
            for code, status in [row]
        }

    async def _get_proxy_changes(self, codes: list[str], errors: list[str]) -> dict[str, float]:
        symbols = [
            leg.symbol
            for code in codes
            for leg in CORE_ETF_BY_CODE.get(code, CoreEtfFallback()).proxies
        ]
        if not symbols:
            return {}
        cached: dict[str, float] = {}
        missing: list[str] = []
        for symbol in dict.fromkeys(symbols):
            payload = self.cache.get("etf_proxy_change", symbol)
            if payload and payload.get("change_pct") is not None:
                cached[symbol] = float(payload["change_pct"])
            else:
                missing.append(symbol)
        fetched: dict[str, float] = {}
        if missing:
            try:
                fetched = await self.proxy_source.get_changes(missing)
            except AppError:
                fetched = {}
            for symbol, change_pct in fetched.items():
                self.cache.set("etf_proxy_change", symbol, {"change_pct": change_pct}, PROXY_QUOTE_TTL_SECONDS)
        return {**cached, **fetched}

    def _build_item(
        self,
        *,
        quote: EtfMarketQuote,
        profile: FundProfile | None,
        status: LofTradingStatus,
        proxy_changes: dict[str, float],
        normal_threshold_pct: float,
        strong_threshold_pct: float,
        min_turnover_yuan: float,
        now: datetime,
    ) -> EtfPremiumItem:
        config = CORE_ETF_BY_CODE.get(quote.code)
        proxy_moves = self._proxy_moves(config.proxies if config else (), proxy_changes, now=now)
        reference_change = self._weighted_reference_change(proxy_moves)
        official_nav = profile.last_nav if profile else None
        official_nav_date = profile.nav_date.isoformat() if profile and profile.nav_date else None
        iopv_premium = self._premium_pct(quote.latest_price, quote.iopv)
        official_premium = self._premium_pct(quote.latest_price, official_nav)
        signal_basis = "iopv" if iopv_premium is not None else "official" if official_premium is not None else "none"
        signal_value = iopv_premium if iopv_premium is not None else official_premium
        direction, level, is_opportunity = _classify_signal(
            signal_value,
            normal_threshold_pct=normal_threshold_pct,
            strong_threshold_pct=strong_threshold_pct,
        )
        if level == "strong" and _is_trade_leg_paused(direction, status):
            level = "normal"
        risks = self._risks(
            quote=quote,
            profile=profile,
            status=status,
            proxy_moves=proxy_moves,
            direction=direction,
            min_turnover_yuan=min_turnover_yuan,
        )
        actionable = bool(
            is_opportunity
            and quote.latest_price is not None
            and (quote.turnover_yuan or 0) >= min_turnover_yuan
            and quote.iopv is not None
            and not (direction == "premium" and status.purchase_status == "暂停")
            and not (direction == "discount" and status.redemption_status == "暂停")
        )
        return EtfPremiumItem(
            code=quote.code,
            name=profile.name if profile else quote.name,
            theme=config.theme if config else None,
            exchange_price=quote.latest_price,
            exchange_change_pct=quote.change_pct,
            exchange_turnover_yuan=quote.turnover_yuan,
            iopv=quote.iopv,
            iopv_premium_pct=_safe_round(iopv_premium),
            official_nav=official_nav,
            official_nav_date=official_nav_date,
            official_premium_pct=_safe_round(official_premium),
            reference_change_pct=_safe_round(reference_change),
            reference_period_start=None,
            reference_period_end=now.date().isoformat(),
            reference_basis="auxiliary_latest_daily",
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
            data_source=f"quote:{quote.source}, profile:{profile.source if profile else 'missing'}, status:{status.source}",
            updated_at=now,
        )

    @staticmethod
    def _proxy_moves(legs: tuple[ProxyLeg, ...], proxy_changes: dict[str, float], *, now: datetime) -> list[LofProxyMove]:
        moves: list[LofProxyMove] = []
        for leg in legs:
            change_pct = proxy_changes.get(leg.symbol)
            moves.append(
                LofProxyMove(
                    symbol=leg.symbol,
                    label=leg.label,
                    weight=leg.weight,
                    change_pct=_safe_round(change_pct),
                    period_start=None,
                    period_end=now.date().isoformat(),
                    change_basis="latest_daily",
                    source="yfinance" if change_pct is not None else "unknown",
                    warning=None if change_pct is not None else "参考标的行情缺失",
                )
            )
        return moves

    @staticmethod
    def _weighted_reference_change(proxy_moves: list[LofProxyMove]) -> float | None:
        weighted = 0.0
        weight = 0.0
        for move in proxy_moves:
            if move.change_pct is None:
                continue
            weighted += move.weight * move.change_pct
            weight += move.weight
        if weight <= 0:
            return None
        return weighted / weight

    @staticmethod
    def _premium_pct(price: float | None, nav: float | None) -> float | None:
        if price is None or nav is None or nav <= 0:
            return None
        return (price / nav - 1) * 100

    @staticmethod
    def _risks(
        *,
        quote: EtfMarketQuote,
        profile: FundProfile | None,
        status: LofTradingStatus,
        proxy_moves: list[LofProxyMove],
        direction: str,
        min_turnover_yuan: float,
    ) -> list[str]:
        risks: list[str] = []
        if quote.iopv is None:
            risks.append("IOPV缺失")
        if quote.turnover_yuan is None:
            risks.append("成交额未知")
        elif quote.turnover_yuan < min_turnover_yuan:
            risks.append("成交额不足")
        if profile is None:
            risks.append("基金资料缺失")
        if profile and profile.stale:
            risks.append("净值过期缓存")
        risks.append("ETF申赎门槛/篮子成本")
        if quote.code in CORE_ETF_BY_CODE or looks_like_cross_border_etf(quote.code, quote.name):
            risks.append("跨境/跨市场时间差")
        if proxy_moves and not any(move.change_pct is not None for move in proxy_moves):
            risks.append("参考标的行情缺失")
        if status.purchase_status in {"暂停", "限制大额"}:
            risks.append(f"申购{status.purchase_status}")
        if status.redemption_status == "暂停":
            risks.append("赎回暂停")
        if direction == "premium" and status.purchase_status == "暂停":
            risks.append("溢价套利申购端受限")
        if direction == "discount" and status.redemption_status == "暂停":
            risks.append("折价套利赎回端受限")
        if status.warning:
            risks.append(status.warning)
        return list(dict.fromkeys(risks))

    @staticmethod
    def _sort_key(item: EtfPremiumItem) -> tuple[int, float, float]:
        premium = item.iopv_premium_pct
        if premium is None:
            premium = item.official_premium_pct
        return (
            0 if item.is_opportunity else 1,
            -abs(premium or 0),
            -(item.exchange_turnover_yuan or 0),
        )


class CoreEtfFallback:
    proxies: tuple[ProxyLeg, ...] = ()
