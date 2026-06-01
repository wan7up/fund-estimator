from __future__ import annotations

from typing import Any

from fund_estimator.models.schema import StockQuote
from fund_estimator.services.exceptions import AppError, DataSourceError


class FallbackQuoteDataSource:
    def __init__(self, sources: list[Any]) -> None:
        self.sources = sources

    async def get_quotes(self, stock_codes: list[str]) -> dict[str, StockQuote]:
        remaining = list(dict.fromkeys(stock_codes))
        quotes: dict[str, StockQuote] = {}
        errors: list[str] = []
        for source in self.sources:
            if not remaining:
                break
            try:
                fetched = await source.get_quotes(remaining)
            except AppError as exc:
                errors.append(f"{source.__class__.__name__}: {exc.message}")
                continue
            quotes.update(fetched)
            remaining = [code for code in remaining if code not in quotes]
        if not quotes and errors:
            raise DataSourceError(
                "QUOTE_FETCH_FAILED",
                "实时股票行情获取失败",
                details={"stock_codes": stock_codes, "errors": errors},
            )
        return quotes
