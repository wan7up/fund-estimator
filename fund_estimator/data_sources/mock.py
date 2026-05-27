from __future__ import annotations

from datetime import date, datetime

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
from fund_estimator.services.exceptions import AppError


MOCK_FUND = FundProfile(
    code="001438",
    name="易方达瑞享混合E",
    fund_type="混合型",
    nav_date=date(2026, 5, 25),
    last_nav=9.8255,
    previous_nav_date=date(2026, 5, 22),
    previous_nav=9.537,
    actual_change_pct=3.03,
    accumulated_nav=9.8255,
    details=FundDetailInfo(
        stage_returns=FundStageReturns(one_month_pct=18.6, three_month_pct=31.4, six_month_pct=42.2, one_year_pct=86.5),
        asset_allocation=FundAssetAllocation(
            report_date=date(2026, 3, 31),
            stock_pct=86.2,
            bond_pct=0.0,
            cash_pct=8.1,
            net_asset_billion=12.34,
        ),
        trading=FundTradingInfo(source_rate_pct=1.5, current_rate_pct=0.15, min_purchase_amount=10),
        managers=[FundManagerInfo(name="示例经理", work_time="5年又120天", fund_size="120.00亿(8只基金)", star=4)],
        similar_rank=FundSimilarRank(rank_date=date(2026, 5, 25), rank=12, total=3280, percentile_pct=99.63),
        scale_date=date(2026, 3, 31),
        scale_billion=12.34,
    ),
    source="mock",
)

MOCK_HOLDINGS = FundHoldings(
    fund_code="001438",
    holdings_date=date(2026, 3, 31),
    source="mock",
    items=[
        HoldingItem(stock_code="600519", stock_name="贵州茅台", weight_pct=9.80, market="SH"),
        HoldingItem(stock_code="300750", stock_name="宁德时代", weight_pct=8.60, market="SZ"),
        HoldingItem(stock_code="000333", stock_name="美的集团", weight_pct=7.50, market="SZ"),
        HoldingItem(stock_code="600036", stock_name="招商银行", weight_pct=7.20, market="SH"),
        HoldingItem(stock_code="000858", stock_name="五粮液", weight_pct=6.90, market="SZ"),
        HoldingItem(stock_code="601318", stock_name="中国平安", weight_pct=6.70, market="SH"),
        HoldingItem(stock_code="002594", stock_name="比亚迪", weight_pct=6.50, market="SZ"),
        HoldingItem(stock_code="600900", stock_name="长江电力", weight_pct=6.20, market="SH"),
        HoldingItem(stock_code="601899", stock_name="紫金矿业", weight_pct=5.90, market="SH"),
        HoldingItem(stock_code="300760", stock_name="迈瑞医疗", weight_pct=7.81, market="SZ"),
    ],
)

MOCK_QUOTES: dict[str, StockQuote] = {
    "600519": StockQuote(stock_code="600519", stock_name="贵州茅台", latest_price=1702.3, previous_close=1680.0, change_pct=1.33, quote_time=datetime(2026, 5, 25, 14, 35), market="SH", source="mock"),
    "300750": StockQuote(stock_code="300750", stock_name="宁德时代", latest_price=224.8, previous_close=220.2, change_pct=2.09, quote_time=datetime(2026, 5, 25, 14, 35), market="SZ", source="mock"),
    "000333": StockQuote(stock_code="000333", stock_name="美的集团", latest_price=72.1, previous_close=72.5, change_pct=-0.55, quote_time=datetime(2026, 5, 25, 14, 35), market="SZ", source="mock"),
    "600036": StockQuote(stock_code="600036", stock_name="招商银行", latest_price=41.2, previous_close=40.7, change_pct=1.23, quote_time=datetime(2026, 5, 25, 14, 35), market="SH", source="mock"),
    "000858": StockQuote(stock_code="000858", stock_name="五粮液", latest_price=151.6, previous_close=150.9, change_pct=0.46, quote_time=datetime(2026, 5, 25, 14, 35), market="SZ", source="mock"),
    "601318": StockQuote(stock_code="601318", stock_name="中国平安", latest_price=48.0, previous_close=48.8, change_pct=-1.64, quote_time=datetime(2026, 5, 25, 14, 35), market="SH", source="mock"),
    "002594": StockQuote(stock_code="002594", stock_name="比亚迪", latest_price=251.1, previous_close=246.8, change_pct=1.74, quote_time=datetime(2026, 5, 25, 14, 35), market="SZ", source="mock"),
    "600900": StockQuote(stock_code="600900", stock_name="长江电力", latest_price=29.3, previous_close=29.0, change_pct=1.03, quote_time=datetime(2026, 5, 25, 14, 35), market="SH", source="mock"),
    "601899": StockQuote(stock_code="601899", stock_name="紫金矿业", latest_price=20.9, previous_close=20.5, change_pct=1.95, quote_time=datetime(2026, 5, 25, 14, 35), market="SH", source="mock"),
    "300760": StockQuote(stock_code="300760", stock_name="迈瑞医疗", latest_price=288.2, previous_close=290.0, change_pct=-0.62, quote_time=datetime(2026, 5, 25, 14, 35), market="SZ", source="mock"),
}


class MockFundDataSource:
    async def search_funds(self, query: str) -> list[FundSearchResult]:
        query = query.strip().lower()
        if not query or query in MOCK_FUND.code or query in MOCK_FUND.name.lower():
            return [
                FundSearchResult(
                    code=MOCK_FUND.code,
                    name=MOCK_FUND.name,
                    fund_type=MOCK_FUND.fund_type,
                    pinyin="YFDRXHHE",
                    source="mock",
                )
            ]
        return []

    async def get_profile(self, code: str) -> FundProfile:
        if code == MOCK_FUND.code:
            return MOCK_FUND
        raise AppError("FUND_NOT_FOUND", f"基金代码不存在：{code}", status_code=404)


class MockHoldingsDataSource:
    async def get_holdings(self, code: str) -> FundHoldings:
        if code == MOCK_HOLDINGS.fund_code:
            return MOCK_HOLDINGS
        raise AppError("HOLDINGS_NOT_AVAILABLE", f"基金 {code} 没有可用持仓数据", status_code=422)


class MockQuoteDataSource:
    async def get_quotes(self, stock_codes: list[str]) -> dict[str, StockQuote]:
        return {code: MOCK_QUOTES[code] for code in stock_codes if code in MOCK_QUOTES}
