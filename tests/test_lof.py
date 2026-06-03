from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

from fund_estimator.data_sources.sina import parse_sina_lof_quotes
from fund_estimator.models.lof import LofMarketQuote, LofOpportunityResponse, LofPremiumItem, LofTradingStatus
from fund_estimator.models.schema import FundProfile, FundSearchResult
from fund_estimator.services.cache import SQLiteCache
from fund_estimator.services.exceptions import AppError, DataSourceError
from fund_estimator.services.lof import EastmoneyLofTradingStatusDataSource, LofMonitorService
from fund_estimator.services.lof_config import CORE_LOF_BY_CODE
from fund_estimator.services.lof_notifications import LofNoticeConfig, LofNoticeService


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
        if code == "501046":
            return FundProfile(
                code=code,
                name="财通多策略福鑫定开混合",
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
                name="财通福鑫" if code == "501046" else "新机会LOF" if code == "160999" else f"核心LOF{code}",
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
        return await self.get_quotes(["160999", "160998", "501046"])


class FailingMarketSource:
    async def get_quotes(self, codes: list[str]):
        raise DataSourceError("LOF_QUOTE_FETCH_FAILED", "LOF 场内实时行情获取失败")


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


def make_service(tmp_path) -> LofMonitorService:
    estimator = DummyEstimator()
    estimator.cache = SQLiteCache(tmp_path / "lof.sqlite3")
    return LofMonitorService(
        estimator=estimator,
        cache=estimator.cache,
        market_source=DummyMarketSource(),
        status_source=DummyStatusSource(),
        proxy_source=DummyProxySource(),
        haoetf_source=DummyHaoEtfSource(),
    )


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
    assert item.is_opportunity is True
    assert item.level == "normal"
    assert item.actionable is False


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

    assert "501046" not in {row.code for row in response.items}


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


def test_lof_scan_keeps_core_rows_when_profile_fails(tmp_path):
    estimator = PartlyFailingEstimator()
    estimator.cache = SQLiteCache(tmp_path / "lof.sqlite3")
    service = LofMonitorService(
        estimator=estimator,
        cache=estimator.cache,
        market_source=DummyMarketSource(),
        status_source=DummyStatusSource(),
        proxy_source=DummyProxySource(),
        haoetf_source=DummyHaoEtfSource(),
    )

    response = __import__("asyncio").run(service.get_opportunities(limit=3))
    item = next(row for row in response.items if row.code == "161128")

    assert item.name == "核心LOF161128"
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

    first = notice.notify_daily_summary(response, now=now)
    second = notice.notify_daily_summary(response, now=now + timedelta(minutes=5))

    assert first["status"] == "sent"
    assert second["status"] == "skipped_duplicate_daily_summary"
    assert len(sent_texts) == 1
    assert "【LOF套利机会提醒】2026-05-29 10:05" in sent_texts[0]
    assert "161128" in sent_texts[0]
    assert "操作建议：" in sent_texts[0]
    assert "成交额：" in sent_texts[0]
    assert "估算溢价：" in sent_texts[0]
    assert "官方净值溢价：" in sent_texts[0]
    assert "申购限额" in sent_texts[0]
    state = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert state["last_daily_summary_date"] == "2026-05-29"


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
    assert "成交额：800万；估算溢价：-4.50%" in sent_texts[0]
    assert "操作建议：折价超过3%，成交额达标；先核实申赎规则、费用和到账时间，再评估场内买入相关操作。" in sent_texts[0]
    assert "操作建议：溢价超过3%，成交额达标；申购状态未明确暂停，先核实开放和限额。" in sent_texts[0]


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
                "【LOF套利机会提醒】2026-06-03 10:04",
                "501312 核心LOF501312",
                "操作建议：当前折溢价未超过提醒阈值，建议仅观察。",
                "成交额：10万；估算溢价：+0.00%",
                "官方净值溢价：+1.00%；申购限额1万",
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
