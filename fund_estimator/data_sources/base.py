from __future__ import annotations

from typing import Protocol

from fund_estimator.models.schema import FundHoldings, FundProfile, FundSearchResult, StockQuote


class FundDataSource(Protocol):
    async def search_funds(self, query: str) -> list[FundSearchResult]:
        ...

    async def get_profile(self, code: str) -> FundProfile:
        ...


class HoldingsDataSource(Protocol):
    async def get_holdings(self, code: str) -> FundHoldings:
        ...


class QuoteDataSource(Protocol):
    async def get_quotes(self, stock_codes: list[str]) -> dict[str, StockQuote]:
        ...
