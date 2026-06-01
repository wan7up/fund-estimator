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
from fund_estimator.models.lof import LofMarketQuote, LofTradingStatus
from fund_estimator.models.etf import EtfMarketQuote
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
        trading=FundTradingInfo(source_rate_pct=1.5, current_rate_pct=0.15, min_purchase_amount=10, purchase_limit_yuan=10_000),
        managers=[FundManagerInfo(name="示例经理", work_time="5年又120天", fund_size="120.00亿(8只基金)", star=4)],
        similar_rank=FundSimilarRank(rank_date=date(2026, 5, 25), rank=12, total=3280, percentile_pct=99.63),
        scale_date=date(2026, 3, 31),
        scale_billion=12.34,
    ),
    source="mock",
)

MOCK_COMPARE_PROFILES: dict[str, FundProfile] = {
    "001439": FundProfile(
        code="001439",
        name="易方达瑞享混合C",
        fund_type="混合型",
        nav_date=date(2026, 5, 25),
        last_nav=9.7012,
        previous_nav_date=date(2026, 5, 22),
        previous_nav=9.4300,
        actual_change_pct=2.88,
        accumulated_nav=9.7012,
        details=FundDetailInfo(
            stage_returns=FundStageReturns(one_month_pct=17.9, three_month_pct=30.1, six_month_pct=40.8, one_year_pct=82.2),
            asset_allocation=FundAssetAllocation(
                report_date=date(2026, 3, 31),
                stock_pct=84.7,
                bond_pct=0.0,
                cash_pct=9.4,
                net_asset_billion=8.96,
            ),
            trading=FundTradingInfo(source_rate_pct=0.0, current_rate_pct=0.0, min_purchase_amount=10, purchase_limit_yuan=10_000),
            managers=[FundManagerInfo(name="示例经理", work_time="5年又120天", fund_size="120.00亿(8只基金)", star=4)],
            similar_rank=FundSimilarRank(rank_date=date(2026, 5, 25), rank=18, total=3280, percentile_pct=99.48),
            scale_date=date(2026, 3, 31),
            scale_billion=8.96,
        ),
        source="mock",
    ),
    "008888": FundProfile(
        code="008888",
        name="示例科技先锋混合A",
        fund_type="混合型",
        nav_date=date(2026, 5, 25),
        last_nav=2.3180,
        previous_nav_date=date(2026, 5, 22),
        previous_nav=2.2810,
        actual_change_pct=1.62,
        accumulated_nav=2.3180,
        details=FundDetailInfo(
            stage_returns=FundStageReturns(one_month_pct=11.2, three_month_pct=22.6, six_month_pct=33.3, one_year_pct=58.7),
            asset_allocation=FundAssetAllocation(
                report_date=date(2026, 3, 31),
                stock_pct=91.8,
                bond_pct=0.0,
                cash_pct=5.6,
                net_asset_billion=4.28,
            ),
            trading=FundTradingInfo(source_rate_pct=1.5, current_rate_pct=0.12, min_purchase_amount=10, purchase_limit_yuan=50_000),
            managers=[FundManagerInfo(name="科技经理", work_time="3年又80天", fund_size="38.00亿(4只基金)", star=3)],
            similar_rank=FundSimilarRank(rank_date=date(2026, 5, 25), rank=160, total=3280, percentile_pct=95.15),
            scale_date=date(2026, 3, 31),
            scale_billion=4.28,
        ),
        source="mock",
    ),
    "000888": FundProfile(
        code="000888",
        name="示例稳健债券A",
        fund_type="债券型",
        nav_date=date(2026, 5, 25),
        last_nav=1.1280,
        previous_nav_date=date(2026, 5, 22),
        previous_nav=1.1265,
        actual_change_pct=0.13,
        accumulated_nav=1.3180,
        details=FundDetailInfo(
            stage_returns=FundStageReturns(one_month_pct=0.8, three_month_pct=2.1, six_month_pct=3.4, one_year_pct=6.2),
            asset_allocation=FundAssetAllocation(
                report_date=date(2026, 3, 31),
                stock_pct=4.5,
                bond_pct=88.0,
                cash_pct=4.0,
                net_asset_billion=52.4,
            ),
            trading=FundTradingInfo(source_rate_pct=0.8, current_rate_pct=0.08, min_purchase_amount=10, purchase_limit_yuan=None),
            managers=[FundManagerInfo(name="固收经理", work_time="7年又60天", fund_size="210.00亿(12只基金)", star=4)],
            similar_rank=FundSimilarRank(rank_date=date(2026, 5, 25), rank=420, total=2500, percentile_pct=83.24),
            scale_date=date(2026, 3, 31),
            scale_billion=52.4,
        ),
        source="mock",
    ),
}

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

MOCK_COMPARE_HOLDINGS: dict[str, FundHoldings] = {
    "001439": FundHoldings(
        fund_code="001439",
        holdings_date=date(2026, 3, 31),
        source="mock",
        items=[
            HoldingItem(stock_code="600519", stock_name="贵州茅台", weight_pct=9.40, market="SH"),
            HoldingItem(stock_code="300750", stock_name="宁德时代", weight_pct=8.30, market="SZ"),
            HoldingItem(stock_code="000333", stock_name="美的集团", weight_pct=7.20, market="SZ"),
            HoldingItem(stock_code="600036", stock_name="招商银行", weight_pct=7.10, market="SH"),
            HoldingItem(stock_code="000858", stock_name="五粮液", weight_pct=6.80, market="SZ"),
            HoldingItem(stock_code="601318", stock_name="中国平安", weight_pct=6.40, market="SH"),
            HoldingItem(stock_code="002594", stock_name="比亚迪", weight_pct=6.20, market="SZ"),
            HoldingItem(stock_code="600900", stock_name="长江电力", weight_pct=6.10, market="SH"),
            HoldingItem(stock_code="601899", stock_name="紫金矿业", weight_pct=5.80, market="SH"),
            HoldingItem(stock_code="300760", stock_name="迈瑞医疗", weight_pct=7.30, market="SZ"),
        ],
    ),
    "008888": FundHoldings(
        fund_code="008888",
        holdings_date=date(2026, 3, 31),
        source="mock",
        items=[
            HoldingItem(stock_code="688981", stock_name="中芯国际", weight_pct=9.20, market="SH"),
            HoldingItem(stock_code="002371", stock_name="北方华创", weight_pct=8.60, market="SZ"),
            HoldingItem(stock_code="300782", stock_name="卓胜微", weight_pct=7.40, market="SZ"),
            HoldingItem(stock_code="603501", stock_name="韦尔股份", weight_pct=7.20, market="SH"),
            HoldingItem(stock_code="688012", stock_name="中微公司", weight_pct=6.80, market="SH"),
            HoldingItem(stock_code="688008", stock_name="澜起科技", weight_pct=6.30, market="SH"),
            HoldingItem(stock_code="300661", stock_name="圣邦股份", weight_pct=5.80, market="SZ"),
            HoldingItem(stock_code="002475", stock_name="立讯精密", weight_pct=5.40, market="SZ"),
            HoldingItem(stock_code="300308", stock_name="中际旭创", weight_pct=5.10, market="SZ"),
            HoldingItem(stock_code="000063", stock_name="中兴通讯", weight_pct=4.80, market="SZ"),
        ],
    ),
}

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
    "688981": StockQuote(stock_code="688981", stock_name="中芯国际", latest_price=65.1, previous_close=63.4, change_pct=2.68, quote_time=datetime(2026, 5, 25, 14, 35), market="SH", source="mock"),
    "002371": StockQuote(stock_code="002371", stock_name="北方华创", latest_price=340.5, previous_close=333.2, change_pct=2.19, quote_time=datetime(2026, 5, 25, 14, 35), market="SZ", source="mock"),
    "300782": StockQuote(stock_code="300782", stock_name="卓胜微", latest_price=98.8, previous_close=99.6, change_pct=-0.80, quote_time=datetime(2026, 5, 25, 14, 35), market="SZ", source="mock"),
    "603501": StockQuote(stock_code="603501", stock_name="韦尔股份", latest_price=121.6, previous_close=118.1, change_pct=2.96, quote_time=datetime(2026, 5, 25, 14, 35), market="SH", source="mock"),
    "688012": StockQuote(stock_code="688012", stock_name="中微公司", latest_price=162.4, previous_close=158.0, change_pct=2.78, quote_time=datetime(2026, 5, 25, 14, 35), market="SH", source="mock"),
    "688008": StockQuote(stock_code="688008", stock_name="澜起科技", latest_price=76.2, previous_close=75.8, change_pct=0.53, quote_time=datetime(2026, 5, 25, 14, 35), market="SH", source="mock"),
    "300661": StockQuote(stock_code="300661", stock_name="圣邦股份", latest_price=88.5, previous_close=87.1, change_pct=1.61, quote_time=datetime(2026, 5, 25, 14, 35), market="SZ", source="mock"),
    "002475": StockQuote(stock_code="002475", stock_name="立讯精密", latest_price=41.2, previous_close=40.6, change_pct=1.48, quote_time=datetime(2026, 5, 25, 14, 35), market="SZ", source="mock"),
    "300308": StockQuote(stock_code="300308", stock_name="中际旭创", latest_price=190.7, previous_close=184.0, change_pct=3.64, quote_time=datetime(2026, 5, 25, 14, 35), market="SZ", source="mock"),
    "000063": StockQuote(stock_code="000063", stock_name="中兴通讯", latest_price=33.8, previous_close=33.4, change_pct=1.20, quote_time=datetime(2026, 5, 25, 14, 35), market="SZ", source="mock"),
}

MOCK_LOF_PROFILES: dict[str, FundProfile] = {
    "161128": FundProfile(
        code="161128",
        name="易方达标普信息科技指数(QDII-LOF)A",
        fund_type="QDII-LOF",
        nav_date=date(2026, 5, 29),
        last_nav=1.0000,
        previous_nav_date=date(2026, 5, 28),
        previous_nav=0.9900,
        actual_change_pct=1.01,
        source="mock",
    ),
    "501018": FundProfile(
        code="501018",
        name="南方原油A",
        fund_type="QDII-LOF",
        nav_date=date(2026, 5, 29),
        last_nav=1.8000,
        previous_nav_date=date(2026, 5, 28),
        previous_nav=1.8200,
        actual_change_pct=-1.10,
        source="mock",
    ),
    "164906": FundProfile(
        code="164906",
        name="交银中证海外中国互联网指数(QDII-LOF)",
        fund_type="QDII-LOF",
        nav_date=date(2026, 5, 29),
        last_nav=0.8200,
        previous_nav_date=date(2026, 5, 28),
        previous_nav=0.8120,
        actual_change_pct=0.99,
        source="mock",
    ),
    "160644": FundProfile(
        code="160644",
        name="鹏华港美互联股票(LOF)",
        fund_type="QDII-LOF",
        nav_date=date(2026, 5, 29),
        last_nav=2.0500,
        previous_nav_date=date(2026, 5, 28),
        previous_nav=2.0100,
        actual_change_pct=1.99,
        source="mock",
    ),
    "160717": FundProfile(
        code="160717",
        name="嘉实恒生中国企业(QDII-LOF)",
        fund_type="QDII-LOF",
        nav_date=date(2026, 5, 29),
        last_nav=0.7600,
        previous_nav_date=date(2026, 5, 28),
        previous_nav=0.7550,
        actual_change_pct=0.66,
        source="mock",
    ),
}

MOCK_LOF_QUOTES: dict[str, tuple[float, float, float]] = {
    "161128": (1.055, 2.20, 8_200_000),
    "501018": (1.930, -1.40, 242_000_000),
    "164906": (0.805, 0.30, 1_200_000),
    "160644": (2.260, -3.20, 2_266_000_000),
    "160717": (0.748, -0.50, 4_600_000),
}

MOCK_LOF_PREFIXES = ("05", "16", "501", "502", "503", "505", "506")


class MockFundDataSource:
    async def search_funds(self, query: str) -> list[FundSearchResult]:
        query = query.strip().lower()
        results: list[FundSearchResult] = []
        if not query or query in MOCK_FUND.code or query in MOCK_FUND.name.lower():
            results.append(
                FundSearchResult(
                    code=MOCK_FUND.code,
                    name=MOCK_FUND.name,
                    fund_type=MOCK_FUND.fund_type,
                    pinyin="YFDRXHHE",
                    source="mock",
                )
            )
        for profile in MOCK_COMPARE_PROFILES.values():
            if not query or query in profile.code or query in profile.name.lower():
                results.append(
                    FundSearchResult(
                        code=profile.code,
                        name=profile.name,
                        fund_type=profile.fund_type,
                        pinyin="COMPARE",
                        source="mock",
                    )
                )
        for profile in MOCK_LOF_PROFILES.values():
            if not query or query in profile.code or query in profile.name.lower() or "lof" in query:
                results.append(
                    FundSearchResult(
                        code=profile.code,
                        name=profile.name,
                        fund_type=profile.fund_type,
                        pinyin="LOF",
                        source="mock",
                )
            )
        return results

    async def list_funds(self) -> list[FundSearchResult]:
        rows = [
            FundSearchResult(
                code=MOCK_FUND.code,
                name=MOCK_FUND.name,
                fund_type=MOCK_FUND.fund_type,
                pinyin="YFDRXHHE",
                source="mock",
            )
        ]
        rows.extend(
            FundSearchResult(
                code=profile.code,
                name=profile.name,
                fund_type=profile.fund_type,
                pinyin="COMPARE",
                source="mock",
            )
            for profile in MOCK_COMPARE_PROFILES.values()
        )
        rows.extend(
            FundSearchResult(
                code=profile.code,
                name=profile.name,
                fund_type=profile.fund_type,
                pinyin="LOF",
                source="mock",
            )
            for profile in MOCK_LOF_PROFILES.values()
        )
        return rows

    async def get_profile(self, code: str) -> FundProfile:
        if code == MOCK_FUND.code:
            return MOCK_FUND
        if code in MOCK_COMPARE_PROFILES:
            return MOCK_COMPARE_PROFILES[code]
        if code in MOCK_LOF_PROFILES:
            return MOCK_LOF_PROFILES[code]
        if code.isdigit() and len(code) == 6 and code.startswith(MOCK_LOF_PREFIXES):
            return FundProfile(
                code=code,
                name=f"演示LOF{code}",
                fund_type="QDII-LOF",
                nav_date=date(2026, 5, 29),
                last_nav=1.0000,
                previous_nav_date=date(2026, 5, 28),
                previous_nav=0.9950,
                actual_change_pct=0.50,
                source="mock",
            )
        raise AppError("FUND_NOT_FOUND", f"基金代码不存在：{code}", status_code=404)


class MockHoldingsDataSource:
    async def get_holdings(self, code: str) -> FundHoldings:
        if code == MOCK_HOLDINGS.fund_code:
            return MOCK_HOLDINGS
        if code in MOCK_COMPARE_HOLDINGS:
            return MOCK_COMPARE_HOLDINGS[code]
        raise AppError("HOLDINGS_NOT_AVAILABLE", f"基金 {code} 没有可用持仓数据", status_code=422)


class MockQuoteDataSource:
    async def get_quotes(self, stock_codes: list[str]) -> dict[str, StockQuote]:
        return {code: MOCK_QUOTES[code] for code in stock_codes if code in MOCK_QUOTES}


class MockLofMarketDataSource:
    async def get_quotes(self, codes: list[str]) -> dict[str, LofMarketQuote]:
        quote_time = datetime(2026, 5, 30, 10, 15)
        quotes: dict[str, LofMarketQuote] = {}
        for code in codes:
            profile = MOCK_LOF_PROFILES.get(code)
            if profile is None and code.isdigit() and len(code) == 6 and code.startswith(MOCK_LOF_PREFIXES):
                profile = FundProfile(
                    code=code,
                    name=f"演示LOF{code}",
                    fund_type="QDII-LOF",
                    nav_date=date(2026, 5, 29),
                    last_nav=1.0000,
                    previous_nav_date=date(2026, 5, 28),
                    previous_nav=0.9950,
                    actual_change_pct=0.50,
                    source="mock",
                )
            if profile is None:
                continue
            quote = MOCK_LOF_QUOTES.get(code)
            if quote is None:
                seed = sum(int(char) for char in code if char.isdigit())
                premium_pct = (-1.4, -0.7, 0.3, 0.9, 1.4)[seed % 5]
                change_pct = (-0.8, -0.2, 0.1, 0.5, 0.9)[seed % 5]
                turnover = 2_000_000 + (seed % 8) * 650_000
                quote = ((profile.last_nav or 1.0) * (1 + premium_pct / 100), change_pct, turnover)
            latest_price, change_pct, turnover = quote
            quotes[code] = LofMarketQuote(
                code=code,
                name=profile.name,
                latest_price=latest_price,
                previous_close=latest_price / (1 + change_pct / 100),
                change_pct=change_pct,
                turnover_yuan=turnover,
                quote_time=quote_time,
                market="SH" if code.startswith("5") else "SZ",
                source="mock",
            )
        return quotes

    async def get_all_quotes(self) -> dict[str, LofMarketQuote]:
        return await self.get_quotes(list(MOCK_LOF_PROFILES.keys()))


class MockLofTradingStatusDataSource:
    async def get_status(self, code: str, profile: FundProfile) -> LofTradingStatus:
        if code == "501018":
            return LofTradingStatus(
                purchase_status="暂停",
                redemption_status="开放",
                daily_purchase_limit_yuan=1,
                fee_rate_pct=0.12,
                source="mock",
            )
        if code == "164906":
            return LofTradingStatus(
                purchase_status="限制大额",
                redemption_status="开放",
                daily_purchase_limit_yuan=1000,
                fee_rate_pct=0.10,
                source="mock",
            )
        return LofTradingStatus(
            purchase_status="开放",
            redemption_status="开放",
            daily_purchase_limit_yuan=10_000,
            fee_rate_pct=0.10,
            source="mock",
        )


class MockLofProxyDataSource:
    async def get_changes(self, symbols: list[str]) -> dict[str, float]:
        changes = {
            "NQ=F": 1.10,
            "ES=F": 0.45,
            "^HSI": -0.35,
            "KWEB": -0.80,
            "CL=F": -2.20,
            "VNQ": 0.20,
            "^NSEI": 0.15,
            "XOP": -1.40,
            "GC=F": 0.30,
        }
        return {symbol: changes[symbol] for symbol in symbols if symbol in changes}


class MockHaoEtfDataSource:
    async def get_snapshots(self, codes: list[str]) -> dict[str, object]:
        return {}


class MockEtfMarketDataSource:
    async def get_all_quotes(self) -> dict[str, EtfMarketQuote]:
        quote_time = datetime(2026, 5, 30, 10, 15)
        rows = {
            "159605": ("中概互联ETF", 0.820, 0.817, 0.24, 2_900_067_000),
            "513050": ("美元债LOF", 0.944, 0.945, 0.11, 108_562_000),
            "513500": ("标普500ETF", 1.428, 1.421, 0.14, 18_809_000),
            "159941": ("纳指ETF", 1.032, 1.028, 0.36, 98_000_000),
        }
        return {
            code: EtfMarketQuote(
                code=code,
                name=name,
                latest_price=price,
                previous_close=price / (1 + change_pct / 100),
                change_pct=change_pct,
                turnover_yuan=turnover,
                iopv=iopv,
                quote_time=quote_time,
                market="SH" if code.startswith("5") else "SZ",
                source="mock",
            )
            for code, (name, price, iopv, change_pct, turnover) in rows.items()
        }
