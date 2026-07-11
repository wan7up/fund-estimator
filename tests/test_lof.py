from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import httpx

from fund_estimator.data_sources.sina import parse_sina_lof_quotes
from fund_estimator.models.lof import LofMarketQuote, LofOpportunityResponse, LofPremiumItem, LofTradingStatus
from fund_estimator.models.schema import FundProfile, FundSearchResult
from fund_estimator.services.cache import SQLiteCache
from fund_estimator.services.exceptions import AppError, DataSourceError
from fund_estimator.services.lof import (
    EastmoneyLofLatestNavDataSource,
    EastmoneyLofMarketDataSource,
    EastmoneyLofTradingStatusDataSource,
    LatestFundNav,
    LofMonitorService,
    YahooProxyDataSource,
)
from fund_estimator.services.lof_config import CORE_LOF_BY_CODE, infer_domestic_lof_proxy
from fund_estimator.services.lof_notice_scheduler import LofDailyNoticeScheduler
from fund_estimator.services.lof_notifications import EastmoneyNewIssueSource, LofNoticeConfig, LofNoticeService, NewIssueCalendar, NewIssueItem


class DummyEstimator:
    def __init__(self) -> None:
        self.cache = SQLiteCache(":memory:")

    async def search_funds(self, query: str):
        return [
            FundSearchResult(code="161128", name="易方达标普信息科技指数(QDII-LOF)A", fund_type="QDII", pinyin="YFD"),
            FundSearchResult(code="501046", name="财通多策略福鑫定开混合", fund_type="混合型-灵活", pinyin="CT"),
            FundSearchResult(code="001438", name="普通混合", fund_type="混合型", pinyin="PT"),
        ]

    async def get_profile(self, code: str) -> FundProfile:
        if code in {"501046", "501062"}:
            return FundProfile(
                code=code,
                name="南方瑞合定开混合(LOF)" if code == "501062" else "财通多策略福鑫定开混合",
                fund_type="混合型-灵活",
                nav_date=date(2026, 5, 29),
                last_nav=1.0,
                previous_nav_date=date(2026, 5, 28),
                previous_nav=0.99,
                actual_change_pct=1.0,
                source="mock",
            )
        return FundProfile(
            code=code,
            name=f"核心LOF{code}",
            fund_type="QDII-LOF",
            nav_date=date(2026, 5, 29),
            last_nav=1.0,
            previous_nav_date=date(2026, 5, 28),
            previous_nav=0.99,
            actual_change_pct=1.0,
            source="mock",
        )


class PartlyFailingEstimator(DummyEstimator):
    async def get_profile(self, code: str) -> FundProfile:
        if code == "161128":
            raise RuntimeError("profile temporarily unavailable")
        return await super().get_profile(code)


class DummyMarketSource:
    async def get_quotes(self, codes: list[str]):
        return {
            code: LofMarketQuote(
                code=code,
                name="瑞合LOF" if code == "501062" else "财通福鑫" if code == "501046" else "新机会LOF" if code == "160999" else f"核心LOF{code}",
                latest_price=1.08 if code == "160999" else 1.05 if code == "161128" else 1.01,
                previous_close=1.0,
                change_pct=5.0,
                turnover_yuan=6_000_000 if code == "160999" else 5_000_000 if code == "161128" else 100_000,
                quote_time=datetime(2026, 5, 30, 6, 0, tzinfo=UTC),
                market="SZ",
                source="mock",
            )
            for code in codes
        }

    async def get_all_quotes(self):
        return await self.get_quotes(["160999", "160998", "501046", "501062"])


class FailingMarketSource:
    async def get_quotes(self, codes: list[str]):
        raise DataSourceError("LOF_QUOTE_FETCH_FAILED", "LOF 场内实时行情获取失败")


class EmptyDiscoveryMarketSource(DummyMarketSource):
    async def get_all_quotes(self):
        return {}


class DummyStatusSource:
    async def get_status(self, code: str, profile: FundProfile):
        return LofTradingStatus(
            purchase_status="开放" if code == "161128" else "暂停",
            redemption_status="开放",
            daily_purchase_limit_yuan=10_000,
            source="mock",
        )


class DummyProxySource:
    def __init__(self) -> None:
        self.base_dates: list[date | None] = []

    async def get_changes(self, symbols: list[str], *, base_date: date | None = None):
        self.base_dates.append(base_date)
        return {symbol: 1.0 for symbol in symbols}


class DummyHaoEtfSource:
    async def get_snapshots(self, codes: list[str]):
        return {}


class DummyLatestNavSource:
    def __init__(self, rows: dict[str, LatestFundNav] | None = None) -> None:
        self.rows = rows or {}
        self.calls: list[str] = []

    async def get_latest_nav(self, code: str):
        self.calls.append(code)
        return self.rows.get(code)


class DummyNewIssueSource:
    def __init__(self, calendar: NewIssueCalendar) -> None:
        self.calendar = calendar
        self.calls: list[date] = []

    async def get_calendar(self, target_date: date) -> NewIssueCalendar:
        self.calls.append(target_date)
        return self.calendar


class PartialFailingEastmoneyNewIssueSource(EastmoneyNewIssueSource):
    async def _fetch_stocks(self, client, target_date: date) -> list[NewIssueItem]:
        raise httpx.ReadTimeout("stock timeout")

    async def _fetch_bonds(self, client, target_date: date) -> list[NewIssueItem]:
        return [NewIssueItem(kind="bond", code="113704", name="春风转债", apply_code="754129")]


class DummyNoticeMonitor:
    def __init__(self, response: LofOpportunityResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def get_opportunities(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.response


def make_service(tmp_path) -> LofMonitorService:
    estimator = DummyEstimator()
    estimator.cache = SQLiteCache(tmp_path / "lof.sqlite3")
    return LofMonitorService(
        estimator=estimator,
        cache=estimator.cache,
        market_source=DummyMarketSource(),
        status_source=DummyStatusSource(),
        latest_nav_source=DummyLatestNavSource(),
        proxy_source=DummyProxySource(),
        haoetf_source=DummyHaoEtfSource(),
    )


def seed_signal_history(config: LofNoticeConfig, items: list[LofPremiumItem], now: datetime) -> None:
    state = json.loads(config.state_path.read_text(encoding="utf-8")) if config.state_path.exists() else {}
    LofNoticeService(config)._record_signal_history(state, items, now=now)
    config.state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def test_eastmoney_latest_nav_parser_uses_first_history_row():
    text = """var apidata={ content:"<table><tbody>
    <tr><td>2026-06-22</td><td class='tor bold'>2.1546</td><td>2.1546</td></tr>
    <tr><td>2026-06-18</td><td class='tor bold'>2.0816</td><td>2.0816</td></tr>
    </tbody></table>"};"""

    latest_nav = EastmoneyLofLatestNavDataSource._parse_latest_nav(text)

    assert latest_nav == LatestFundNav(nav_date=date(2026, 6, 22), nav=2.1546)


def test_eastmoney_lof_quote_uses_previous_close_when_latest_missing_preopen():
    quote = EastmoneyLofMarketDataSource._quote_from_row(
        {
            "f12": "501096",
            "f13": "1",
            "f14": "国联安科创LOF",
            "f2": "-",
            "f3": "-",
            "f18": "2.386",
            "f6": "-",
            "f8": "0.0",
        },
        quote_time=datetime(2026, 6, 23, 0, 58, tzinfo=UTC),
    )

    assert quote is not None
    assert quote.latest_price == 2.386
    assert quote.previous_close == 2.386
    assert quote.change_pct is None


def test_domestic_proxy_fetch_failure_does_not_abort_scan():
    source = YahooProxyDataSource()

    async def fail(*args, **kwargs):
        raise httpx.ConnectError("temporary gateway failure")

    source._get_sina_changes = fail

    result = __import__("asyncio").run(source.get_changes(["SINA:sh588000"]))

    assert result == {}


def test_sina_domestic_proxy_change_parser():
    source = YahooProxyDataSource()
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://hq.sinajs.cn/list=sh588000"),
        content='var hq_str_sh588000="科创50ETF华夏,2.220,2.200,2.244,2.250,2.100,2.243,2.244,1,1.000,2026-07-02,15:00:00,00,";'.encode(
            "gbk"
        ),
    )

    class Client:
        async def get(self, *args, **kwargs):
            return response

    result = __import__("asyncio").run(source._get_sina_changes(Client(), ["SINA:sh588000"]))

    assert round(result["SINA:sh588000"], 6) == 2.0


def test_domestic_proxy_requires_index_fund_wording():
    assert infer_domestic_lof_proxy("国联安科创混合(LOF)", "混合型-偏股") is None

    rule = infer_domestic_lof_proxy("国联安科创50指数(LOF)", "指数型-股票")

    assert rule is not None
    assert rule.theme == "科创/创新主题"
    assert rule.proxies[0].symbol == "SINA:sh588000"


def test_lof_scan_deep_profiles_full_discovery_when_preopen_turnover_missing():
    quotes = {
        str(index): LofMarketQuote(
            code=f"16{index:04d}"[:6],
            name="测试LOF",
            latest_price=1.0,
            previous_close=1.0,
            change_pct=None,
            turnover_yuan=None,
            quote_time=datetime(2026, 6, 23, 0, 58, tzinfo=UTC),
            market="SZ",
            source="mock",
        )
        for index in range(120)
    }

    assert LofMonitorService._deep_profile_limit(quotes) == 120


def test_lof_item_refreshes_stale_profile_nav_from_latest_nav_source(tmp_path):
    estimator = DummyEstimator()
    estimator.cache = SQLiteCache(tmp_path / "lof.sqlite3")
    service = LofMonitorService(
        estimator=estimator,
        cache=estimator.cache,
        market_source=DummyMarketSource(),
        status_source=DummyStatusSource(),
        latest_nav_source=DummyLatestNavSource({"161128": LatestFundNav(date(2026, 6, 22), 1.02)}),
        proxy_source=DummyProxySource(),
        haoetf_source=DummyHaoEtfSource(),
    )

    item = __import__("asyncio").run(service.get_item("161128"))

    assert item.official_nav_date == "2026-06-22"
    assert item.official_nav == 1.02
    assert item.official_premium_pct == 2.9412


def test_lof_opportunity_uses_proxy_estimate_and_flags_risks(tmp_path):
    service = make_service(tmp_path)

    response = __import__("asyncio").run(service.get_opportunities(limit=3))
    item = next(row for row in response.items if row.code == "161128")

    assert item.estimated_nav == 1.01
    assert item.estimated_premium_pct == 3.9604
    assert item.official_premium_pct == 5.0
    assert item.reference_change_pct == 1.0
    assert item.reference_period_start == "2026-05-29"
    assert service.proxy_source.base_dates == [date(2026, 5, 29)]
    assert item.direction == "premium"
    assert item.level == "normal"
    assert item.actionable is True
    assert "QDII/跨市场时间差" in item.risks


def test_non_qdii_official_discount_waits_for_cross_day_confirmation(tmp_path):
    service = make_service(tmp_path)
    now = datetime(2026, 6, 8, 6, 30, tzinfo=UTC)
    profile = FundProfile(
        code="161005",
        name="富国天惠成长混合(LOF)A",
        fund_type="混合型-偏股",
        nav_date=date(2026, 6, 5),
        last_nav=1.0,
        previous_nav_date=date(2026, 6, 4),
        previous_nav=1.0,
        actual_change_pct=0.0,
        source="mock",
    )
    quote = LofMarketQuote(
        code="161005",
        name="富国天惠",
        latest_price=0.96,
        previous_close=1.0,
        change_pct=-2.0,
        turnover_yuan=5_000_000,
        turnover_rate_pct=0.86,
        quote_time=now,
        market="SZ",
        source="mock",
    )
    status = LofTradingStatus(
        purchase_status="unknown",
        redemption_status="开放",
        daily_purchase_limit_yuan=20_000,
        fee_rate_pct=0.15,
        source="mock",
    )

    item = service._build_item(
        code="161005",
        profile=profile,
        quote=quote,
        status=status,
        haoetf_snapshot=None,
        proxy_changes={},
        normal_threshold_pct=2.0,
        strong_threshold_pct=5.0,
        min_turnover_yuan=3_000_000,
        cooldown_keys=set(),
        signal_history={},
        now=now,
    )

    assert item.is_qdii is False
    assert item.estimated_premium_pct is None
    assert item.official_premium_pct == -4.0
    assert item.signal_basis == "official"
    assert item.direction == "discount"
    assert item.is_opportunity is True
    assert item.exchange_turnover_rate_pct == 0.86
    assert item.actionable is False
    assert "换手率不足10%" in item.risks
    assert "非QDII官方折价候选，等待跨日确认" in item.risks

    low_turnover_confirmed = service._build_item(
        code="161005",
        profile=profile,
        quote=quote,
        status=status,
        haoetf_snapshot=None,
        proxy_changes={},
        normal_threshold_pct=2.0,
        strong_threshold_pct=5.0,
        min_turnover_yuan=3_000_000,
        cooldown_keys=set(),
        signal_history={"2026-06-07": {"items": {"161005": {"direction": "discount"}}}},
        now=now,
    )

    assert low_turnover_confirmed.actionable is False
    assert "换手率不足10%" in low_turnover_confirmed.risks
    assert "非QDII官方折价候选，等待跨日确认" not in low_turnover_confirmed.risks

    active_quote = quote.model_copy(update={"turnover_rate_pct": 12.0})
    confirmed = service._build_item(
        code="161005",
        profile=profile,
        quote=active_quote,
        status=status,
        haoetf_snapshot=None,
        proxy_changes={},
        normal_threshold_pct=2.0,
        strong_threshold_pct=5.0,
        min_turnover_yuan=3_000_000,
        cooldown_keys=set(),
        signal_history={"2026-06-07": {"items": {"161005": {"direction": "discount"}}}},
        now=now,
    )

    assert confirmed.exchange_turnover_rate_pct == 12.0
    assert confirmed.actionable is True
    assert "换手率不足10%" not in confirmed.risks
    assert "非QDII官方折价候选，等待跨日确认" not in confirmed.risks

    neutral_quote = quote.model_copy(update={'latest_price': 1.0164})
    neutral = service._build_item(
        code='161005',
        profile=profile,
        quote=neutral_quote,
        status=status,
        haoetf_snapshot=None,
        proxy_changes={},
        normal_threshold_pct=2.0,
        strong_threshold_pct=5.0,
        min_turnover_yuan=3_000_000,
        cooldown_keys=set(),
        signal_history={},
        now=now,
    )

    assert neutral.direction == 'neutral'
    assert neutral.is_opportunity is False
    assert '换手率不足10%' not in neutral.risks
    assert '非QDII官方信号，等待跨日确认' not in neutral.risks


def test_shanghai_active_lof_discount_without_tday_nav_is_not_actionable(tmp_path):
    service = make_service(tmp_path)
    now = datetime(2026, 7, 2, 6, 30, tzinfo=UTC)
    profile = FundProfile(
        code="501096",
        name="国联安科创混合(LOF)",
        fund_type="混合型-偏股",
        nav_date=date(2026, 7, 1),
        last_nav=1.0,
        previous_nav_date=date(2026, 6, 30),
        previous_nav=1.0,
        actual_change_pct=0.0,
        source="mock",
    )
    quote = LofMarketQuote(
        code="501096",
        name="国联安科创LOF",
        latest_price=0.91,
        previous_close=1.0,
        change_pct=-8.0,
        turnover_yuan=20_000_000,
        turnover_rate_pct=14.0,
        quote_time=now,
        market="SH",
        source="mock",
    )
    status = LofTradingStatus(
        purchase_status="暂停",
        redemption_status="开放",
        daily_purchase_limit_yuan=None,
        fee_rate_pct=0.15,
        source="mock",
    )

    item = service._build_item(
        code="501096",
        profile=profile,
        quote=quote,
        status=status,
        haoetf_snapshot=None,
        proxy_changes={("SINA:sh588000", "2026-07-01"): 1.0},
        normal_threshold_pct=2.0,
        strong_threshold_pct=5.0,
        min_turnover_yuan=3_000_000,
        cooldown_keys=set(),
        signal_history={},
        now=now,
    )

    assert item.direction == "discount"
    assert item.estimated_nav is None
    assert item.estimated_premium_pct is None
    assert item.official_premium_pct == -9.0
    assert item.actionable is False
    assert "非QDII官方折价候选，等待跨日确认" not in item.risks
    assert "申购暂停" not in item.risks
    assert "T日估算净值缺失，沪市LOF折价不可直接操作" in item.risks


def test_shanghai_index_lof_discount_with_tday_nav_is_actionable(tmp_path):
    service = make_service(tmp_path)
    now = datetime(2026, 7, 2, 6, 30, tzinfo=UTC)
    profile = FundProfile(
        code="501096",
        name="国联安科创50指数(LOF)",
        fund_type="指数型-股票",
        nav_date=date(2026, 7, 1),
        last_nav=1.0,
        previous_nav_date=date(2026, 6, 30),
        previous_nav=1.0,
        actual_change_pct=0.0,
        source="mock",
    )
    quote = LofMarketQuote(
        code="501096",
        name="国联安科创50指数LOF",
        latest_price=0.91,
        previous_close=1.0,
        change_pct=-8.0,
        turnover_yuan=20_000_000,
        turnover_rate_pct=14.0,
        quote_time=now,
        market="SH",
        source="mock",
    )
    status = LofTradingStatus(
        purchase_status="暂停",
        redemption_status="开放",
        daily_purchase_limit_yuan=None,
        fee_rate_pct=0.15,
        source="mock",
    )

    item = service._build_item(
        code="501096",
        profile=profile,
        quote=quote,
        status=status,
        haoetf_snapshot=None,
        proxy_changes={("SINA:sh588000", "2026-07-01"): 1.0},
        normal_threshold_pct=2.0,
        strong_threshold_pct=5.0,
        min_turnover_yuan=3_000_000,
        cooldown_keys=set(),
        signal_history={},
        now=now,
    )

    assert item.direction == "discount"
    assert item.estimated_nav == 1.01
    assert item.estimated_premium_pct == -9.901
    assert item.official_premium_pct == -9.0
    assert item.actionable is True
    assert "沪市LOF折价T日可赎回，需确认当日估算净值" in item.risks


def test_high_single_day_official_premium_skips_cross_day_confirmation(tmp_path):
    service = make_service(tmp_path)
    now = datetime(2026, 7, 2, 6, 30, tzinfo=UTC)
    profile = FundProfile(
        code="166011",
        name="中欧盛世成长混合(LOF)A",
        fund_type="混合型-偏股",
        nav_date=date(2026, 7, 1),
        last_nav=1.0,
        previous_nav_date=date(2026, 6, 30),
        previous_nav=1.0,
        actual_change_pct=0.0,
        source="mock",
    )
    quote = LofMarketQuote(
        code="166011",
        name="中欧盛世LOF",
        latest_price=1.09,
        previous_close=1.0,
        change_pct=9.0,
        turnover_yuan=12_000_000,
        turnover_rate_pct=15.0,
        quote_time=now,
        market="SZ",
        source="mock",
    )
    status = LofTradingStatus(
        purchase_status="开放",
        redemption_status="开放",
        daily_purchase_limit_yuan=None,
        fee_rate_pct=0.15,
        source="mock",
    )

    item = service._build_item(
        code="166011",
        profile=profile,
        quote=quote,
        status=status,
        haoetf_snapshot=None,
        proxy_changes={},
        normal_threshold_pct=2.0,
        strong_threshold_pct=5.0,
        min_turnover_yuan=3_000_000,
        cooldown_keys=set(),
        signal_history={},
        now=now,
    )

    assert item.direction == "premium"
    assert item.official_premium_pct == 9.0
    assert item.actionable is True
    assert "非QDII官方溢价候选，等待跨日确认" not in item.risks
    assert "单日高折溢价信号，需确认当日估算净值" in item.risks


def test_lof_watchlist_is_device_scoped(tmp_path):
    service = make_service(tmp_path)

    added = __import__("asyncio").run(service.add_watchlist("161128", "phone-a"))

    assert added.code == "161128"
    assert [item.code for item in service.list_watchlist("phone-a")] == ["161128"]
    assert service.list_watchlist("phone-b") == []


def test_lof_search_filters_non_lof(tmp_path):
    service = make_service(tmp_path)

    results = __import__("asyncio").run(service.search_lofs("1"))

    assert [item.code for item in results] == ["161128"]


def test_lof_watchlist_rejects_closed_end_fund(tmp_path):
    service = make_service(tmp_path)

    try:
        __import__("asyncio").run(service.add_watchlist("501046", "phone-a"))
    except AppError as exc:
        assert exc.code == "NOT_LOF_FUND"
    else:
        raise AssertionError("closed-end fund should not be accepted as a LOF watch item")


def test_hot_hong_kong_us_internet_lof_is_in_core_pool():
    item = CORE_LOF_BY_CODE["160644"]

    assert item.theme == "Hong Kong and US internet"
    assert {leg.symbol for leg in item.proxies} >= {"KWEB", "NQ=F", "^HSI"}


def test_overseas_technology_lof_is_in_core_pool():
    item = CORE_LOF_BY_CODE["501312"]

    assert item.theme == "Overseas technology"
    assert {leg.symbol for leg in item.proxies} == {"NQ=F"}


def test_lof_scan_discovers_non_core_premium_from_full_market_quotes(tmp_path):
    service = make_service(tmp_path)

    response = __import__("asyncio").run(service.get_opportunities(limit=200))
    item = next(row for row in response.items if row.code == "160999")

    assert item.name == "核心LOF160999"
    assert item.official_premium_pct == 8.0
    assert item.signal_basis == "official"
    assert item.is_opportunity is False
    assert item.level == "none"
    assert item.actionable is False


def test_lof_scan_uses_expired_discovery_quotes_when_market_preopen_empty(tmp_path):
    service = make_service(tmp_path)
    stale_quote = LofMarketQuote(
        code="160999",
        name="新机会LOF",
        latest_price=1.08,
        previous_close=1.0,
        change_pct=5.0,
        turnover_yuan=6_000_000,
        quote_time=datetime(2026, 5, 30, 6, 0, tzinfo=UTC),
        market="SZ",
        source="mock",
    )
    service.cache.set(
        "lof_discovery_quotes",
        "all",
        {"quotes": [stale_quote.model_dump(mode="json")]},
        -1,
    )
    service.discovery_market_source = EmptyDiscoveryMarketSource()

    response = __import__("asyncio").run(service.get_opportunities(limit=200))
    item = next(row for row in response.items if row.code == "160999")

    assert item.name == "核心LOF160999"
    assert item.exchange_price == 1.08
    assert "全市场 LOF 行情为空，已使用过期发现池缓存" in response.errors


def test_lof_scan_refresh_false_falls_back_to_live_scan_on_cache_miss(tmp_path):
    service = make_service(tmp_path)

    response = __import__("asyncio").run(service.get_opportunities(limit=20, refresh=False))

    assert response.items
    assert not response.errors


def test_lof_scan_cache_key_normalizes_integer_turnover(tmp_path):
    service = make_service(tmp_path)

    __import__("asyncio").run(service.get_opportunities(limit=20, min_turnover_yuan=3_000_000.0))

    assert service.cache.get("lof_opportunity_scan", "v7:default:2.0:5.0:3000000", include_expired=True) is not None
    assert service.cache.get("lof_opportunity_scan", "v7:default:2.0:5.0:3000000.0", include_expired=True) is None


def test_lof_scan_includes_non_core_lof_below_opportunity_threshold(tmp_path):
    service = make_service(tmp_path)

    response = __import__("asyncio").run(service.get_opportunities(limit=200))
    item = next(row for row in response.items if row.code == "160998")

    assert item.official_premium_pct == 1.0
    assert item.is_opportunity is False
    assert item.actionable is False
    assert item.exchange_turnover_yuan == 100_000


def test_lof_scan_excludes_closed_end_funds_from_display_pool(tmp_path):
    service = make_service(tmp_path)

    response = __import__("asyncio").run(service.get_opportunities(limit=200))

    codes = {row.code for row in response.items}
    assert "501046" not in codes
    assert "501062" not in codes


def test_eastmoney_lof_quote_parses_turnover_rate():
    quote = EastmoneyLofMarketDataSource._quote_from_row(
        {
            "f12": "161005",
            "f13": "0",
            "f14": "富国天惠LOF",
            "f2": 3.121,
            "f3": 2.6,
            "f18": 3.043,
            "f6": 9_910_027.175,
            "f8": 0.86,
        },
        quote_time=datetime(2026, 6, 9, 6, 30, tzinfo=UTC),
    )

    assert quote is not None
    assert quote.code == "161005"
    assert quote.turnover_yuan == 9_910_027.175
    assert quote.turnover_rate_pct == 0.86


def test_sina_lof_quote_parses_hot_hong_kong_us_internet_quote():
    text = (
        'var hq_str_sz160644="互联网QD,2.322,2.323,2.212,2.344,2.175,2.212,2.213,'
        '999707214,2266415078.910,696772,2.212,121500,2.211,189690,2.210,66800,2.209,'
        '140700,2.208,90739,2.213,20934,2.214,435298,2.215,23310,2.216,21398,2.217,'
        '2026-05-29,15:00:00,00";'
    )

    quote = parse_sina_lof_quotes(text)["160644"]

    assert quote.name == "互联网QD"
    assert quote.latest_price == 2.212
    assert quote.previous_close == 2.323
    assert quote.change_pct == -4.7783
    assert quote.turnover_yuan == 2266415078.91
    assert quote.market == "SZ"
    assert quote.source == "sina"


def test_eastmoney_trading_status_prefers_transaction_row_over_nav_link():
    html = """
    <a>申购状态</a>
    <p class="row"><label>交易状态：
      <span>暂停申购</span>
      <span>（<span>单日累计购买上限1.00万元</span>）</span>
      <span>开放赎回</span>
    </label></p>
    """
    profile = __import__("asyncio").run(DummyEstimator().get_profile("160644"))

    status = EastmoneyLofTradingStatusDataSource()._parse_status(html, profile)

    assert status.purchase_status == "暂停"
    assert status.redemption_status == "开放"
    assert status.daily_purchase_limit_yuan == 10_000


def test_eastmoney_trading_status_parses_short_large_purchase_limit():
    html = """
    <p>交易状态：限大额（单日累计购买上限100元）开放赎回</p>
    <p>申购起点10.00元日累计申购限额100.00元首次购买10.00元</p>
    """
    profile = __import__("asyncio").run(DummyEstimator().get_profile("160644"))

    status = EastmoneyLofTradingStatusDataSource()._parse_status(html, profile)

    assert status.purchase_status == "限制大额"
    assert status.redemption_status == "开放"
    assert status.daily_purchase_limit_yuan == 100


def test_eastmoney_lof_limit_parser_ignores_unlimited_with_minimum_purchase():
    text = "申购起点10.00元定投起点10.00元日累计申购限额无限额首次购买10.00元追加购买10.00元"

    assert EastmoneyLofTradingStatusDataSource._extract_limit_yuan(text) is None


def test_eastmoney_lof_limit_parser_keeps_real_daily_limit():
    text = "交易状态开放申购开放赎回申购与赎回金额申购起点10.00元单日累计购买上限1000元首次购买10.00元"

    assert EastmoneyLofTradingStatusDataSource._extract_limit_yuan(text) == 1000


def test_eastmoney_lof_limit_parser_keeps_reverse_daily_limit():
    text = "交易状态开放申购开放赎回每个账户1000元单日申购限额"

    assert EastmoneyLofTradingStatusDataSource._extract_limit_yuan(text) == 1000


def test_lof_scan_keeps_core_rows_when_profile_fails(tmp_path):
    estimator = PartlyFailingEstimator()
    estimator.cache = SQLiteCache(tmp_path / "lof.sqlite3")
    service = LofMonitorService(
        estimator=estimator,
        cache=estimator.cache,
        market_source=DummyMarketSource(),
        status_source=DummyStatusSource(),
        latest_nav_source=DummyLatestNavSource(),
        proxy_source=DummyProxySource(),
        haoetf_source=DummyHaoEtfSource(),
    )

    response = __import__("asyncio").run(service.get_opportunities(limit=3))
    item = next(row for row in response.items if row.code == "161128")

    assert item.name == "核心LOF161128"
    assert item.is_qdii is True
    assert item.exchange_price == 1.05
    assert item.estimated_premium_pct is None
    assert "基金资料缺失" in item.risks


def test_lof_scan_uses_stale_quotes_when_market_source_fails(tmp_path):
    service = make_service(tmp_path)
    service.cache.set(
        "lof_market_quote",
        "161128",
        LofMarketQuote(
            code="161128",
            name="核心LOF161128",
            latest_price=1.05,
            previous_close=1.0,
            change_pct=5.0,
            turnover_yuan=5_000_000,
            quote_time=datetime(2026, 5, 30, 6, 0, tzinfo=UTC),
            market="SZ",
            source="stale-test",
        ).model_dump(mode="json"),
        0,
    )
    service.market_source = FailingMarketSource()

    response = __import__("asyncio").run(service.get_opportunities(limit=3))
    item = next(row for row in response.items if row.code == "161128")

    assert item.exchange_price == 1.05
    assert item.exchange_turnover_yuan == 5_000_000
    assert "已使用缓存" in response.errors[0]


def test_lof_notice_cooldown_dedupes(tmp_path):
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        timeout_seconds=5,
        notice_dir=tmp_path,
    )
    notice = LofNoticeService(config)
    state = {
        "cooldowns": {
            "161128:premium:normal": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(timespec="seconds")
        }
    }
    notice.config.state_path.write_text(__import__("json").dumps(state), encoding="utf-8")

    assert notice.active_cooldown_keys() == {"161128:premium:normal"}


def test_lof_notice_feishu_qr_connect_stores_app_and_user_target(tmp_path):
    config = LofNoticeConfig(
        enabled=True,
        timeout_seconds=5,
        notice_dir=tmp_path,
    )
    notice = LofNoticeService(config)
    responses = [
        {"supported_auth_methods": ["client_secret"]},
        {
            "verification_uri_complete": "https://accounts.feishu.cn/device/scan?code=abc",
            "device_code": "device-unit",
            "interval": 2,
            "expire_in": 600,
        },
        {
            "client_id": "cli_unit",
            "client_secret": "secret_unit",
            "user_info": {"open_id": "ou_unit", "name": "Unit User", "tenant_brand": "feishu"},
        },
    ]

    def fake_registration_post(base_url, form_body):
        return responses.pop(0)

    notice._feishu_registration_post = fake_registration_post  # type: ignore[method-assign]
    connect = notice.begin_feishu_connect(callback_url="https://example.test/api/lof/notice/feishu/callback")

    assert connect.configured is True
    assert connect.status == "pending"
    assert connect.qr_url == "https://accounts.feishu.cn/device/scan?code=abc&from=onboard"

    poll = notice.poll_feishu_connect()
    status = notice.status()

    assert poll.status == "connected"
    assert status.app_configured is True
    assert status.connected is True
    assert status.target_set is True
    assert status.target_kind == "user"
    assert status.target_name == "Unit User"
    stored = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert stored["feishu_app"]["app_id"] == "cli_unit"
    assert stored["feishu_app"]["app_secret"] == "secret_unit"
    assert stored["feishu"]["open_id"] == "ou_unit"
    assert "feishu_onboard" not in stored


def test_lof_notice_daily_summary_sends_once_per_day(tmp_path):
    service = make_service(tmp_path)
    response = __import__("asyncio").run(service.get_opportunities(limit=20))
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        timeout_seconds=5,
        notice_dir=tmp_path,
        daily_summary_time="10:00",
        send_empty_daily_summary=True,
    )
    notice = LofNoticeService(config)
    sent_texts: list[str] = []
    notice._send_feishu_openapi = lambda text, *, state: sent_texts.append(text) or {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]
    now = datetime(2026, 5, 29, 2, 5, tzinfo=UTC)
    seed_signal_history(config, notice._raw_notice_candidates(response), now - timedelta(days=1))

    first = notice.notify_daily_summary(response, now=now)
    second = notice.notify_daily_summary(response, now=now + timedelta(minutes=5))

    assert first["status"] == "sent"
    assert second["status"] == "skipped_duplicate_daily_summary"
    assert len(sent_texts) == 1
    assert sent_texts[0].startswith("【LOF套利机会提醒】\n2026-05-29 10:05")
    assert "161128" in sent_texts[0]
    assert "操作建议：" in sent_texts[0]
    assert "成交额：" in sent_texts[0]
    assert "估算折溢价：" in sent_texts[0]
    assert "官方净值折溢价：" in sent_texts[0]
    assert "申购限额" in sent_texts[0]
    state = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert state["last_daily_summary_date"] == "2026-05-29"


def test_lof_notice_empty_daily_summary_uses_compact_reason_template(tmp_path):
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        timeout_seconds=5,
        notice_dir=tmp_path,
        daily_summary_time="09:30",
        send_empty_daily_summary=True,
    )
    notice = LofNoticeService(config)
    sent_texts: list[str] = []
    notice._send_feishu_openapi = lambda text, *, state: sent_texts.append(text) or {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]
    now = datetime(2026, 6, 4, 1, 30, tzinfo=UTC)
    items = [
        LofPremiumItem(
            code=f"16{i:04d}",
            name=f"测试LOF{i}",
            estimated_premium_pct=1.0,
            official_premium_pct=1.0,
            exchange_turnover_yuan=5_000_000,
            purchase_status="开放",
            redemption_status="开放",
            daily_purchase_limit_yuan=10_000,
            direction="neutral",
            level="none",
            updated_at=now,
        )
        for i in range(120)
    ]
    response = LofOpportunityResponse(
        scanned_at=now,
        normal_threshold_pct=2.0,
        strong_threshold_pct=5.0,
        min_turnover_yuan=3_000_000,
        core_count=0,
        watchlist_count=0,
        items=items,
    )

    result = notice.notify_daily_summary(response, now=now)

    assert result["status"] == "sent"
    assert sent_texts == [
        "\n".join(
            [
                "【LOF套利机会提醒】",
                "2026-06-04 09:30",
                "当前暂无可操作套利机会。",
                "扫描池：120只",
                "原因：无发现沪市可赎回折价或跨扫描日持续折溢价超过3%且成交额超过300万",
            ]
        )
    ]


def test_lof_notice_filters_abs_premium_by_purchase_status_and_turnover(tmp_path):
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        timeout_seconds=5,
        notice_dir=tmp_path,
        daily_summary_time="10:00",
        send_empty_daily_summary=False,
    )
    notice = LofNoticeService(config)
    sent_texts: list[str] = []
    notice._send_feishu_openapi = lambda text, *, state: sent_texts.append(text) or {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]
    now = datetime(2026, 6, 3, 2, 4, tzinfo=UTC)

    def item(code: str, premium: float, turnover: float, purchase_status: str = "开放") -> LofPremiumItem:
        return LofPremiumItem(
            code=code,
            name=f"测试LOF{code}",
            estimated_premium_pct=premium,
            official_premium_pct=premium,
            exchange_turnover_yuan=turnover,
            purchase_status=purchase_status,
            redemption_status="开放",
            daily_purchase_limit_yuan=10_000,
            direction="neutral",
            level="none",
            updated_at=now,
        )

    response = LofOpportunityResponse(
        scanned_at=now,
        normal_threshold_pct=2.0,
        strong_threshold_pct=5.0,
        min_turnover_yuan=3_000_000,
        core_count=0,
        watchlist_count=0,
        items=[
            item("160001", 3.2, 5_000_000),
            item("160002", -4.5, 8_000_000),
            item("160003", 5.0, 9_000_000, "暂停"),
            item("160004", 4.0, 100_000),
            item("160005", 3.0, 10_000_000),
            item("160006", 5.0, 20_000_000, "unknown"),
            item("160007", 3.1, 4_000_000, "限制大额"),
            item("160008", -3.2, 3_000_000),
        ],
    )
    seed_signal_history(config, notice._raw_notice_candidates(response), now - timedelta(days=1))

    result = notice.notify_daily_summary(response, now=now)

    assert result["status"] == "sent"
    assert len(sent_texts) == 1
    assert "160006 测试LOF160006" in sent_texts[0]
    assert "160002 测试LOF160002" in sent_texts[0]
    assert "160001 测试LOF160001" in sent_texts[0]
    assert "160007 测试LOF160007" in sent_texts[0]
    assert "160003" not in sent_texts[0]
    assert "160004" not in sent_texts[0]
    assert "160005" not in sent_texts[0]
    assert "160008" not in sent_texts[0]
    assert "成交额：800万；换手率：--；估算折溢价：-4.50%" in sent_texts[0]
    assert "操作建议：深市/非沪市LOF折价超过3%，成交额达标；通常需次日赎回，先核实申赎规则、费用、到账时间和次日净值波动风险。" in sent_texts[0]
    assert "操作建议：溢价超过3%，成交额达标；申购状态未明确暂停，先核实开放和限额。" in sent_texts[0]
    rows = [json.loads(line) for line in config.ledger_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["scan_items"] == 8
    assert rows[0]["scanned_at"] == "2026-06-03T02:04:00+00:00"
    assert rows[0]["raw_candidate_count"] == 4
    assert rows[0]["raw_candidate_codes"] == ["160006", "160002", "160001", "160007"]
    assert rows[0]["candidate_codes"] == ["160006", "160002", "160001", "160007"]


def test_lof_notice_prioritizes_shanghai_discount_with_tday_estimate(tmp_path):
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        timeout_seconds=5,
        notice_dir=tmp_path,
        daily_summary_time="10:00",
        send_empty_daily_summary=False,
    )
    notice = LofNoticeService(config)
    sent_texts: list[str] = []
    notice._send_feishu_openapi = lambda text, *, state: sent_texts.append(text) or {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]
    now = datetime(2026, 7, 2, 6, 30, tzinfo=UTC)

    def item(
        code: str,
        premium: float,
        *,
        estimated_premium: float | None = None,
        purchase_status: str = "开放",
        redemption_status: str = "开放",
    ) -> LofPremiumItem:
        return LofPremiumItem(
            code=code,
            name=f"测试LOF{code}",
            estimated_premium_pct=estimated_premium,
            official_premium_pct=premium,
            signal_basis="estimated" if estimated_premium is not None else "official",
            exchange_turnover_yuan=8_000_000,
            exchange_turnover_rate_pct=12.0,
            purchase_status=purchase_status,
            redemption_status=redemption_status,
            direction="discount",
            level="strong",
            updated_at=now,
        )

    response = LofOpportunityResponse(
        scanned_at=now,
        normal_threshold_pct=2.0,
        strong_threshold_pct=5.0,
        min_turnover_yuan=3_000_000,
        core_count=0,
        watchlist_count=0,
        items=[
            item("501096", -8.8, estimated_premium=-8.8, purchase_status="暂停"),
            item("501099", -7.2, redemption_status="暂停"),
            item("166011", 8.5),
            item("160324", -7.5),
            item("501088", -9.1),
        ],
    )

    result = notice.notify_afternoon_check(response, now=now)

    assert result["status"] == "sent"
    assert len(sent_texts) == 1
    assert "501096 测试LOF501096" in sent_texts[0]
    assert "166011 测试LOF166011" in sent_texts[0]
    assert "501099" not in sent_texts[0]
    assert "501088" not in sent_texts[0]
    assert "160324" not in sent_texts[0]
    assert "操作建议：沪市LOF折价超过3%，成交额达标；可重点关注T日场内买入并当日提交赎回，务必在15:00前确认券商支持场内赎回、赎回开放、费用和T日估算净值。" in sent_texts[0]
    assert "操作建议：单日溢价超过8%，成交额达标；可首日提醒，但申购套利仍有转场内时间差，优先确认申购开放、限额、费用和溢价持续性。" in sent_texts[0]


def test_daily_summary_schedule_skips_after_same_day_send(tmp_path):
    config = LofNoticeConfig(
        app_id="app",
        app_secret="secret",
        notice_dir=tmp_path,
        daily_summary_time="10:00",
    )
    notice = LofNoticeService(config)
    state = {
        "settings": {"daily_summary_time": "10:00"},
        "last_daily_summary_date": "2026-06-03",
    }
    config.state_path.write_text(json.dumps(state), encoding="utf-8")

    assert notice.should_run_daily_summary(datetime(2026, 6, 3, 2, 30, tzinfo=UTC)) is False


def test_lof_notice_scheduler_uses_configured_daily_time(tmp_path):
    config = LofNoticeConfig(
        app_id="app",
        app_secret="secret",
        notice_dir=tmp_path,
        daily_summary_time="10:00",
    )
    notice = LofNoticeService(config)
    scheduler = LofDailyNoticeScheduler(monitor=object(), notice=notice)

    before = scheduler.seconds_until_next_run(datetime(2026, 6, 3, 1, 55, tzinfo=UTC))
    due = scheduler.seconds_until_next_run(datetime(2026, 6, 3, 2, 0, tzinfo=UTC))
    state = {
        "settings": {"daily_summary_time": "10:00"},
        "last_daily_summary_date": "2026-06-03",
    }
    config.state_path.write_text(json.dumps(state), encoding="utf-8")
    after_sent = scheduler.seconds_until_next_run(datetime(2026, 6, 3, 2, 5, tzinfo=UTC))

    assert before == 300
    assert due == 0
    assert after_sent == 4 * 60 * 60 + 25 * 60


def test_lof_notice_scheduler_sends_new_issue_after_daily_summary(tmp_path):
    now = datetime(2026, 6, 3, 2, 0, tzinfo=UTC)
    response = LofOpportunityResponse(
        scanned_at=now,
        normal_threshold_pct=2.0,
        strong_threshold_pct=5.0,
        min_turnover_yuan=3_000_000,
        core_count=0,
        watchlist_count=0,
        items=[],
    )
    monitor = DummyNoticeMonitor(response)
    source = DummyNewIssueSource(
        NewIssueCalendar(
            target_date=date(2026, 6, 3),
            stocks=[NewIssueItem(kind="stock", code="920126", name="永大股份", apply_code="920126")],
            bonds=[],
        )
    )
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        notice_dir=tmp_path,
        daily_summary_time="10:00",
        ipo_reminder_enabled=True,
    )
    notice = LofNoticeService(config, new_issue_source=source)
    sent_texts: list[str] = []
    notice._send_feishu_openapi = lambda text, *, state: sent_texts.append(text) or {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]
    scheduler = LofDailyNoticeScheduler(monitor=monitor, notice=notice)

    result = __import__("asyncio").run(scheduler.run_once(now=now))

    assert result["notice"]["status"] == "sent"
    assert result["new_issue_notice"]["status"] == "sent"
    assert len(sent_texts) == 2
    assert sent_texts[0].startswith("【LOF套利机会提醒】")
    assert sent_texts[1].startswith("【打新提醒】")
    assert source.calls == [date(2026, 6, 3)]
    assert monitor.calls and monitor.calls[0]["refresh"] is True


def test_lof_notice_scheduler_retries_new_issue_after_empty_check(tmp_path):
    first_now = datetime(2026, 6, 3, 2, 0, tzinfo=UTC)
    response = LofOpportunityResponse(
        scanned_at=first_now,
        normal_threshold_pct=2.0,
        strong_threshold_pct=5.0,
        min_turnover_yuan=3_000_000,
        core_count=0,
        watchlist_count=0,
        items=[],
    )
    monitor = DummyNoticeMonitor(response)
    empty_source = DummyNewIssueSource(NewIssueCalendar(target_date=date(2026, 6, 3), stocks=[], bonds=[]))
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        notice_dir=tmp_path,
        daily_summary_time="10:00",
        ipo_reminder_enabled=True,
    )
    notice = LofNoticeService(config, new_issue_source=empty_source)
    sent_texts: list[str] = []
    notice._send_feishu_openapi = lambda text, *, state: sent_texts.append(text) or {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]
    scheduler = LofDailyNoticeScheduler(monitor=monitor, notice=notice)

    first = __import__("asyncio").run(scheduler.run_once(now=first_now))
    state = json.loads(config.state_path.read_text(encoding="utf-8"))

    assert first["notice"]["status"] == "sent"
    assert first["new_issue_notice"]["status"] == "no_new_issue_items"
    assert state["last_daily_summary_date"] == "2026-06-03"
    assert state["last_ipo_check_at"] == "2026-06-03T02:00:00+00:00"
    assert state.get("last_ipo_reminder_date") is None
    assert scheduler.seconds_until_next_run(datetime(2026, 6, 3, 2, 5, tzinfo=UTC)) == 25 * 60

    bond_source = DummyNewIssueSource(
        NewIssueCalendar(
            target_date=date(2026, 6, 3),
            stocks=[],
            bonds=[NewIssueItem(kind="bond", code="113704", name="春风转债", apply_code="754129")],
        )
    )
    notice_retry = LofNoticeService(config, new_issue_source=bond_source)
    notice_retry._send_feishu_openapi = lambda text, *, state: sent_texts.append(text) or {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]
    retry_scheduler = LofDailyNoticeScheduler(monitor=monitor, notice=notice_retry)

    second = __import__("asyncio").run(retry_scheduler.run_once(now=datetime(2026, 6, 3, 2, 30, tzinfo=UTC)))

    assert second["scan"] is None
    assert second["new_issue_notice"]["status"] == "sent"
    assert bond_source.calls == [date(2026, 6, 3)]
    assert len(monitor.calls) == 1
    assert sent_texts[-1].startswith("【打新提醒】")


def test_new_issue_reminder_sends_when_called_with_daily_notice(tmp_path):
    calendar = NewIssueCalendar(
        target_date=date(2026, 6, 3),
        stocks=[
            NewIssueItem(
                kind="stock",
                code="920126",
                name="永大股份",
                apply_code="920126",
                market="北交所",
                issue_price=7.79,
                apply_limit=2_093_400,
            )
        ],
        bonds=[
            NewIssueItem(
                kind="bond",
                code="118068",
                name="迪威转债",
                apply_code="718377",
                rating="AA-",
                issue_scale_billion=9.07705,
                underlying="迪威尔",
            )
        ],
    )
    source = DummyNewIssueSource(calendar)
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        timeout_seconds=5,
        notice_dir=tmp_path,
        daily_summary_time="10:00",
        ipo_reminder_enabled=True,
    )
    notice = LofNoticeService(config, new_issue_source=source)
    sent_texts: list[str] = []
    notice._send_feishu_openapi = lambda text, *, state: sent_texts.append(text) or {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]

    first = __import__("asyncio").run(notice.notify_new_issue_reminder(now=datetime(2026, 6, 3, 2, 0, tzinfo=UTC)))
    second = __import__("asyncio").run(notice.notify_new_issue_reminder(now=datetime(2026, 6, 3, 2, 5, tzinfo=UTC)))

    assert first["status"] == "sent"
    assert second["status"] == "skipped_duplicate_ipo_reminder"
    assert source.calls == [date(2026, 6, 3)]
    assert sent_texts == [
        "\n".join(
            [
                "【打新提醒】2026-06-03 10:00",
                "今日可打新：新股 1 只，新债 1 只",
                "",
                "新股：",
                "920126 永大股份（申购代码 920126，北交所）",
                "发行价：7.79；申购上限：209.34万股",
                "",
                "新债：",
                "118068 迪威转债（申购代码 718377，正股 迪威尔）",
                "评级：AA-；规模：9.08亿",
            ]
        )
    ]
    state = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert state["last_ipo_reminder_date"] == "2026-06-03"


def test_new_issue_calendar_keeps_bonds_when_stock_endpoint_times_out():
    source = PartialFailingEastmoneyNewIssueSource()

    calendar = __import__("asyncio").run(source.get_calendar(date(2026, 6, 10)))

    assert calendar.stocks == []
    assert [bond.code for bond in calendar.bonds] == ["113704"]


def test_new_issue_reminder_omits_empty_stock_section_and_footer(tmp_path):
    calendar = NewIssueCalendar(
        target_date=date(2026, 6, 4),
        stocks=[],
        bonds=[
            NewIssueItem(
                kind="bond",
                code="118068",
                name="迪威转债",
                apply_code="718377",
                rating="AA-",
                issue_scale_billion=9.07705,
                underlying="迪威尔",
            )
        ],
    )
    source = DummyNewIssueSource(calendar)
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        timeout_seconds=5,
        notice_dir=tmp_path,
        daily_summary_time="09:30",
        ipo_reminder_enabled=True,
    )
    notice = LofNoticeService(config, new_issue_source=source)
    sent_texts: list[str] = []
    notice._send_feishu_openapi = lambda text, *, state: sent_texts.append(text) or {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]

    result = __import__("asyncio").run(notice.notify_new_issue_reminder(now=datetime(2026, 6, 4, 1, 30, tzinfo=UTC)))

    assert result["status"] == "sent"
    assert sent_texts == [
        "\n".join(
            [
                "【打新提醒】2026-06-04 09:30",
                "今日可打新：新股 0 只，新债 1 只",
                "",
                "新债：",
                "118068 迪威转债（申购代码 718377，正股 迪威尔）",
                "评级：AA-；规模：9.08亿",
            ]
        )
    ]


def test_new_issue_reminder_skips_when_disabled_or_empty(tmp_path):
    calendar = NewIssueCalendar(target_date=date(2026, 6, 3), stocks=[], bonds=[])
    source = DummyNewIssueSource(calendar)
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        timeout_seconds=5,
        notice_dir=tmp_path,
        daily_summary_time="10:00",
        ipo_reminder_enabled=False,
    )
    notice = LofNoticeService(config, new_issue_source=source)
    notice._send_feishu_openapi = lambda text, *, state: {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]

    disabled = __import__("asyncio").run(notice.notify_new_issue_reminder(now=datetime(2026, 6, 3, 1, 45, tzinfo=UTC)))
    notice.update_settings(ipo_reminder_enabled=True)
    empty = __import__("asyncio").run(notice.notify_new_issue_reminder(now=datetime(2026, 6, 3, 1, 50, tzinfo=UTC)))

    assert disabled["status"] == "ipo_reminder_disabled"
    assert empty["status"] == "no_new_issue_items"


def test_lof_notice_test_uses_alert_template(tmp_path):
    service = make_service(tmp_path)
    item = __import__("asyncio").run(service.get_item("501312"))
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        timeout_seconds=5,
        notice_dir=tmp_path,
    )
    notice = LofNoticeService(config)
    sent_texts: list[str] = []
    notice._send_feishu_openapi = lambda text, *, state: sent_texts.append(text) or {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]

    result = notice.send_test(item=item, now=datetime(2026, 6, 3, 2, 4, tzinfo=UTC))

    assert result["status"] == "sent"
    assert sent_texts == [
        "\n".join(
            [
                "【LOF套利机会提醒】",
                "2026-06-03 10:04",
                "501312 [QDII] 核心LOF501312",
                "操作建议：当前折溢价未超过提醒阈值，建议仅观察。",
                "成交额：10万；换手率：--；估算折溢价：+0.00%",
                "T日估算净值：1.0100；官方净值折溢价：+1.00%",
                "赎回开放；费率--；申购限额1万",
            ]
        )
    ]


def test_lof_notice_page_settings_override_env_config(tmp_path):
    service = make_service(tmp_path)
    response = __import__("asyncio").run(service.get_opportunities(limit=20))
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        timeout_seconds=5,
        notice_dir=tmp_path,
        daily_summary_time="10:00",
        send_empty_daily_summary=True,
    )
    notice = LofNoticeService(config)
    sent_texts: list[str] = []
    notice._send_feishu_openapi = lambda text, *, state: sent_texts.append(text) or {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]

    disabled_status = notice.update_settings(enabled=False, daily_summary_time="10:30")
    disabled_result = notice.notify_daily_summary(response, now=datetime(2026, 5, 29, 2, 35, tzinfo=UTC))

    assert disabled_status.enabled is False
    assert disabled_status.daily_summary_time == "10:30"
    assert disabled_result["status"] == "disabled"

    notice.update_settings(enabled=True)
    before_time = notice.notify_daily_summary(response, now=datetime(2026, 5, 29, 2, 5, tzinfo=UTC))
    after_time = notice.notify_daily_summary(response, now=datetime(2026, 5, 29, 2, 35, tzinfo=UTC))

    assert before_time["status"] == "skipped_before_daily_summary_time"
    assert after_time["status"] == "sent"
    assert len(sent_texts) == 1


def test_lof_notice_rejects_invalid_page_time(tmp_path):
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        timeout_seconds=5,
        notice_dir=tmp_path,
    )
    notice = LofNoticeService(config)

    try:
        notice.update_settings(daily_summary_time="24:01")
    except AppError as exc:
        assert exc.code == "INVALID_LOF_NOTICE_TIME"
    else:
        raise AssertionError("invalid notice time should raise AppError")


def test_lof_notice_daily_summary_skips_weekends(tmp_path):
    service = make_service(tmp_path)
    response = __import__("asyncio").run(service.get_opportunities(limit=20))
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        timeout_seconds=5,
        notice_dir=tmp_path,
        daily_summary_time="10:00",
    )
    notice = LofNoticeService(config)
    notice._send_feishu_openapi = lambda text, *, state: {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]

    result = notice.notify_daily_summary(response, now=datetime(2026, 5, 30, 2, 30, tzinfo=UTC))

    assert result["status"] == "skipped_non_trading_day"



def _afternoon_notice_item(
    code: str = "160001",
    premium: float = 4.2,
    turnover: float = 5_000_000,
    purchase_status: str = "开放",
) -> LofPremiumItem:
    now = datetime(2026, 6, 3, 6, 30, tzinfo=UTC)
    return LofPremiumItem(
        code=code,
        name=f"测试LOF{code}",
        estimated_premium_pct=premium,
        official_premium_pct=premium,
        exchange_turnover_yuan=turnover,
        purchase_status=purchase_status,
        redemption_status="开放",
        daily_purchase_limit_yuan=10_000,
        direction="neutral",
        level="none",
        updated_at=now,
    )


def _afternoon_response(now: datetime, items: list[LofPremiumItem]) -> LofOpportunityResponse:
    return LofOpportunityResponse(
        scanned_at=now,
        normal_threshold_pct=2.0,
        strong_threshold_pct=5.0,
        min_turnover_yuan=3_000_000,
        core_count=0,
        watchlist_count=0,
        items=items,
    )


def test_lof_notice_afternoon_check_sends_once_after_1430(tmp_path):
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        notice_dir=tmp_path,
        daily_summary_time="10:00",
    )
    notice = LofNoticeService(config)
    sent_texts: list[str] = []
    notice._send_feishu_openapi = lambda text, *, state: sent_texts.append(text) or {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]
    now = datetime(2026, 6, 3, 6, 30, tzinfo=UTC)
    item = _afternoon_notice_item()
    response = _afternoon_response(now, [item])
    seed_signal_history(config, [item], now - timedelta(days=1))

    first = notice.notify_afternoon_check(response, now=now)
    second = notice.notify_afternoon_check(response, now=now + timedelta(minutes=5))

    assert first["status"] == "sent"
    assert second["status"] == "skipped_duplicate_afternoon_check"
    assert len(sent_texts) == 1
    assert sent_texts[0].startswith("【LOF套利机会提醒】\n2026-06-03 14:30")
    assert "160001 测试LOF160001" in sent_texts[0]
    state = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert state["last_afternoon_check_date"] == "2026-06-03"
    rows = [json.loads(line) for line in config.ledger_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["kind"] == "afternoon_check"
    assert rows[0]["scan_items"] == 1
    assert rows[0]["scanned_at"] == "2026-06-03T06:30:00+00:00"
    assert rows[0]["raw_candidate_count"] == 1
    assert rows[0]["raw_candidate_codes"] == ["160001"]
    assert rows[0]["candidate_codes"] == ["160001"]


def test_lof_notice_afternoon_check_records_transient_without_sending(tmp_path):
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        notice_dir=tmp_path,
        daily_summary_time="10:00",
    )
    notice = LofNoticeService(config)
    sent_texts: list[str] = []
    notice._send_feishu_openapi = lambda text, *, state: sent_texts.append(text) or {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]
    now = datetime(2026, 6, 3, 6, 30, tzinfo=UTC)
    item = _afternoon_notice_item()
    response = _afternoon_response(now, [item])

    result = notice.notify_afternoon_check(response, now=now)

    assert result["status"] == "no_afternoon_opportunities"
    assert sent_texts == []
    assert not config.ledger_path.exists()
    state = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert state["last_afternoon_check_date"] == "2026-06-03"
    assert "160001" in state["signal_history"]["2026-06-03"]["items"]


def test_lof_notice_afternoon_check_skips_empty_without_sending(tmp_path):
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        notice_dir=tmp_path,
        daily_summary_time="10:00",
    )
    notice = LofNoticeService(config)
    sent_texts: list[str] = []
    notice._send_feishu_openapi = lambda text, *, state: sent_texts.append(text) or {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]
    now = datetime(2026, 6, 3, 6, 30, tzinfo=UTC)
    response = _afternoon_response(now, [])

    result = notice.notify_afternoon_check(response, now=now)
    duplicate = notice.notify_afternoon_check(response, now=now + timedelta(minutes=5))

    assert result["status"] == "no_afternoon_opportunities"
    assert duplicate["status"] == "skipped_duplicate_afternoon_check"
    assert sent_texts == []
    assert not config.ledger_path.exists()
    state = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert state["last_afternoon_check_date"] == "2026-06-03"


def test_lof_notice_afternoon_check_does_not_backfill_after_window(tmp_path):
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        notice_dir=tmp_path,
        daily_summary_time="10:00",
    )
    notice = LofNoticeService(config)
    sent_texts: list[str] = []
    notice._send_feishu_openapi = lambda text, *, state: sent_texts.append(text) or {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]
    late = datetime(2026, 6, 3, 7, 0, tzinfo=UTC)
    response = _afternoon_response(late, [_afternoon_notice_item()])

    result = notice.notify_afternoon_check(response, now=late)

    assert notice.should_run_afternoon_check(late) is False
    assert result["status"] == "skipped_after_afternoon_check_window"
    assert sent_texts == []
    assert not config.ledger_path.exists()


def test_lof_notice_scheduler_runs_afternoon_after_daily_summary(tmp_path):
    config = LofNoticeConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        notice_dir=tmp_path,
        daily_summary_time="10:00",
    )
    config.state_path.write_text(json.dumps({"last_daily_summary_date": "2026-06-03"}), encoding="utf-8")
    notice = LofNoticeService(config)
    sent_texts: list[str] = []
    notice._send_feishu_openapi = lambda text, *, state: sent_texts.append(text) or {"status": "sent", "provider": "unit"}  # type: ignore[method-assign]
    now = datetime(2026, 6, 3, 6, 30, tzinfo=UTC)
    item = _afternoon_notice_item("160002", 4.8, 8_000_000)
    seed_signal_history(config, [item], now - timedelta(days=1))
    monitor = DummyNoticeMonitor(_afternoon_response(now, [item]))
    scheduler = LofDailyNoticeScheduler(monitor=monitor, notice=notice)

    due_delay = scheduler.seconds_until_next_run(datetime(2026, 6, 3, 2, 5, tzinfo=UTC))
    result = __import__("asyncio").run(scheduler.run_once(now=now))

    assert due_delay == 4 * 60 * 60 + 25 * 60
    assert result["notice"]["status"] == "skipped_daily_summary_schedule"
    assert result["afternoon_notice"]["status"] == "sent"
    assert len(sent_texts) == 1
    assert monitor.calls and monitor.calls[0]["refresh"] is True
