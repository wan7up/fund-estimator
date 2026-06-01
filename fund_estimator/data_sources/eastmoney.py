from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
import time
from datetime import date, datetime
from html import unescape
from typing import Any
from urllib.parse import urlencode

import httpx
from lxml import html

from fund_estimator.models.schema import (
    FundAssetAllocation,
    FundDetailInfo,
    FundHoldings,
    FundManagerInfo,
    FundProfile,
    FundSearchResult,
    FundSimilarRank,
    FundStageReturns,
    FundTradingInfo,
    HoldingItem,
    StockQuote,
)
from fund_estimator.services.exceptions import AppError, DataSourceError


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 fund-estimator/0.1",
    "Referer": "https://fund.eastmoney.com/",
}


def _decode_utf8_response(response: httpx.Response) -> str:
    return response.content.decode("utf-8", errors="replace")


def _is_notfound_redirect(response: httpx.Response) -> bool:
    location = response.headers.get("location", "")
    return response.status_code in {301, 302, 303, 307, 308} and "notfound" in location.lower()


def infer_market(stock_code: str) -> str:
    code = stock_code.strip().upper()
    if re.fullmatch(r"\d{6}", code):
        if code.startswith(("5", "6", "9")):
            return "SH"
        if code.startswith(("0", "1", "2", "3")):
            return "SZ"
        if code.startswith(("4", "8")):
            return "BJ"
    if re.fullmatch(r"\d{5}", code):
        return "HK"
    return "UNKNOWN"


def to_eastmoney_secid(stock_code: str) -> str | None:
    market = infer_market(stock_code)
    if market == "SH":
        return f"1.{stock_code}"
    if market in {"SZ", "BJ"}:
        return f"0.{stock_code}"
    return None


def _extract_js_var(text: str, var_name: str) -> str | None:
    match = re.search(rf"var\s+{re.escape(var_name)}\s*=\s*(.*?);", text, flags=re.S)
    return match.group(1).strip() if match else None


def _extract_json_var(text: str, var_name: str) -> Any:
    payload = _extract_js_var(text, var_name)
    if payload is None:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _extract_float_var(text: str, var_name: str) -> float | None:
    return _parse_float(_extract_json_var(text, var_name))


def _date_from_ms(value: Any) -> date | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000).date()
    except (TypeError, ValueError, OSError):
        return None


def parse_fund_code_search(text: str) -> list[FundSearchResult]:
    payload = _extract_js_var(text, "r")
    if not payload:
        return []
    rows = json.loads(payload)
    results: list[FundSearchResult] = []
    for row in rows:
        if len(row) < 4:
            continue
        results.append(
            FundSearchResult(
                code=str(row[0]),
                pinyin=str(row[1]) if row[1] else None,
                name=str(row[2]),
                fund_type=str(row[3]) if row[3] else None,
                source="eastmoney",
            )
        )
    return results


def parse_pingzhong_profile(code: str, text: str, fund_type: str | None = None) -> FundProfile:
    name = _extract_json_var(text, "fS_name")
    trend = _extract_json_var(text, "Data_netWorthTrend")
    ac_trend = _extract_json_var(text, "Data_ACWorthTrend")
    if not name or not trend:
        raise AppError("FUND_NOT_FOUND", f"基金代码不存在或净值数据不可用：{code}", status_code=404)

    if not trend:
        raise AppError("FUND_NOT_FOUND", f"基金代码不存在或净值数据不可用：{code}", status_code=404)

    latest = trend[-1]
    ts = int(latest["x"]) / 1000
    nav_date = datetime.fromtimestamp(ts).date()
    last_nav = float(latest["y"])
    previous_nav: float | None = None
    previous_nav_date: date | None = None
    actual_change_pct = _parse_float(latest.get("equityReturn"))
    if len(trend) >= 2:
        previous = trend[-2]
        previous_nav = float(previous["y"])
        previous_nav_date = datetime.fromtimestamp(int(previous["x"]) / 1000).date()

    accumulated_nav: float | None = None
    if ac_trend:
        accumulated_nav = float(ac_trend[-1][1])

    return FundProfile(
        code=code,
        name=name,
        fund_type=fund_type,
        nav_date=nav_date,
        last_nav=last_nav,
        previous_nav_date=previous_nav_date,
        previous_nav=previous_nav,
        actual_change_pct=actual_change_pct,
        accumulated_nav=accumulated_nav,
        details=parse_pingzhong_details(text),
        source="eastmoney",
    )


def parse_pingzhong_details(text: str) -> FundDetailInfo:
    scale_date, scale_billion = _parse_scale(text)
    return FundDetailInfo(
        stage_returns=FundStageReturns(
            one_month_pct=_extract_float_var(text, "syl_1y"),
            three_month_pct=_extract_float_var(text, "syl_3y"),
            six_month_pct=_extract_float_var(text, "syl_6y"),
            one_year_pct=_extract_float_var(text, "syl_1n"),
        ),
        asset_allocation=_parse_asset_allocation(text),
        trading=FundTradingInfo(
            source_rate_pct=_extract_float_var(text, "fund_sourceRate"),
            current_rate_pct=_extract_float_var(text, "fund_Rate"),
            min_purchase_amount=_extract_float_var(text, "fund_minsg"),
        ),
        managers=_parse_managers(text),
        similar_rank=_parse_similar_rank(text),
        scale_date=scale_date,
        scale_billion=scale_billion,
    )


def _parse_scale(text: str) -> tuple[date | None, float | None]:
    payload = _extract_json_var(text, "Data_fluctuationScale")
    if not isinstance(payload, dict):
        return None, None
    categories = payload.get("categories") or []
    series = payload.get("series") or []
    if not categories or not series:
        return None, None
    latest = series[-1] or {}
    try:
        scale_date = date.fromisoformat(str(categories[-1]))
    except ValueError:
        scale_date = None
    return scale_date, _parse_float(latest.get("y"))


def _parse_asset_allocation(text: str) -> FundAssetAllocation:
    payload = _extract_json_var(text, "Data_assetAllocation")
    if not isinstance(payload, dict):
        return FundAssetAllocation()
    categories = payload.get("categories") or []
    report_date: date | None = None
    if categories:
        try:
            report_date = date.fromisoformat(str(categories[-1]))
        except ValueError:
            report_date = None

    values: dict[str, float] = {}
    for series in payload.get("series") or []:
        name = str(series.get("name") or "")
        data = series.get("data") or []
        if data:
            parsed = _parse_float(data[-1])
            if parsed is not None:
                values[name] = parsed
    return FundAssetAllocation(
        report_date=report_date,
        stock_pct=values.get("股票占净比"),
        bond_pct=values.get("债券占净比"),
        cash_pct=values.get("现金占净比"),
        net_asset_billion=values.get("净资产"),
    )


def _parse_managers(text: str) -> list[FundManagerInfo]:
    payload = _extract_json_var(text, "Data_currentFundManager")
    if not isinstance(payload, list):
        return []
    managers: list[FundManagerInfo] = []
    for item in payload:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        managers.append(
            FundManagerInfo(
                name=name,
                work_time=str(item.get("workTime") or "") or None,
                fund_size=str(item.get("fundSize") or "") or None,
                star=int(float(item["star"])) if _parse_float(item.get("star")) is not None else None,
            )
        )
    return managers


def _parse_similar_rank(text: str) -> FundSimilarRank:
    rank_payload = _extract_json_var(text, "Data_rateInSimilarType")
    percentile_payload = _extract_json_var(text, "Data_rateInSimilarPersent")
    latest_rank = rank_payload[-1] if isinstance(rank_payload, list) and rank_payload else {}
    latest_percentile = percentile_payload[-1] if isinstance(percentile_payload, list) and percentile_payload else []
    rank = _parse_float(latest_rank.get("y")) if isinstance(latest_rank, dict) else None
    total = _parse_float(latest_rank.get("sc")) if isinstance(latest_rank, dict) else None
    percentile = None
    if isinstance(latest_percentile, list) and len(latest_percentile) >= 2:
        percentile = _parse_float(latest_percentile[1])
    return FundSimilarRank(
        rank_date=_date_from_ms(latest_rank.get("x")) if isinstance(latest_rank, dict) else None,
        rank=int(rank) if rank is not None else None,
        total=int(total) if total is not None else None,
        percentile_pct=percentile,
    )


def _decode_archives_content(text: str) -> str:
    match = re.search(r'content\s*:\s*"(?P<content>.*?)"\s*,\s*arryear', text, flags=re.S)
    if not match:
        match = re.search(r"content\s*:\s*'(?P<content>.*?)'\s*,\s*arryear", text, flags=re.S)
    if not match:
        return text
    escaped = match.group("content")
    try:
        decoded = json.loads(f'"{escaped}"')
    except json.JSONDecodeError:
        decoded = escaped.replace('\\"', '"').replace("\\/", "/")
    return unescape(decoded)


def parse_holdings_response(code: str, text: str) -> FundHoldings:
    content = _decode_archives_content(text)
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", content)
    if not date_match:
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    holdings_date = date.fromisoformat(date_match.group(1)) if date_match else date.today()

    doc = html.fromstring(content)
    rows = doc.xpath("//tr[td]")
    items: list[HoldingItem] = []
    for row in rows:
        cells = [" ".join(cell.xpath(".//text()")).strip() for cell in row.xpath("./td")]
        joined = " ".join(cells)
        code_match = re.search(r"\b(\d{6}|\d{5})\b", joined)
        weight_pct = _extract_holdings_weight_pct(cells)
        if not code_match or weight_pct is None:
            continue

        stock_code = code_match.group(1)
        stock_name = ""
        links = [x.strip() for x in row.xpath(".//a/text()") if x.strip()]
        for link_text in links:
            if link_text != stock_code and not re.fullmatch(r"\d+", link_text) and re.search(r"[\u4e00-\u9fffA-Za-z]", link_text):
                stock_name = re.sub(r"\s+", "", link_text)
                break
        for cell in cells:
            if stock_name:
                break
            if (
                stock_code not in cell
                and "%" not in cell
                and cell
                and not re.fullmatch(r"\d+", cell)
                and re.search(r"[\u4e00-\u9fffA-Za-z]", cell)
                and "详细" not in cell
            ):
                stock_name = re.sub(r"\s+", "", cell)
                break
        if not stock_name:
            stock_name = next((x.strip() for x in links if x.strip() and stock_code not in x), stock_code)

        items.append(
            HoldingItem(
                stock_code=stock_code,
                stock_name=stock_name,
                weight_pct=weight_pct,
                market=infer_market(stock_code),
            )
        )

    if not items:
        raise AppError("HOLDINGS_NOT_AVAILABLE", f"基金 {code} 没有可解析的前十大持仓", status_code=422)

    return FundHoldings(fund_code=code, holdings_date=holdings_date, items=items[:10], source="eastmoney")


def _extract_holdings_weight_pct(cells: list[str]) -> float | None:
    # Eastmoney F10 columns are: 序号, 股票代码, 股票名称, 最新价, 涨跌幅, 相关资讯, 占净值比例, ...
    candidates = []
    for idx, cell in enumerate(cells):
        if not cell:
            continue
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*", cell)
        if match:
            candidates.append((idx, float(match.group(1))))
    if not candidates:
        return None
    for idx, value in candidates:
        if idx >= 6:
            return value
    return candidates[0][1]


def _parse_float(value: Any) -> float | None:
    if value in (None, "-", ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_quote_response(payload: dict[str, Any]) -> dict[str, StockQuote]:
    rows = (payload.get("data") or {}).get("diff") or []
    if isinstance(rows, dict):
        rows = rows.values()
    quote_time = datetime.now()
    quotes: dict[str, StockQuote] = {}
    for row in rows:
        code = str(row.get("f12") or "")
        latest = _parse_float(row.get("f2"))
        change_pct = _parse_float(row.get("f3"))
        previous_close = _parse_float(row.get("f18"))
        if not code or latest is None or change_pct is None or previous_close is None:
            continue
        quotes[code] = StockQuote(
            stock_code=code,
            stock_name=str(row.get("f14") or code),
            latest_price=latest,
            previous_close=previous_close,
            change_pct=change_pct,
            quote_time=quote_time,
            market=infer_market(code),
            source="eastmoney",
        )
    return quotes


def parse_single_quote_response(payload: dict[str, Any]) -> StockQuote | None:
    data = payload.get("data") or {}
    code = str(data.get("f57") or "")
    latest_raw = _parse_float(data.get("f43"))
    previous_close_raw = _parse_float(data.get("f60"))
    change_pct_raw = _parse_float(data.get("f170"))
    if not code or latest_raw is None or previous_close_raw is None or change_pct_raw is None:
        return None
    return StockQuote(
        stock_code=code,
        stock_name=str(data.get("f58") or code),
        latest_price=latest_raw / 100,
        previous_close=previous_close_raw / 100,
        change_pct=change_pct_raw / 100,
        quote_time=datetime.now(),
        market=infer_market(code),
        source="eastmoney",
    )


class EastmoneyFundDataSource:
    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout
        self._search_cache: list[FundSearchResult] | None = None

    async def search_funds(self, query: str) -> list[FundSearchResult]:
        query = query.strip().lower()
        if not query:
            return []
        results = await self._load_fund_search()
        return [
            item
            for item in results
            if query in item.code.lower()
            or query in item.name.lower()
            or (item.pinyin and query in item.pinyin.lower())
            if "后端" not in item.name
        ][:30]

    async def list_funds(self) -> list[FundSearchResult]:
        return await self._load_fund_search()

    async def get_profile(self, code: str) -> FundProfile:
        fund_type: str | None = None
        try:
            matches = await self.search_funds(code)
            exact = next((item for item in matches if item.code == code), None)
            fund_type = exact.fund_type if exact else None
        except Exception:
            fund_type = None

        url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js?v={int(time.time() * 1000)}"
        async with httpx.AsyncClient(timeout=self.timeout, headers=DEFAULT_HEADERS, trust_env=False) as client:
            try:
                response = await client.get(url)
                if _is_notfound_redirect(response):
                    raise AppError(
                        "FUND_NOT_FOUND",
                        f"基金代码不存在或当前数据源不可用：{code}",
                        status_code=404,
                        details={"code": code, "redirect": response.headers.get("location")},
                    )
                response.raise_for_status()
            except AppError:
                raise
            except httpx.HTTPError as exc:
                raise DataSourceError(
                    "FUND_SOURCE_FAILED",
                    "基金基础信息/净值数据源请求失败",
                    details={"code": code, "error": str(exc)},
                ) from exc
        return parse_pingzhong_profile(code, _decode_utf8_response(response), fund_type=fund_type)

    async def _load_fund_search(self) -> list[FundSearchResult]:
        if self._search_cache is not None:
            return self._search_cache
        url = f"https://fund.eastmoney.com/js/fundcode_search.js?v={int(time.time() * 1000)}"
        async with httpx.AsyncClient(timeout=self.timeout, headers=DEFAULT_HEADERS, trust_env=False) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise DataSourceError(
                    "FUND_SOURCE_FAILED",
                    "基金搜索数据源请求失败",
                    details={"error": str(exc)},
                ) from exc
        self._search_cache = parse_fund_code_search(_decode_utf8_response(response))
        return self._search_cache


class EastmoneyHoldingsDataSource:
    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    async def get_holdings(self, code: str) -> FundHoldings:
        url = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
        params = {
            "type": "jjcc",
            "code": code,
            "topline": "10",
            "year": "",
            "month": "",
            "rt": str(time.time()),
        }
        async with httpx.AsyncClient(timeout=self.timeout, headers=DEFAULT_HEADERS, trust_env=False) as client:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise DataSourceError(
                    "HOLDINGS_SOURCE_FAILED",
                    "基金持仓数据源请求失败",
                    details={"code": code, "error": str(exc)},
                ) from exc
        return parse_holdings_response(code, _decode_utf8_response(response))


class EastmoneyQuoteDataSource:
    def __init__(self, timeout: float = 5.0, *, use_windows_fallback: bool = False) -> None:
        self.timeout = timeout
        self.use_windows_fallback = use_windows_fallback

    async def get_quotes(self, stock_codes: list[str]) -> dict[str, StockQuote]:
        secids = [to_eastmoney_secid(code) for code in stock_codes]
        secids = [secid for secid in secids if secid]
        if not secids:
            return {}

        url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        params = {
            "fltt": "2",
            "secids": ",".join(secids),
            "fields": "f12,f14,f2,f3,f18",
            "_": str(int(time.time() * 1000)),
        }
        headers = {**DEFAULT_HEADERS, "Referer": "https://quote.eastmoney.com/"}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, trust_env=False) as client:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                quotes = parse_quote_response(response.json())
                if quotes:
                    return quotes
            except httpx.HTTPError as exc:
                last_error: Exception | None = exc
            else:
                last_error = None

            clist_quotes = await self._get_quotes_from_clist(client, stock_codes)
            if clist_quotes:
                return clist_quotes

            if self.use_windows_fallback:
                powershell_payload = await self._get_json_with_powershell(url, params)
                if powershell_payload:
                    quotes = parse_quote_response(powershell_payload)
                    if quotes:
                        return quotes

                fallback_quotes: dict[str, StockQuote] = {}
                for stock_code in stock_codes:
                    quote = await self._get_single_quote(client, stock_code)
                    if quote is not None:
                        fallback_quotes[stock_code] = quote
                if fallback_quotes:
                    return fallback_quotes

        raise DataSourceError(
            "QUOTE_FETCH_FAILED",
            "实时股票行情获取失败",
            details={"stock_codes": stock_codes, "error": str(last_error) if last_error else "empty quote response"},
        )

    async def _get_quotes_from_clist(self, client: httpx.AsyncClient, stock_codes: list[str]) -> dict[str, StockQuote]:
        wanted = set(stock_codes)
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1",
            "pz": "8000",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81",
            "fields": "f12,f14,f2,f3,f18",
            "_": str(int(time.time() * 1000)),
        }
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
        except httpx.HTTPError:
            return {}
        quotes = parse_quote_response(response.json())
        return {code: quote for code, quote in quotes.items() if code in wanted}

    async def _get_single_quote(self, client: httpx.AsyncClient, stock_code: str) -> StockQuote | None:
        secid = to_eastmoney_secid(stock_code)
        if secid is None:
            return None
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": secid,
            "fields": "f43,f57,f58,f60,f170",
            "_": str(int(time.time() * 1000)),
        }
        for _ in range(2):
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                quote = parse_single_quote_response(response.json())
                if quote is not None:
                    return quote
            except httpx.HTTPError:
                powershell_payload = await self._get_json_with_powershell(url, params)
                if powershell_payload:
                    quote = parse_single_quote_response(powershell_payload)
                    if quote is not None:
                        return quote
        return None

    async def _get_json_with_powershell(self, url: str, params: dict[str, str]) -> dict[str, Any] | None:
        if os.name != "nt":
            return None
        return await asyncio.to_thread(self._get_json_with_powershell_sync, url, params)

    def _get_json_with_powershell_sync(self, url: str, params: dict[str, str]) -> dict[str, Any] | None:
        full_url = f"{url}?{urlencode(params, safe=',')}"
        script = (
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
            "$ProgressPreference='SilentlyContinue'; "
            f"(Invoke-WebRequest -Uri '{full_url}' -UseBasicParsing -TimeoutSec {int(self.timeout)}).Content"
        )
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-EncodedCommand",
                    encoded,
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout + 3,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
