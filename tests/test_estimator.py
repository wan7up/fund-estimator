from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

import pytest

from fund_estimator.models.schema import FundAssetAllocation, FundDetailInfo, FundHoldings, FundProfile, HoldingItem, StockQuote
from fund_estimator.services.cache import SQLiteCache
from fund_estimator.services.estimator import MARKET_TZ, FundEstimatorService
from fund_estimator.services.exceptions import DataSourceError


class FakeFundSource:
    async def search_funds(self, query: str):
        return []

    async def get_profile(self, code: str) -> FundProfile:
        nav_date = FundEstimatorService._previous_trading_day(FundEstimatorService._current_estimate_date())
        previous_nav_date = FundEstimatorService._previous_trading_day(nav_date)
        return FundProfile(
            code=code,
            name="测试基金",
            fund_type="混合型",
            nav_date=nav_date,
            last_nav=10.0,
            previous_nav_date=previous_nav_date,
            previous_nav=9.9,
            actual_change_pct=1.0101,
            source="fake",
        )


class FakeHoldingsSource:
    async def get_holdings(self, code: str) -> FundHoldings:
        return FundHoldings(
            fund_code=code,
            holdings_date=date.today(),
            source="fake",
            items=[
                HoldingItem(stock_code="600000", stock_name="浦发银行", weight_pct=50.0, market="SH"),
                HoldingItem(stock_code="000001", stock_name="平安银行", weight_pct=25.0, market="SZ"),
            ],
        )


class FakeQuoteSource:
    def __init__(self, missing: set[str] | None = None) -> None:
        self.missing = missing or set()

    async def get_quotes(self, stock_codes: list[str]) -> dict[str, StockQuote]:
        data = {
            "600000": StockQuote(
                stock_code="600000",
                stock_name="浦发银行",
                latest_price=10.2,
                previous_close=10.0,
                change_pct=2.0,
                quote_time=datetime.now(),
                market="SH",
                source="fake",
            ),
            "000001": StockQuote(
                stock_code="000001",
                stock_name="平安银行",
                latest_price=9.9,
                previous_close=10.0,
                change_pct=-1.0,
                quote_time=datetime.now(),
                market="SZ",
                source="fake",
            ),
            "161128": StockQuote(
                stock_code="161128",
                stock_name="标普信息科技LOF",
                latest_price=1.02,
                previous_close=1.0,
                change_pct=2.0,
                quote_time=datetime.now(),
                market="SZ",
                source="fake",
            ),
            "515880": StockQuote(
                stock_code="515880",
                stock_name="通信ETF",
                latest_price=1.04,
                previous_close=1.0,
                change_pct=4.0,
                quote_time=datetime.now(),
                market="SH",
                source="fake",
            ),
        }
        return {code: quote for code, quote in data.items() if code in stock_codes and code not in self.missing}


class ThemedFundSource(FakeFundSource):
    async def get_profile(self, code: str) -> FundProfile:
        profile = await super().get_profile(code)
        profile.details = FundDetailInfo(asset_allocation=FundAssetAllocation(stock_pct=90.0))
        return profile


class CpoHoldingsSource(FakeHoldingsSource):
    async def get_holdings(self, code: str) -> FundHoldings:
        holdings = await super().get_holdings(code)
        holdings.items[0].stock_name = "长飞光纤"
        return holdings


class OfficialNavFundSource(FakeFundSource):
    async def get_profile(self, code: str) -> FundProfile:
        return FundProfile(
            code=code,
            name="测试基金",
            fund_type="混合型",
            nav_date=datetime.now(MARKET_TZ).date(),
            last_nav=10.5,
            previous_nav_date=datetime.now(MARKET_TZ).date() - timedelta(days=1),
            previous_nav=10.0,
            actual_change_pct=5.0,
            source="fake",
        )


class FixedDateOfficialNavFundSource(FakeFundSource):
    async def get_profile(self, code: str) -> FundProfile:
        return FundProfile(
            code=code,
            name="测试基金",
            fund_type="混合型",
            nav_date=date(2026, 5, 26),
            last_nav=9.6041,
            previous_nav_date=date(2026, 5, 25),
            previous_nav=9.8255,
            actual_change_pct=-2.25,
            source="fake",
        )


class ShouldNotBeCalledHoldingsSource(FakeHoldingsSource):
    async def get_holdings(self, code: str) -> FundHoldings:
        raise AssertionError("holdings should not be fetched when official NAV is current")


class ShouldNotBeCalledQuoteSource(FakeQuoteSource):
    async def get_quotes(self, stock_codes: list[str]) -> dict[str, StockQuote]:
        raise AssertionError("quotes should not be fetched when official NAV is current")


class EmptyHoldingsSource(FakeHoldingsSource):
    async def get_holdings(self, code: str) -> FundHoldings:
        return FundHoldings(fund_code=code, holdings_date=date.today(), source="fake", items=[])


class NoHoldingsSource(FakeHoldingsSource):
    async def get_holdings(self, code: str) -> FundHoldings:
        from fund_estimator.services.exceptions import AppError

        raise AppError("HOLDINGS_NOT_AVAILABLE", f"基金 {code} 没有可解析的前十大持仓", status_code=422)


def make_service(tmp_path, quote_source=None) -> FundEstimatorService:
    return FundEstimatorService(
        fund_source=FakeFundSource(),
        holdings_source=FakeHoldingsSource(),
        quote_source=quote_source or FakeQuoteSource(),
        cache=SQLiteCache(tmp_path / "test.sqlite3"),
        allow_mock_fallback=False,
    )


def test_raw_and_normalized_estimates(tmp_path):
    service = make_service(tmp_path)

    result = asyncio.run(service.estimate("123456", mode="both"))

    assert result.raw is not None
    assert result.normalized is not None
    assert result.raw.estimated_nav == 10.075
    assert result.raw.estimated_change_pct == 0.75
    assert result.normalized.estimated_nav == 10.1
    assert result.normalized.estimated_change_pct == 1.0
    assert result.primary_mode == "raw"
    assert result.estimated_nav == 10.075
    assert result.estimated_change_pct == 0.75
    assert result.official_nav == 10.0
    expected_official_date = FundEstimatorService._previous_trading_day(FundEstimatorService._current_estimate_date())
    assert result.official_nav_date == expected_official_date
    assert result.estimated_nav_date == FundEstimatorService._current_estimate_date()
    assert result.actual_change_pct == 1.0101
    assert result.actual_change_date == expected_official_date
    assert result.top10_weight_sum == 75.0
    assert result.usable_weight_sum == 75.0


def test_enhanced_estimate_uses_theme_proxy_for_residual_stock_position(tmp_path):
    service = FundEstimatorService(
        fund_source=ThemedFundSource(),
        holdings_source=CpoHoldingsSource(),
        quote_source=FakeQuoteSource(),
        cache=SQLiteCache(tmp_path / "enhanced.sqlite3"),
        allow_mock_fallback=False,
    )

    result = asyncio.run(service.estimate("123456", mode="both"))

    assert result.raw is not None
    assert result.enhanced is not None
    assert result.theme_proxy is not None
    assert result.theme_proxy.theme == "CPO/通信"
    assert result.theme_proxy.proxy_code == "515880"
    assert result.theme_proxy.weight_pct == 15.0
    assert result.raw.estimated_change_pct == 0.75
    assert result.enhanced.estimated_change_pct == 1.35
    assert result.primary_mode == "enhanced"
    assert result.estimated_change_pct == 1.35


def test_enhanced_estimate_is_skipped_when_theme_evidence_is_weak(tmp_path):
    service = FundEstimatorService(
        fund_source=ThemedFundSource(),
        holdings_source=FakeHoldingsSource(),
        quote_source=FakeQuoteSource(),
        cache=SQLiteCache(tmp_path / "weak-theme.sqlite3"),
        allow_mock_fallback=False,
    )

    result = asyncio.run(service.estimate("123456", mode="both"))

    assert result.theme_proxy is None
    assert result.enhanced is None
    assert result.primary_mode == "raw"
    assert result.estimated_change_pct == 0.75


def test_current_day_official_nav_uses_official_state(tmp_path):
    service = FundEstimatorService(
        fund_source=OfficialNavFundSource(),
        holdings_source=FakeHoldingsSource(),
        quote_source=FakeQuoteSource(),
        cache=SQLiteCache(tmp_path / "official.sqlite3"),
        allow_mock_fallback=False,
    )

    result = asyncio.run(service.estimate("123456", mode="both"))

    assert result.is_official_nav is True
    assert result.valuation_status == "official_nav"
    assert result.official_nav == 10.5
    assert result.official_nav_date == datetime.now(MARKET_TZ).date()
    assert result.actual_change_pct == 5.0
    assert result.actual_change_date == datetime.now(MARKET_TZ).date()
    assert result.estimated_nav == 10.075
    assert result.estimated_nav_date == FundEstimatorService._current_estimate_date()
    assert result.estimated_change_pct == 0.75
    assert result.raw is not None
    assert result.normalized is not None
    assert result.holdings
    assert result.holdings_date is not None


def test_official_nav_matching_estimate_date_keeps_comparison_estimate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        FundEstimatorService,
        "_current_estimate_date",
        staticmethod(lambda now=None: date(2026, 5, 26)),
    )
    service = FundEstimatorService(
        fund_source=FixedDateOfficialNavFundSource(),
        holdings_source=FakeHoldingsSource(),
        quote_source=FakeQuoteSource(),
        cache=SQLiteCache(tmp_path / "estimate-date-official.sqlite3"),
        allow_mock_fallback=False,
    )

    result = asyncio.run(service.estimate("001438", mode="both"))

    assert result.is_official_nav is True
    assert result.valuation_status == "official_nav"
    assert result.official_nav == 9.6041
    assert result.official_nav_date == date(2026, 5, 26)
    assert result.actual_change_pct == -2.25
    assert result.estimated_nav == 9.8992
    assert result.estimated_nav_date == date(2026, 5, 26)
    assert result.estimated_change_pct == 0.75
    assert result.raw is not None
    assert result.normalized is not None


def test_before_open_holds_official_state_with_comparison_estimate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        FundEstimatorService,
        "_current_estimate_date",
        staticmethod(lambda now=None: date(2026, 5, 26)),
    )
    service = FundEstimatorService(
        fund_source=FixedDateOfficialNavFundSource(),
        holdings_source=FakeHoldingsSource(),
        quote_source=FakeQuoteSource(),
        cache=SQLiteCache(tmp_path / "pre-open-official.sqlite3"),
        allow_mock_fallback=False,
    )

    result = asyncio.run(service.estimate("001438", mode="both"))

    assert result.is_official_nav is True
    assert result.valuation_status == "official_nav"
    assert result.official_nav_date == date(2026, 5, 26)
    assert result.estimated_nav == 9.8992
    assert result.estimated_nav_date == date(2026, 5, 26)
    assert result.estimated_change_pct == 0.75
    assert result.raw is not None


def test_official_nav_falls_back_when_comparison_estimate_unavailable(tmp_path):
    service = FundEstimatorService(
        fund_source=OfficialNavFundSource(),
        holdings_source=NoHoldingsSource(),
        quote_source=FakeQuoteSource(),
        cache=SQLiteCache(tmp_path / "official-fallback.sqlite3"),
        allow_mock_fallback=False,
    )

    result = asyncio.run(service.estimate("123456", mode="both"))

    assert result.is_official_nav is True
    assert result.valuation_status == "official_nav"
    assert result.official_nav == 10.5
    assert result.estimated_nav == 10.5
    assert result.estimated_change_pct is None
    assert result.raw is None
    assert result.normalized is None
    assert any("未生成预估复盘值" in warning for warning in result.warnings)


def test_missing_holdings_uses_exchange_traded_proxy_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr(
        FundEstimatorService,
        "_current_estimate_date",
        staticmethod(lambda now=None: date(2026, 5, 27)),
    )
    service = FundEstimatorService(
        fund_source=FixedDateOfficialNavFundSource(),
        holdings_source=NoHoldingsSource(),
        quote_source=FakeQuoteSource(),
        cache=SQLiteCache(tmp_path / "proxy.sqlite3"),
        allow_mock_fallback=False,
    )

    result = asyncio.run(service.estimate("003721", mode="both"))

    assert result.confidence == "low"
    assert result.estimated_change_pct == 2.0
    assert result.estimated_nav == 9.7962
    assert result.raw is not None
    assert result.raw.method == "proxy_exchange_traded_fund_return"
    assert result.normalized is not None
    assert result.normalized.method == "proxy_exchange_traded_fund_return"
    assert result.holdings[0].stock_code == "161128"
    assert "proxy_quote:fake" in result.data_source


def test_estimate_date_uses_previous_trading_day_before_open():
    before_open = datetime(2026, 5, 26, 9, 29, tzinfo=MARKET_TZ)
    after_open = datetime(2026, 5, 26, 9, 30, tzinfo=MARKET_TZ)
    monday_before_open = datetime(2026, 6, 1, 9, 0, tzinfo=MARKET_TZ)

    assert FundEstimatorService._current_estimate_date(before_open) == date(2026, 5, 25)
    assert FundEstimatorService._current_estimate_date(after_open) == date(2026, 5, 26)
    assert FundEstimatorService._current_estimate_date(monday_before_open) == date(2026, 5, 29)


def test_partial_missing_quote_is_reported(tmp_path):
    service = make_service(tmp_path, quote_source=FakeQuoteSource(missing={"000001"}))

    result = asyncio.run(service.estimate("123456", mode="both"))

    assert result.estimated_change_pct == 1.0
    assert result.primary_mode == "raw"
    assert result.usable_weight_sum == 50.0
    assert any("000001" in warning for warning in result.warnings)
    assert any(not item.used and item.stock_code == "000001" for item in result.holdings)


def test_all_quotes_missing_raises_502(tmp_path):
    service = make_service(tmp_path, quote_source=FakeQuoteSource(missing={"600000", "000001"}))

    with pytest.raises(DataSourceError) as exc_info:
        asyncio.run(service.estimate("123456", mode="both"))

    assert exc_info.value.code == "QUOTE_FETCH_FAILED"
    assert exc_info.value.status_code == 502
