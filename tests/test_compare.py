from __future__ import annotations

import asyncio
from datetime import date, datetime

from fund_estimator.models.schema import (
    FundAssetAllocation,
    FundDetailInfo,
    FundHoldings,
    FundManagerInfo,
    FundProfile,
    FundSimilarRank,
    FundStageReturns,
    FundTradingInfo,
    HoldingItem,
    StockQuote,
    CompareRequest,
)
from fund_estimator.services.cache import SQLiteCache
from fund_estimator.services.comparison import FundComparisonService
from fund_estimator.services.estimator import FundEstimatorService
from fund_estimator.services.exceptions import AppError


def profile(
    code: str,
    name: str,
    fund_type: str,
    *,
    one_year: float,
    stock: float,
    bond: float,
    cash: float,
    fee: float,
    scale: float,
    percentile: float,
) -> FundProfile:
    return FundProfile(
        code=code,
        name=name,
        fund_type=fund_type,
        nav_date=date(2026, 5, 29),
        last_nav=1.0,
        previous_nav_date=date(2026, 5, 28),
        previous_nav=0.99,
        actual_change_pct=1.01,
        details=FundDetailInfo(
            stage_returns=FundStageReturns(
                one_month_pct=one_year / 8,
                three_month_pct=one_year / 4,
                six_month_pct=one_year / 2,
                one_year_pct=one_year,
            ),
            asset_allocation=FundAssetAllocation(
                report_date=date(2026, 3, 31),
                stock_pct=stock,
                bond_pct=bond,
                cash_pct=cash,
                net_asset_billion=scale,
            ),
            trading=FundTradingInfo(current_rate_pct=fee, min_purchase_amount=10, purchase_limit_yuan=10_000),
            managers=[FundManagerInfo(name="测试经理", work_time="4年", fund_size="20.00亿")],
            similar_rank=FundSimilarRank(rank_date=date(2026, 5, 29), rank=20, total=1000, percentile_pct=percentile),
            scale_date=date(2026, 3, 31),
            scale_billion=scale,
        ),
        source="fake",
    )


PROFILES = {
    "100001": profile("100001", "测试科技半导体精选A", "混合型", one_year=45, stock=88, bond=0, cash=5, fee=0.12, scale=18, percentile=96),
    "100002": profile("100002", "测试科技半导体精选C", "混合型", one_year=42, stock=86, bond=0, cash=7, fee=0.0, scale=12, percentile=95),
    "100003": profile("100003", "测试半导体先锋混合A", "混合型", one_year=60, stock=93, bond=0, cash=4, fee=0.15, scale=5, percentile=91),
    "200001": profile("200001", "测试稳健债券A", "债券型", one_year=5, stock=4, bond=88, cash=4, fee=0.08, scale=50, percentile=83),
    "300002": profile("300002", "测试主题未明混合A", "混合型", one_year=28, stock=82, bond=0, cash=8, fee=0.1, scale=9, percentile=88),
}

HOLDINGS = {
    "100001": FundHoldings(
        fund_code="100001",
        holdings_date=date(2026, 3, 31),
        source="fake",
        items=[
            HoldingItem(stock_code="600000", stock_name="浦发银行", weight_pct=18, market="SH"),
            HoldingItem(stock_code="000001", stock_name="平安银行", weight_pct=16, market="SZ"),
            HoldingItem(stock_code="600519", stock_name="贵州茅台", weight_pct=14, market="SH"),
            HoldingItem(stock_code="300750", stock_name="宁德时代", weight_pct=12, market="SZ"),
        ],
    ),
    "100002": FundHoldings(
        fund_code="100002",
        holdings_date=date(2026, 3, 31),
        source="fake",
        items=[
            HoldingItem(stock_code="600000", stock_name="浦发银行", weight_pct=17.6, market="SH"),
            HoldingItem(stock_code="000001", stock_name="平安银行", weight_pct=15.8, market="SZ"),
            HoldingItem(stock_code="600519", stock_name="贵州茅台", weight_pct=14.2, market="SH"),
            HoldingItem(stock_code="300750", stock_name="宁德时代", weight_pct=12.4, market="SZ"),
        ],
    ),
    "100003": FundHoldings(
        fund_code="100003",
        holdings_date=date(2026, 3, 31),
        source="fake",
        items=[
            HoldingItem(stock_code="688981", stock_name="中芯国际", weight_pct=18, market="SH"),
            HoldingItem(stock_code="002371", stock_name="北方华创", weight_pct=16, market="SZ"),
            HoldingItem(stock_code="300782", stock_name="卓胜微", weight_pct=14, market="SZ"),
            HoldingItem(stock_code="603501", stock_name="韦尔股份", weight_pct=12, market="SH"),
        ],
    ),
    "300002": FundHoldings(
        fund_code="300002",
        holdings_date=date(2026, 3, 31),
        source="fake",
        items=[
            HoldingItem(stock_code="688111", stock_name="半导体芯片设备", weight_pct=18, market="SH"),
            HoldingItem(stock_code="688222", stock_name="集成电路材料", weight_pct=16, market="SH"),
            HoldingItem(stock_code="688333", stock_name="电子制造龙头", weight_pct=12, market="SH"),
        ],
    ),
}

QUOTES = {
    code: StockQuote(
        stock_code=code,
        stock_name=code,
        latest_price=10,
        previous_close=9.9,
        change_pct=1.01,
        quote_time=datetime(2026, 5, 29, 14, 30),
        market="SH" if code.startswith("6") else "SZ",
        source="fake",
    )
    for code in {"600000", "000001", "600519", "300750", "688981", "002371", "300782", "603501", "688111", "688222", "688333"}
}


class FakeFundSource:
    async def search_funds(self, query: str):
        return []

    async def get_profile(self, code: str) -> FundProfile:
        if code not in PROFILES:
            raise AppError("FUND_NOT_FOUND", f"基金代码不存在：{code}", status_code=404)
        return PROFILES[code]


class FakeHoldingsSource:
    async def get_holdings(self, code: str) -> FundHoldings:
        if code not in HOLDINGS:
            raise AppError("HOLDINGS_NOT_AVAILABLE", f"基金 {code} 没有可用持仓数据", status_code=422)
        return HOLDINGS[code]


class BrokenHoldingsSource:
    async def get_holdings(self, code: str) -> FundHoldings:
        raise RuntimeError("empty holdings document")


class FakeQuoteSource:
    async def get_quotes(self, stock_codes: list[str]) -> dict[str, StockQuote]:
        return {code: QUOTES[code] for code in stock_codes if code in QUOTES}


def make_service(tmp_path, holdings_source=None) -> FundComparisonService:
    estimator = FundEstimatorService(
        fund_source=FakeFundSource(),
        holdings_source=holdings_source or FakeHoldingsSource(),
        quote_source=FakeQuoteSource(),
        cache=SQLiteCache(tmp_path / "compare.sqlite3"),
        allow_mock_fallback=False,
    )
    return FundComparisonService(estimator)


def compare(tmp_path, codes, strategy="balanced", theme_hint=None):
    service = make_service(tmp_path)
    request = CompareRequest(codes=codes, strategy=strategy, theme_hint=theme_hint)
    return asyncio.run(service.compare(request))


def test_very_similar_funds_get_direct_recommendation(tmp_path):
    result = compare(tmp_path, ["100001", "100002"], theme_hint="半导体")

    assert result.conclusion == "very_similar"
    assert result.recommendation_code in {"100001", "100002"}
    assert "逐只风格" in result.recommendation
    assert "购买取舍" in result.recommendation
    assert "相对优选" in result.recommendation
    assert "相对稳妥" in result.recommendation
    assert "不构成投资建议" not in result.recommendation
    assert "仅供研究参考" not in result.recommendation
    assert result.funds[0].recommended is True
    assert result.pair_similarities[0].holdings_similarity > 90
    assert not hasattr(result.funds[0].score_breakdown, "confidence")
    assert not hasattr(result.funds[0].score_breakdown, "estimated_move")
    assert not hasattr(result.funds[0].score_breakdown, "fee")
    assert hasattr(result.funds[0].score_breakdown, "manager")
    assert any(factor.key == "manager" for factor in result.score_factors)
    assert all(factor.key != "fee" for factor in result.score_factors)
    assert result.funds[0].snapshot.current_rate_pct is not None
    assert result.funds[0].snapshot.purchase_limit_yuan == 10_000


def test_purchase_limit_parser_handles_daily_limit_text():
    text = "<html><body>交易状态：开放申购 单日累计购买上限 1000 元</body></html>"

    assert FundComparisonService._extract_purchase_limit_yuan(text) == 1000


def test_purchase_limit_parser_ignores_minimum_purchase_text():
    text = "<html><body>申购金额限制：购买起点 10 元，首次申购最低 10 元，追加申购最低 10 元</body></html>"

    assert FundComparisonService._extract_purchase_limit_yuan(text) is None


def test_purchase_limit_parser_ignores_unlimited_text_before_minimum_purchase():
    text = "<html><body>申购起点 10.00 元 定投起点 10.00 元 日累计申购限额 无限额 首次购买 10.00 元</body></html>"

    assert FundComparisonService._extract_purchase_limit_yuan(text) is None


def test_purchase_limit_parser_keeps_limit_when_minimum_purchase_also_present():
    text = "<html><body>申购金额限制：购买起点 10 元。单日累计购买上限 1000 元。</body></html>"

    assert FundComparisonService._extract_purchase_limit_yuan(text) == 1000


def test_profile_purchase_limit_ignores_minimum_purchase_amount():
    item = profile(
        "300001",
        "测试起购金额基金A",
        "指数型",
        one_year=20,
        stock=90,
        bond=0,
        cash=5,
        fee=0.1,
        scale=8,
        percentile=80,
    )
    item.details.trading.purchase_limit_yuan = 10

    assert FundComparisonService._profile_purchase_limit_yuan(item) is None


def test_same_theme_different_funds_are_scored(tmp_path):
    result = compare(tmp_path, ["100001", "100003"], theme_hint="半导体")

    assert result.conclusion == "same_theme_different"
    assert result.recommendation_code in {"100001", "100003"}
    assert result.theme_analysis is not None
    assert result.theme_analysis.theme_hint == "半导体"
    assert "目标板块是“半导体”" in result.theme_analysis.summary
    assert all(item.match_level == "match" for item in result.theme_analysis.exposures)
    assert "板块匹配" in result.recommendation
    assert "逐只风格" in result.recommendation
    assert "股票仓位" in result.recommendation
    assert result.pair_similarities[0].relation == "same_theme_different"
    assert result.pair_similarities[0].holdings_similarity < 40


def test_unrelated_funds_do_not_get_strong_recommendation(tmp_path):
    result = compare(tmp_path, ["100001", "200001"], theme_hint="半导体")

    assert result.conclusion == "not_comparable"
    assert result.recommendation_code is None
    assert result.theme_analysis is not None
    assert any(item.code == "200001" and item.match_level == "unmatched" for item in result.theme_analysis.exposures)
    assert "偏离目标板块" in result.theme_analysis.summary
    assert "板块匹配" in result.recommendation
    assert "逐只风格" in result.recommendation
    assert "低权益波动" in result.recommendation
    assert "购买取舍" in result.recommendation
    assert "目标板块/同组候选" in result.recommendation
    assert "不构成投资建议" not in result.recommendation
    assert "仅供研究参考" not in result.recommendation
    assert all(not item.recommended for item in result.funds)
    assert any("同板块/同资产类型小组" in warning for warning in result.warnings)


def test_theme_analysis_without_hint_only_reports_inferred_themes(tmp_path):
    result = compare(tmp_path, ["100001", "100003"])

    assert result.theme_analysis is not None
    assert result.theme_analysis.theme_hint is None
    assert "自动识别" in result.theme_analysis.summary
    assert "共同板块" in result.theme_analysis.summary
    assert "未填写目标板块" not in result.theme_analysis.summary
    assert "板块线索" in result.recommendation
    assert all("未填写目标板块" not in item.comment for item in result.theme_analysis.exposures)
    assert all(item.match_level == "match" for item in result.theme_analysis.exposures)


def test_style_summary_uses_holdings_theme_evidence(tmp_path):
    result = compare(tmp_path, ["300002", "100003"])

    assert result.theme_analysis is not None
    exposure = next(item for item in result.theme_analysis.exposures if item.code == "300002")
    assert "半导体" in exposure.inferred_themes
    assert "测试主题未明混合A（300002）" in result.recommendation
    assert "主题线索偏半导体" in result.recommendation
    assert "需要结合持仓进一步确认" not in result.recommendation
    assert "结合外部基金档案复核" not in result.recommendation


def test_mixed_group_explains_similar_pairs_and_outliers(tmp_path):
    result = compare(tmp_path, ["100001", "100002", "100003", "200001"], theme_hint="半导体")

    assert result.conclusion == "not_comparable"
    assert result.recommendation_code is None
    assert "100001" in result.recommendation
    assert "100002" in result.recommendation
    assert "200001" in result.recommendation
    assert "相对优选" in result.recommendation
    assert "偏离项" in result.recommendation
    assert any(pair.relation == "very_similar" for pair in result.pair_similarities)
    assert any(pair.relation == "not_comparable" for pair in result.pair_similarities)


def test_two_to_four_candidates_and_strategy_switch(tmp_path):
    balanced = compare(tmp_path, ["100001", "100002", "100003"], strategy="balanced", theme_hint="半导体")
    aggressive = compare(tmp_path, ["100001", "100002", "100003"], strategy="aggressive", theme_hint="半导体")

    assert len(balanced.funds) == 3
    assert balanced.conclusion == "same_theme_different"
    assert aggressive.strategy == "aggressive"
    assert [item.total_score for item in balanced.funds] != [item.total_score for item in aggressive.funds]


def test_missing_holdings_and_estimate_degrade_with_warning(tmp_path):
    result = compare(tmp_path, ["100002", "200001"], strategy="low_cost")

    bond = next(item for item in result.funds if item.code == "200001")
    assert bond.snapshot.top10_weight_sum is None
    assert bond.warnings
    assert result.conclusion == "not_comparable"


def test_unexpected_holdings_parser_error_degrades_with_warning(tmp_path):
    service = make_service(tmp_path, holdings_source=BrokenHoldingsSource())
    request = CompareRequest(codes=["100001", "200001"], strategy="balanced", theme_hint="半导体")

    result = asyncio.run(service.compare(request))

    assert result.conclusion == "not_comparable"
    assert all(item.snapshot.top10_weight_sum is None for item in result.funds)
    assert any("RuntimeError" in warning for item in result.funds for warning in item.warnings)
    assert result.theme_analysis is not None
