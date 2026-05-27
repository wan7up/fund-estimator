from __future__ import annotations

import re
from datetime import datetime

import httpx

from fund_estimator.data_sources.eastmoney import infer_market
from fund_estimator.models.schema import StockQuote
from fund_estimator.services.exceptions import DataSourceError


SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 fund-estimator/0.1",
    "Referer": "https://finance.sina.com.cn/",
}


def to_sina_symbol(stock_code: str) -> str | None:
    market = infer_market(stock_code)
    if market == "SH":
        return f"sh{stock_code}"
    if market == "SZ":
        return f"sz{stock_code}"
    if market == "BJ":
        return f"bj{stock_code}"
    return None


def parse_sina_quotes(text: str) -> dict[str, StockQuote]:
    quotes: dict[str, StockQuote] = {}
    pattern = re.compile(r'var hq_str_(?P<symbol>[a-z]{2})(?P<code>\d{5,6})="(?P<body>.*?)";', re.S)
    for match in pattern.finditer(text):
        code = match.group("code")
        fields = match.group("body").split(",")
        if len(fields) < 32 or not fields[0]:
            continue
        try:
            previous_close = float(fields[2])
            latest_price = float(fields[3])
        except ValueError:
            continue
        if previous_close <= 0:
            continue
        if latest_price <= 0:
            latest_price = previous_close
        change_pct = (latest_price - previous_close) / previous_close * 100
        quote_time = datetime.now()
        if fields[30] and fields[31]:
            try:
                quote_time = datetime.fromisoformat(f"{fields[30]} {fields[31]}")
            except ValueError:
                quote_time = datetime.now()
        quotes[code] = StockQuote(
            stock_code=code,
            stock_name=fields[0],
            latest_price=latest_price,
            previous_close=previous_close,
            change_pct=round(change_pct, 4),
            quote_time=quote_time,
            market=infer_market(code),
            source="sina",
        )
    return quotes


class SinaQuoteDataSource:
    def __init__(self, timeout: float = 5.0) -> None:
        self.timeout = timeout

    async def get_quotes(self, stock_codes: list[str]) -> dict[str, StockQuote]:
        symbols = [to_sina_symbol(code) for code in stock_codes]
        symbols = [symbol for symbol in symbols if symbol]
        if not symbols:
            return {}
        url = f"https://hq.sinajs.cn/list={','.join(symbols)}"
        async with httpx.AsyncClient(timeout=self.timeout, headers=SINA_HEADERS) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise DataSourceError(
                    "QUOTE_FETCH_FAILED",
                    "新浪实时行情获取失败",
                    details={"stock_codes": stock_codes, "error": str(exc)},
                ) from exc
        text = response.content.decode("gb18030", errors="ignore")
        return parse_sina_quotes(text)
