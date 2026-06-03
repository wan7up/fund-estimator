from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from fund_estimator.models.lof import LofFeishuConnectResponse, LofNoticeStatus, LofOpportunityResponse, LofPremiumItem
from fund_estimator.services.exceptions import AppError
from fund_estimator.services.http_settings import http_trust_env


TRUTHY = {"1", "true", "yes", "on", "enabled"}
MARKET_TZ = ZoneInfo("Asia/Shanghai")
COOLDOWN_SECONDS = 30 * 60
SUMMARY_INTERVAL_SECONDS = 10 * 60
NOTICE_PREMIUM_THRESHOLD_PCT = 3.0
DEFAULT_NOTICE_MIN_TURNOVER_YUAN = 3_000_000
DEFAULT_DAILY_SUMMARY_TIME = "10:00"
CONNECT_STATE_TTL_SECONDS = 10 * 60
FEISHU_ACCOUNTS_BASE = "https://accounts.feishu.cn"
LARK_ACCOUNTS_BASE = "https://accounts.larksuite.com"
FEISHU_API_BASE = "https://open.feishu.cn"
FEISHU_SETUP_HINT = "点击接入飞书后用飞书扫码配置机器人；无需手动填写 App ID 或 App Secret。"
NEW_STOCK_LIST_URL = "https://datapc.eastmoney.com/da/purchase/list2"
NEW_BOND_LIST_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


@dataclass(frozen=True)
class LofNoticeConfig:
    enabled: bool = True
    app_id: str = ""
    app_secret: str = ""
    receive_id: str = ""
    receive_id_type: str = "open_id"
    timeout_seconds: int = 30
    notice_dir: Path = Path("data")
    daily_summary_time: str = DEFAULT_DAILY_SUMMARY_TIME
    send_empty_daily_summary: bool = True
    ipo_reminder_enabled: bool = False

    @property
    def state_path(self) -> Path:
        return self.notice_dir / "lof_notice_state.json"

    @property
    def ledger_path(self) -> Path:
        return self.notice_dir / "lof_notice_ledger.jsonl"


def _bool_env(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in TRUTHY


def _int_env(value: str | None, default: int) -> int:
    try:
        return int(value or default)
    except ValueError:
        return default


def _notice_dir_from_env(env: dict[str, str]) -> Path:
    configured = env.get("LOF_NOTICE_DIR")
    if configured:
        return Path(configured)
    db_path = env.get("FUND_ESTIMATOR_DB")
    if db_path:
        return Path(db_path).parent
    return Path("data")


def load_notice_config(env: dict[str, str] | None = None) -> LofNoticeConfig:
    source = env if env is not None else os.environ
    return LofNoticeConfig(
        enabled=_bool_env(source.get("LOF_NOTICE_ENABLED"), default=True),
        timeout_seconds=_int_env(source.get("LOF_FEISHU_TIMEOUT_SECONDS") or source.get("FEISHU_TIMEOUT_SECONDS"), 30),
        notice_dir=_notice_dir_from_env(source),
        daily_summary_time=(source.get("LOF_NOTICE_DAILY_SUMMARY_TIME") or DEFAULT_DAILY_SUMMARY_TIME).strip() or DEFAULT_DAILY_SUMMARY_TIME,
        send_empty_daily_summary=_bool_env(source.get("LOF_NOTICE_SEND_EMPTY_DAILY_SUMMARY"), default=True),
        ipo_reminder_enabled=_bool_env(source.get("LOF_IPO_REMINDER_ENABLED"), default=False),
    )


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return dict(default or {})
    return payload if isinstance(payload, dict) else dict(default or {})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class NewIssueItem:
    kind: str
    code: str
    name: str
    apply_code: str | None = None
    market: str | None = None
    issue_price: float | None = None
    apply_limit: float | None = None
    rating: str | None = None
    issue_scale_billion: float | None = None
    underlying: str | None = None


@dataclass(frozen=True)
class NewIssueCalendar:
    target_date: date
    stocks: list[NewIssueItem]
    bonds: list[NewIssueItem]
    source: str = "eastmoney"


class EastmoneyNewIssueSource:
    def __init__(self, timeout: float = 12.0) -> None:
        self.timeout = timeout

    async def get_calendar(self, target_date: date) -> NewIssueCalendar:
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://datapc.eastmoney.com/da/purchase/index?color=b"}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, trust_env=http_trust_env()) as client:
            stocks, bonds = await self._fetch_stocks(client, target_date), await self._fetch_bonds(client, target_date)
        return NewIssueCalendar(target_date=target_date, stocks=stocks, bonds=bonds)

    async def _fetch_stocks(self, client: httpx.AsyncClient, target_date: date) -> list[NewIssueItem]:
        response = await client.get(
            NEW_STOCK_LIST_URL,
            params={"stat": 0, "st": "APPLY_DATE", "sr": -1, "p": 1, "ps": 100},
        )
        response.raise_for_status()
        rows = ((response.json().get("result") or {}).get("data") or [])
        items: list[NewIssueItem] = []
        for row in rows:
            if _parse_date_value(row.get("APPLY_DATE")) != target_date:
                continue
            items.append(
                NewIssueItem(
                    kind="stock",
                    code=str(row.get("SECURITY_CODE") or "").strip(),
                    name=str(row.get("SECURITY_NAME") or "").strip(),
                    apply_code=_clean_text(row.get("APPLY_CODE")),
                    market=_clean_text(row.get("MARKET_TYPE") or row.get("TRADE_MARKET")),
                    issue_price=_parse_float(row.get("ISSUE_PRICE")),
                    apply_limit=_parse_float(row.get("ONLINE_APPLY_UPPER")),
                )
            )
        return [item for item in items if item.code and item.name]

    async def _fetch_bonds(self, client: httpx.AsyncClient, target_date: date) -> list[NewIssueItem]:
        day = target_date.isoformat()
        response = await client.get(
            NEW_BOND_LIST_URL,
            params={
                "sortColumns": "PUBLIC_START_DATE,SECURITY_CODE",
                "sortTypes": "-1,-1",
                "pageSize": 100,
                "pageNumber": 1,
                "reportName": "RPT_BOND_CB_LIST",
                "columns": "ALL",
                "filter": f"(PUBLIC_START_DATE='{day}')",
            },
        )
        response.raise_for_status()
        rows = ((response.json().get("result") or {}).get("data") or [])
        items: list[NewIssueItem] = []
        for row in rows:
            if _parse_date_value(row.get("PUBLIC_START_DATE")) != target_date:
                continue
            items.append(
                NewIssueItem(
                    kind="bond",
                    code=str(row.get("SECURITY_CODE") or "").strip(),
                    name=str(row.get("SECURITY_NAME_ABBR") or "").strip(),
                    apply_code=_clean_text(row.get("CORRECODE")),
                    market=_clean_text(row.get("TRADE_MARKET")),
                    issue_price=_parse_float(row.get("ISSUE_PRICE")),
                    rating=_clean_text(row.get("RATING")),
                    issue_scale_billion=_parse_float(row.get("ACTUAL_ISSUE_SCALE")),
                    underlying=_clean_text(row.get("SECURITY_SHORT_NAME")),
                )
            )
        return [item for item in items if item.code and item.name]


def _parse_float(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    if isinstance(value, str):
        value = value.replace(",", "").replace("%", "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date_value(value: Any) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _clean_text(value: Any) -> str | None:
    raw = str(value or "").strip()
    return raw or None


class LofNoticeService:
    def __init__(self, config: LofNoticeConfig | None = None, new_issue_source: Any | None = None) -> None:
        self.config = config or load_notice_config()
        self.new_issue_source = new_issue_source or EastmoneyNewIssueSource(timeout=self.config.timeout_seconds)

    def status(self) -> LofNoticeStatus:
        state = read_json(self.config.state_path, {})
        target = self._resolve_target(state)
        app_configured = self.app_configured()
        last_status = state.get("last_status")
        last_error = state.get("last_error")
        if last_error and "lark-cli" in str(last_error).lower():
            last_status = None
            last_error = None
        return LofNoticeStatus(
            enabled=self.effective_enabled(state),
            app_configured=app_configured,
            connected=target["source"] == "oauth",
            target_set=target["kind"] != "missing",
            target_kind=target["kind"],
            target_name=target.get("name"),
            app_id=self._masked_app_id(),
            redirect_uri=None,
            setup_hint=None if app_configured else FEISHU_SETUP_HINT,
            last_scan_at=state.get("last_scan_at"),
            last_send_at=state.get("last_send_at"),
            last_status=last_status,
            last_error=last_error,
            daily_summary_time=self.effective_daily_summary_time(state),
            ipo_reminder_enabled=self.effective_ipo_reminder_enabled(state),
            last_daily_summary_date=state.get("last_daily_summary_date"),
            last_ipo_reminder_date=state.get("last_ipo_reminder_date"),
            cooldown_count=len(self.active_cooldown_keys()),
            state_path=str(self.config.state_path),
            ledger_path=str(self.config.ledger_path),
        )

    def app_configured(self) -> bool:
        state = read_json(self.config.state_path, {})
        app = self._resolve_app_credentials(state)
        return bool(app.get("app_id") and app.get("app_secret"))

    def begin_feishu_connect(self, *, callback_url: str) -> LofFeishuConnectResponse:
        now = datetime.now(UTC)
        try:
            init_payload = self._feishu_registration_post(FEISHU_ACCOUNTS_BASE, {"action": "init"})
        except httpx.HTTPError as exc:
            return LofFeishuConnectResponse(
                configured=False,
                status="failed",
                setup_hint=f"连接飞书接入服务失败：{exc}",
            )
        supported = init_payload.get("supported_auth_methods") or []
        if "client_secret" not in supported:
            return LofFeishuConnectResponse(
                configured=False,
                status="failed",
                setup_hint="当前飞书环境暂不支持机器人扫码配置，请稍后再试或升级工具。",
            )
        try:
            begin_payload = self._feishu_registration_post(
                FEISHU_ACCOUNTS_BASE,
                {
                    "action": "begin",
                    "archetype": "PersonalAgent",
                    "auth_method": "client_secret",
                    "request_user_info": "open_id",
                },
            )
        except httpx.HTTPError as exc:
            return LofFeishuConnectResponse(
                configured=False,
                status="failed",
                setup_hint=f"生成飞书二维码失败：{exc}",
            )
        qr_url = str(begin_payload.get("verification_uri_complete") or "")
        device_code = str(begin_payload.get("device_code") or "")
        if not qr_url or not device_code:
            raise AppError(
                "FEISHU_ONBOARD_BEGIN_FAILED",
                "飞书未返回二维码接入信息。",
                status_code=502,
                details=begin_payload,
            )
        if "from=" not in qr_url:
            separator = "&" if "?" in qr_url else "?"
            qr_url = f"{qr_url}{separator}from=onboard"
        interval = _int_env(str(begin_payload.get("interval") or ""), 5)
        expire_in = _int_env(str(begin_payload.get("expire_in") or ""), CONNECT_STATE_TTL_SECONDS)
        expires_at = (now + timedelta(seconds=max(60, expire_in))).isoformat(timespec="seconds")
        payload = read_json(self.config.state_path, {})
        payload["feishu_onboard"] = {
            "device_code": device_code,
            "qr_url": qr_url,
            "account_base": FEISHU_ACCOUNTS_BASE,
            "created_at": now.isoformat(timespec="seconds"),
            "expires_at": expires_at,
            "interval_seconds": max(2, interval),
        }
        payload.pop("feishu_oauth", None)
        payload["last_status"] = None
        payload["last_error"] = None
        payload["notice_provider"] = "feishu_openapi"
        payload["updated_at"] = now.isoformat(timespec="seconds")
        write_json(self.config.state_path, payload)
        return LofFeishuConnectResponse(
            configured=True,
            status="pending",
            qr_url=qr_url,
            device_code=device_code,
            expires_at=expires_at,
            interval_seconds=max(2, interval),
        )

    def poll_feishu_connect(self) -> LofFeishuConnectResponse:
        payload = read_json(self.config.state_path, {})
        pending = payload.get("feishu_onboard") if isinstance(payload.get("feishu_onboard"), dict) else {}
        expires_at = self._parse_time(pending.get("expires_at"))
        if not pending:
            return LofFeishuConnectResponse(configured=False, status="failed", setup_hint="没有待完成的飞书接入流程，请重新点击接入飞书。")
        if expires_at is None or expires_at < datetime.now(UTC):
            payload.pop("feishu_onboard", None)
            payload["updated_at"] = iso_now()
            write_json(self.config.state_path, payload)
            return LofFeishuConnectResponse(configured=False, status="expired", setup_hint="二维码已过期，请重新点击接入飞书。")
        account_base = str(pending.get("account_base") or FEISHU_ACCOUNTS_BASE)
        try:
            poll_payload = self._feishu_registration_post(
                account_base,
                {"action": "poll", "device_code": str(pending.get("device_code") or "")},
            )
        except httpx.HTTPError as exc:
            return LofFeishuConnectResponse(
                configured=True,
                status="pending",
                qr_url=str(pending.get("qr_url") or ""),
                device_code=str(pending.get("device_code") or ""),
                expires_at=str(pending.get("expires_at") or ""),
                interval_seconds=_int_env(str(pending.get("interval_seconds") or ""), 5),
                setup_hint=f"连接飞书接入服务失败，稍后会重试：{exc}",
            )
        tenant_brand = poll_payload.get("user_info", {}).get("tenant_brand") if isinstance(poll_payload.get("user_info"), dict) else None
        if tenant_brand == "lark" and account_base != LARK_ACCOUNTS_BASE:
            pending["account_base"] = LARK_ACCOUNTS_BASE
            payload["feishu_onboard"] = pending
            write_json(self.config.state_path, payload)
            poll_payload = self._feishu_registration_post(
                LARK_ACCOUNTS_BASE,
                {"action": "poll", "device_code": str(pending.get("device_code") or "")},
            )
        client_id = str(poll_payload.get("client_id") or "").strip()
        client_secret = str(poll_payload.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            error = str(poll_payload.get("error") or "")
            if error in {"", "authorization_pending", "slow_down"}:
                return LofFeishuConnectResponse(
                    configured=True,
                    status="pending",
                    qr_url=str(pending.get("qr_url") or ""),
                    device_code=str(pending.get("device_code") or ""),
                    expires_at=str(pending.get("expires_at") or ""),
                    interval_seconds=_int_env(str(pending.get("interval_seconds") or ""), 5),
                    setup_hint="等待飞书扫码确认。",
                )
            if error == "expired_token":
                return LofFeishuConnectResponse(configured=False, status="expired", setup_hint="二维码已过期，请重新点击接入飞书。")
            return LofFeishuConnectResponse(
                configured=False,
                status="failed",
                setup_hint=str(poll_payload.get("error_description") or error or "飞书接入失败"),
            )
        user = poll_payload.get("user_info") if isinstance(poll_payload.get("user_info"), dict) else {}
        open_id = str(user.get("open_id") or "").strip()
        now = datetime.now(UTC)
        tenant_brand = user.get("tenant_brand")
        payload["feishu"] = {
            "open_id": open_id,
            "union_id": user.get("union_id"),
            "tenant_key": user.get("tenant_key"),
            "name": user.get("name") or user.get("en_name") or open_id,
            "avatar_url": user.get("avatar_url"),
            "connected_at": now.isoformat(timespec="seconds"),
        }
        payload["feishu_app"] = {
            "app_id": client_id,
            "app_secret": client_secret,
            "domain": "lark" if tenant_brand == "lark" else "feishu",
            "connected_at": now.isoformat(timespec="seconds"),
        }
        payload.pop("feishu_onboard", None)
        payload.pop("feishu_oauth", None)
        payload["last_error"] = None
        payload["notice_provider"] = "feishu_openapi"
        payload["updated_at"] = now.isoformat(timespec="seconds")
        write_json(self.config.state_path, payload)
        return LofFeishuConnectResponse(configured=True, status="connected", setup_hint="飞书机器人已接入。")

    def disconnect_feishu(self) -> LofNoticeStatus:
        payload = read_json(self.config.state_path, {})
        payload.pop("feishu", None)
        payload.pop("feishu_app", None)
        payload.pop("feishu_onboard", None)
        payload.pop("feishu_oauth", None)
        payload["last_status"] = None
        payload["last_error"] = None
        payload["notice_provider"] = "feishu_openapi"
        payload["updated_at"] = iso_now()
        write_json(self.config.state_path, payload)
        return self.status()

    def update_settings(
        self,
        *,
        enabled: bool | None = None,
        daily_summary_time: str | None = None,
        ipo_reminder_enabled: bool | None = None,
    ) -> LofNoticeStatus:
        state = read_json(self.config.state_path, {})
        settings = state.get("settings") if isinstance(state.get("settings"), dict) else {}
        settings = dict(settings)
        if enabled is not None:
            settings["enabled"] = bool(enabled)
        if daily_summary_time is not None:
            settings["daily_summary_time"] = self._normalize_hhmm(daily_summary_time)
        if ipo_reminder_enabled is not None:
            settings["ipo_reminder_enabled"] = bool(ipo_reminder_enabled)
        state["settings"] = settings
        state["notice_provider"] = "feishu_openapi"
        state["updated_at"] = iso_now()
        write_json(self.config.state_path, state)
        return self.status()

    def effective_enabled(self, state: dict[str, Any] | None = None) -> bool:
        settings = self._settings_from_state(state)
        if "enabled" not in settings:
            return self.config.enabled
        value = settings.get("enabled")
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in TRUTHY

    def effective_daily_summary_time(self, state: dict[str, Any] | None = None) -> str:
        settings = self._settings_from_state(state)
        raw = settings.get("daily_summary_time") or self.config.daily_summary_time
        try:
            return self._normalize_hhmm(raw)
        except AppError:
            return DEFAULT_DAILY_SUMMARY_TIME

    def effective_ipo_reminder_enabled(self, state: dict[str, Any] | None = None) -> bool:
        settings = self._settings_from_state(state)
        if "ipo_reminder_enabled" not in settings:
            return bool(self.config.ipo_reminder_enabled)
        return bool(settings.get("ipo_reminder_enabled"))

    def should_run_daily_summary(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        local = now.astimezone(MARKET_TZ)
        if local.weekday() >= 5:
            return False
        state = read_json(self.config.state_path, {})
        if state.get("last_daily_summary_date") == local.date().isoformat():
            return False
        return self._is_after_daily_summary_time(local, self.effective_daily_summary_time(state))

    def _settings_from_state(self, state: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = state if state is not None else read_json(self.config.state_path, {})
        settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
        return settings

    def active_cooldown_keys(self, now: datetime | None = None) -> set[str]:
        now = now or datetime.now(UTC)
        state = read_json(self.config.state_path, {})
        raw = state.get("cooldowns") if isinstance(state.get("cooldowns"), dict) else {}
        keys: set[str] = set()
        for key, value in raw.items():
            expires_at = self._parse_time(value)
            if expires_at is not None and expires_at > now:
                keys.add(str(key))
        return keys

    def notify_from_scan(self, response: LofOpportunityResponse, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        state = read_json(self.config.state_path, {})
        if not self.effective_enabled(state):
            result = self._state_result("disabled", now=now, last_error=None, rows=[])
            self._write_state(state, result, now=now)
            return result
        rows: list[dict[str, Any]] = []
        notice_items = self._notice_candidates(response)
        for item in notice_items:
            if self._is_instant_candidate(item, min_turnover_yuan=response.min_turnover_yuan):
                row = self._send_candidate(item, state=state, now=now, reason="instant", min_turnover_yuan=response.min_turnover_yuan)
                if row is not None:
                    rows.append(row)
        if self._should_send_summary(state, now) and notice_items:
            row = self._send_summary(response, state=state, now=now)
            if row is not None:
                rows.append(row)
        if rows:
            append_jsonl(self.config.ledger_path, rows)
        failed = [row for row in rows if str(row.get("status") or "").startswith("failed")]
        sent = [row for row in rows if row.get("status") == "sent"]
        status = "sent" if sent and not failed else "completed_with_failures" if failed else "no_notice"
        result = self._state_result(status, now=now, last_error=failed[-1].get("error") if failed else None, rows=rows)
        self._write_state(state, result, now=now)
        return result

    def notify_daily_summary(
        self,
        response: LofOpportunityResponse,
        *,
        now: datetime | None = None,
        force: bool = False,
        send_empty: bool | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        state = read_json(self.config.state_path, {})
        if not self.effective_enabled(state):
            result = self._state_result("disabled", now=now, last_error=None, rows=[])
            self._write_state(state, result, now=now)
            return result
        local = now.astimezone(MARKET_TZ)
        today = local.date().isoformat()
        if not force and local.weekday() >= 5:
            result = self._state_result("skipped_non_trading_day", now=now, last_error=None, rows=[])
            self._write_state(state, result, now=now)
            return result
        if not force and state.get("last_daily_summary_date") == today:
            result = self._state_result("skipped_duplicate_daily_summary", now=now, last_error=None, rows=[])
            self._write_state(state, result, now=now)
            return result
        if not force and not self._is_after_daily_summary_time(local, self.effective_daily_summary_time(state)):
            result = self._state_result("skipped_before_daily_summary_time", now=now, last_error=None, rows=[])
            self._write_state(state, result, now=now)
            return result
        send_empty = self.config.send_empty_daily_summary if send_empty is None else send_empty
        items = self._notice_candidates(response)
        if not items and not send_empty:
            result = self._state_result("no_actionable_opportunities", now=now, last_error=None, rows=[])
            state["last_daily_summary_date"] = today
            self._write_state(state, result, now=now)
            return result
        text = self._format_daily_summary(items, response=response, now=now)
        row = {
            "created_at": now.isoformat(timespec="seconds"),
            "kind": "daily_summary",
            "summary_date": today,
            "count": len(items),
            **self._send_feishu_openapi(text, state=state),
        }
        append_jsonl(self.config.ledger_path, [row])
        if row.get("status") == "sent":
            state["last_daily_summary_date"] = today
            state["last_daily_summary_at"] = now.isoformat(timespec="seconds")
        result = self._state_result(row["status"], now=now, last_error=row.get("error"), rows=[row])
        self._write_state(state, result, now=now)
        return result

    async def notify_new_issue_reminder(
        self,
        *,
        now: datetime | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        state = read_json(self.config.state_path, {})
        if not self.effective_enabled(state):
            result = self._state_result("disabled", now=now, last_error=None, rows=[])
            self._write_state(state, result, now=now)
            return result
        if not self.effective_ipo_reminder_enabled(state):
            result = self._state_result("ipo_reminder_disabled", now=now, last_error=None, rows=[])
            self._write_state(state, result, now=now)
            return result
        local = now.astimezone(MARKET_TZ)
        today = local.date().isoformat()
        if not force and local.weekday() >= 5:
            result = self._state_result("skipped_non_trading_day", now=now, last_error=None, rows=[])
            self._write_state(state, result, now=now)
            return result
        if not force and state.get("last_ipo_reminder_date") == today:
            result = self._state_result("skipped_duplicate_ipo_reminder", now=now, last_error=None, rows=[])
            self._write_state(state, result, now=now)
            return result
        reminder_time = self._ipo_reminder_time(self.effective_daily_summary_time(state))
        if not force and local.time() < reminder_time:
            result = self._state_result("skipped_before_ipo_reminder_time", now=now, last_error=None, rows=[])
            self._write_state(state, result, now=now)
            return result
        try:
            calendar = await self.new_issue_source.get_calendar(local.date())
        except httpx.HTTPError as exc:
            result = self._state_result("failed_new_issue_fetch", now=now, last_error=str(exc), rows=[])
            self._write_state(state, result, now=now)
            return result
        except Exception as exc:
            result = self._state_result("failed_new_issue_fetch", now=now, last_error=str(exc), rows=[])
            self._write_state(state, result, now=now)
            return result
        count = len(calendar.stocks) + len(calendar.bonds)
        if count == 0:
            result = self._state_result("no_new_issue_items", now=now, last_error=None, rows=[])
            self._write_state(state, result, now=now)
            return result
        text = self._format_new_issue_reminder(calendar, now=now)
        row = {
            "created_at": now.isoformat(timespec="seconds"),
            "kind": "new_issue_reminder",
            "summary_date": today,
            "count": count,
            "stock_count": len(calendar.stocks),
            "bond_count": len(calendar.bonds),
            **self._send_feishu_openapi(text, state=state),
        }
        append_jsonl(self.config.ledger_path, [row])
        if row.get("status") == "sent":
            state["last_ipo_reminder_date"] = today
            state["last_ipo_reminder_at"] = now.isoformat(timespec="seconds")
        result = self._state_result(row["status"], now=now, last_error=row.get("error"), rows=[row])
        self._write_state(state, result, now=now)
        return result

    def send_test(self, *, item: LofPremiumItem | None = None, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        text = self._format_alert([item], now=now) if item is not None else self._format_test_fallback(now=now)
        state = read_json(self.config.state_path, {})
        row = {
            "created_at": now.isoformat(timespec="seconds"),
            "kind": "test",
            "code": item.code if item is not None else None,
            "name": item.name if item is not None else None,
            **self._send_feishu_openapi(text, state=state),
        }
        append_jsonl(self.config.ledger_path, [row])
        result = self._state_result(row["status"], now=now, last_error=row.get("error"), rows=[row])
        self._write_state(state, result, now=now)
        return result

    @staticmethod
    def _cooldown_key(item: LofPremiumItem) -> str:
        premium = LofNoticeService._notice_premium_pct(item)
        direction = "premium" if premium is not None and premium > 0 else "discount" if premium is not None and premium < 0 else item.direction
        level = "abs_gt_3pct" if premium is not None and abs(premium) > NOTICE_PREMIUM_THRESHOLD_PCT else item.level
        return f"{item.code}:{direction}:{level}"

    @classmethod
    def _notice_candidates(cls, response: LofOpportunityResponse) -> list[LofPremiumItem]:
        items = [
            item
            for item in response.items
            if cls._is_notice_candidate(item, min_turnover_yuan=response.min_turnover_yuan)
        ]
        return sorted(items, key=cls._notice_sort_key)

    @classmethod
    def _is_instant_candidate(cls, item: LofPremiumItem, *, min_turnover_yuan: float = DEFAULT_NOTICE_MIN_TURNOVER_YUAN) -> bool:
        return cls._is_notice_candidate(item, min_turnover_yuan=min_turnover_yuan)

    @staticmethod
    def _is_notice_candidate(item: LofPremiumItem, *, min_turnover_yuan: float) -> bool:
        premium = LofNoticeService._notice_premium_pct(item)
        if premium is None or abs(premium) <= NOTICE_PREMIUM_THRESHOLD_PCT:
            return False
        if item.purchase_status == "暂停":
            return False
        if item.exchange_turnover_yuan is None or item.exchange_turnover_yuan <= min_turnover_yuan:
            return False
        return True

    @staticmethod
    def _notice_premium_pct(item: LofPremiumItem) -> float | None:
        return item.estimated_premium_pct if item.estimated_premium_pct is not None else item.official_premium_pct

    @staticmethod
    def _notice_sort_key(item: LofPremiumItem) -> tuple[float, float, str]:
        premium = LofNoticeService._notice_premium_pct(item)
        return (-(abs(premium) if premium is not None else -1), -(item.exchange_turnover_yuan or 0), item.code)

    def _send_candidate(
        self,
        item: LofPremiumItem,
        *,
        state: dict[str, Any],
        now: datetime,
        reason: str,
        min_turnover_yuan: float,
    ) -> dict[str, Any] | None:
        key = self._cooldown_key(item)
        cooldowns = state.setdefault("cooldowns", {})
        expires_at = self._parse_time(cooldowns.get(key))
        if expires_at is not None and expires_at > now:
            return None
        text = self._format_candidate(item, reason=reason, min_turnover_yuan=min_turnover_yuan)
        premium = self._notice_premium_pct(item)
        row = {
            "created_at": now.isoformat(timespec="seconds"),
            "kind": reason,
            "code": item.code,
            "name": item.name,
            "direction": "premium" if premium is not None and premium > 0 else "discount" if premium is not None and premium < 0 else item.direction,
            "level": "abs_gt_3pct" if premium is not None and abs(premium) > NOTICE_PREMIUM_THRESHOLD_PCT else item.level,
            "estimated_premium_pct": item.estimated_premium_pct,
            "official_premium_pct": item.official_premium_pct,
            **self._send_feishu_openapi(text, state=state),
        }
        if row.get("status") == "sent":
            cooldowns[key] = (now + timedelta(seconds=COOLDOWN_SECONDS)).isoformat(timespec="seconds")
        return row

    def _send_summary(self, response: LofOpportunityResponse, *, state: dict[str, Any], now: datetime) -> dict[str, Any] | None:
        items = self._notice_candidates(response)[:8]
        if not items:
            return None
        text = self._format_summary(items, response=response)
        row = {
            "created_at": now.isoformat(timespec="seconds"),
            "kind": "summary",
            "count": len(items),
            **self._send_feishu_openapi(text, state=state),
        }
        if row.get("status") == "sent":
            state["last_summary_at"] = now.isoformat(timespec="seconds")
        return row

    @staticmethod
    def _should_send_summary(state: dict[str, Any], now: datetime) -> bool:
        local = now.astimezone(MARKET_TZ)
        if local.weekday() >= 5:
            return False
        current_time = local.time()
        if not (time(13, 0) <= current_time <= time(14, 40)):
            return False
        last_summary = LofNoticeService._parse_time(state.get("last_summary_at"))
        return last_summary is None or (now - last_summary).total_seconds() >= SUMMARY_INTERVAL_SECONDS

    @staticmethod
    def _format_new_issue_reminder(calendar: NewIssueCalendar, *, now: datetime) -> str:
        local_now = now.astimezone(MARKET_TZ)
        lines = [
            f"【打新提醒】{local_now.strftime('%Y-%m-%d %H:%M')}",
            f"今日可打新：新股 {len(calendar.stocks)} 只，新债 {len(calendar.bonds)} 只",
        ]
        if calendar.stocks:
            lines.extend(["", "新股："])
            for item in calendar.stocks[:10]:
                market = f"，{item.market}" if item.market else ""
                lines.extend(
                    [
                        f"{item.code} {item.name}（申购代码 {item.apply_code or item.code}{market}）",
                        f"发行价：{LofNoticeService._format_price(item.issue_price)}；申购上限：{LofNoticeService._format_share_limit(item.apply_limit)}",
                    ]
                )
            if len(calendar.stocks) > 10:
                lines.append(f"另有 {len(calendar.stocks) - 10} 只新股未列出。")
        if calendar.bonds:
            lines.extend(["", "新债："])
            for item in calendar.bonds[:10]:
                underlying = f"，正股 {item.underlying}" if item.underlying else ""
                lines.extend(
                    [
                        f"{item.code} {item.name}（申购代码 {item.apply_code or item.code}{underlying}）",
                        f"评级：{item.rating or '--'}；规模：{LofNoticeService._format_billion(item.issue_scale_billion)}",
                    ]
                )
            if len(calendar.bonds) > 10:
                lines.append(f"另有 {len(calendar.bonds) - 10} 只新债未列出。")
        lines.extend(["", "操作提示：在券商客户端的新股/新债申购入口处理；中签后注意缴款日和账户可用资金。"])
        return "\n".join(lines)

    @staticmethod
    def _format_candidate(item: LofPremiumItem, *, reason: str, min_turnover_yuan: float = DEFAULT_NOTICE_MIN_TURNOVER_YUAN) -> str:
        return LofNoticeService._format_alert([item], now=datetime.now(UTC), min_turnover_yuan=min_turnover_yuan)

    @staticmethod
    def _format_summary(items: list[LofPremiumItem], *, response: LofOpportunityResponse) -> str:
        return LofNoticeService._format_alert(items, now=response.scanned_at, min_turnover_yuan=response.min_turnover_yuan)

    @staticmethod
    def _format_daily_summary(items: list[LofPremiumItem], *, response: LofOpportunityResponse, now: datetime) -> str:
        if not items:
            local_now = now.astimezone(MARKET_TZ)
            return "\n".join(
                [
                    f"【LOF套利机会提醒】{local_now.strftime('%Y-%m-%d %H:%M')}",
                    "当前暂无可操作套利机会。",
                    f"扫描池：{len(response.items)}只",
                    f"主要过滤条件：折价或溢价超过{NOTICE_PREMIUM_THRESHOLD_PCT:.0f}%、申购未明确暂停、成交额超过{LofNoticeService._format_money(response.min_turnover_yuan)}。",
                ]
            )
        return LofNoticeService._format_alert(items[:10], now=now, min_turnover_yuan=response.min_turnover_yuan)

    @staticmethod
    def _format_alert(
        items: list[LofPremiumItem],
        *,
        now: datetime,
        min_turnover_yuan: float = DEFAULT_NOTICE_MIN_TURNOVER_YUAN,
    ) -> str:
        local_now = now.astimezone(MARKET_TZ)
        lines = [f"【LOF套利机会提醒】{local_now.strftime('%Y-%m-%d %H:%M')}"]
        for index, item in enumerate(items):
            if index:
                lines.append("")
            lines.extend(
                [
                    f"{item.code} {item.name}",
                    f"操作建议：{LofNoticeService._action_advice(item, min_turnover_yuan=min_turnover_yuan)}",
                    f"成交额：{LofNoticeService._format_money(item.exchange_turnover_yuan)}；估算溢价：{LofNoticeService._format_pct(item.estimated_premium_pct)}",
                    f"官方净值溢价：{LofNoticeService._format_pct(item.official_premium_pct)}；申购限额{LofNoticeService._format_limit(item.daily_purchase_limit_yuan)}",
                ]
            )
        return "\n".join(lines)

    @staticmethod
    def _format_test_fallback(*, now: datetime) -> str:
        local_now = now.astimezone(MARKET_TZ)
        return "\n".join(
            [
                f"【LOF套利机会提醒】{local_now.strftime('%Y-%m-%d %H:%M')}",
                "501312 华宝海外科技股票(QDII-LOF)A",
                "操作建议：测试通知，当前未取得基金实时数据，仅用于验证飞书连接。",
                "成交额：--；估算溢价：--",
                "官方净值溢价：--；申购限额--",
            ]
        )

    @staticmethod
    def _action_advice(item: LofPremiumItem, *, min_turnover_yuan: float = DEFAULT_NOTICE_MIN_TURNOVER_YUAN) -> str:
        premium = LofNoticeService._notice_premium_pct(item)
        if premium is None or abs(premium) <= NOTICE_PREMIUM_THRESHOLD_PCT:
            return "当前折溢价未超过提醒阈值，建议仅观察。"
        if item.purchase_status == "暂停":
            return "折溢价超过3%，但申购暂停，暂不进入套利提醒。"
        if item.exchange_turnover_yuan is None:
            return "折溢价超过3%，但成交额未知，先核实流动性和申购状态。"
        if item.exchange_turnover_yuan <= min_turnover_yuan:
            return f"折溢价超过3%，但成交额未超过{LofNoticeService._format_money(min_turnover_yuan)}，建议仅观察或小额验证。"
        if premium < 0:
            if item.purchase_status == "unknown":
                return "折价超过3%，成交额达标；申购状态未明确暂停，先核实申赎规则和费用。"
            return "折价超过3%，成交额达标；先核实申赎规则、费用和到账时间，再评估场内买入相关操作。"
        if item.purchase_status == "unknown":
            return "溢价超过3%，成交额达标；申购状态未明确暂停，先核实开放和限额。"
        if item.purchase_status == "限制大额":
            return "溢价超过3%，成交额达标；申购限额受限，先确认额度再评估申购转场内卖出。"
        return "溢价超过3%，成交额达标；优先确认申购开放、限额和费率，再评估申购转场内卖出。"

    @staticmethod
    def _format_pct(value: float | None) -> str:
        if value is None:
            return "--"
        return f"{value:+.2f}%"

    @staticmethod
    def _format_money(value: float | None) -> str:
        if value is None:
            return "--"
        if abs(value) >= 100000000:
            return f"{value / 100000000:.2f}亿"
        if abs(value) >= 10000:
            return f"{value / 10000:.0f}万"
        return f"{value:.0f}元"

    @staticmethod
    def _format_limit(value: float | None) -> str:
        if value is None:
            return "--"
        return LofNoticeService._format_money(value)

    @staticmethod
    def _format_price(value: float | None) -> str:
        if value is None:
            return "--"
        return f"{value:.2f}"

    @staticmethod
    def _format_share_limit(value: float | None) -> str:
        if value is None:
            return "--"
        if abs(value) >= 10000:
            return f"{value / 10000:.2f}万股"
        return f"{value:.0f}股"

    @staticmethod
    def _format_billion(value: float | None) -> str:
        if value is None:
            return "--"
        return f"{value:.2f}亿"

    def _is_after_daily_summary_time(self, local_now: datetime, summary_time: str | None = None) -> bool:
        return local_now.time() >= self._parse_hhmm(summary_time or self.effective_daily_summary_time())

    @staticmethod
    def _ipo_reminder_time(summary_time: str) -> time:
        base = datetime.combine(date(2000, 1, 2), LofNoticeService._parse_hhmm(summary_time))
        return (base - timedelta(minutes=15)).time()

    @staticmethod
    def _parse_hhmm(value: str) -> time:
        try:
            hour, minute = value.split(":", 1)
            return time(max(0, min(23, int(hour))), max(0, min(59, int(minute))))
        except (ValueError, TypeError):
            return time(10, 0)

    @staticmethod
    def _normalize_hhmm(value: Any) -> str:
        raw = str(value or "").strip()
        try:
            hour_text, minute_text = raw.split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)
        except (TypeError, ValueError):
            raise AppError(
                "INVALID_LOF_NOTICE_TIME",
                "通知时间必须是 HH:MM 格式",
                status_code=422,
                details={"value": raw},
            ) from None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise AppError(
                "INVALID_LOF_NOTICE_TIME",
                "通知时间必须在 00:00 到 23:59 之间",
                status_code=422,
                details={"value": raw},
            )
        return f"{hour:02d}:{minute:02d}"

    def _send_feishu_openapi(self, text: str, *, state: dict[str, Any]) -> dict[str, Any]:
        if not self._resolve_app_credentials(state):
            return {"status": "failed_missing_config", "provider": "feishu_openapi", "error": FEISHU_SETUP_HINT}
        target = self._resolve_target(state)
        if target["kind"] == "missing" or not target.get("receive_id"):
            return {"status": "failed_missing_config", "provider": "feishu_openapi", "error": "尚未接入飞书，请先在页面点击接入飞书完成授权。"}
        try:
            tenant_token = self._get_tenant_access_token(state)
            content = json.dumps({"text": text}, ensure_ascii=False, separators=(",", ":"))
            response = self._feishu_post(
                f"/open-apis/im/v1/messages?receive_id_type={target['receive_id_type']}",
                token=tenant_token,
                json_body={
                    "receive_id": target["receive_id"],
                    "msg_type": "text",
                    "content": content,
                },
            )
            data = self._extract_feishu_data(response, code="FEISHU_MESSAGE_SEND_FAILED")
        except AppError as exc:
            return {"status": "failed_provider_rejected", "provider": "feishu_openapi", "error": exc.message}
        except httpx.TimeoutException:
            return {"status": "failed_send_error", "provider": "feishu_openapi", "error": "Feishu OpenAPI timed out"}
        except httpx.HTTPError as exc:
            return {"status": "failed_send_error", "provider": "feishu_openapi", "error": str(exc)}
        return {
            "status": "sent",
            "provider": "feishu_openapi",
            "target_kind": target["kind"],
            "message_id": data.get("message_id") or data.get("message", {}).get("message_id"),
        }

    def _get_tenant_access_token(self, state: dict[str, Any]) -> str:
        credentials = self._resolve_app_credentials(state)
        app_id = str(credentials.get("app_id") or "")
        app_secret = str(credentials.get("app_secret") or "")
        if not app_id or not app_secret:
            raise AppError("FEISHU_APP_NOT_CONFIGURED", FEISHU_SETUP_HINT, status_code=400)
        cached = state.get("feishu_tenant_token") if isinstance(state.get("feishu_tenant_token"), dict) else {}
        cached_token = str(cached.get("tenant_access_token") or "")
        expires_at = self._parse_time(cached.get("expires_at"))
        if cached_token and expires_at is not None and expires_at > datetime.now(UTC) + timedelta(minutes=5):
            return cached_token
        response = self._feishu_post(
            "/open-apis/auth/v3/tenant_access_token/internal",
            json_body={"app_id": app_id, "app_secret": app_secret},
        )
        data = self._extract_feishu_data(response, code="FEISHU_TENANT_TOKEN_FAILED")
        token = str(data.get("tenant_access_token") or "")
        if not token:
            raise AppError("FEISHU_TENANT_TOKEN_MISSING", "飞书未返回 tenant_access_token。", status_code=502, details=data)
        expire_seconds = _int_env(str(data.get("expire") or ""), 7200)
        state["feishu_tenant_token"] = {
            "tenant_access_token": token,
            "expires_at": (datetime.now(UTC) + timedelta(seconds=max(60, expire_seconds - 60))).isoformat(timespec="seconds"),
        }
        return token

    def _feishu_registration_post(self, base_url: str, form_body: dict[str, str]) -> dict[str, Any]:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        with httpx.Client(timeout=max(5, self.config.timeout_seconds), trust_env=http_trust_env()) as client:
            response = client.post(
                f"{base_url}/oauth/v1/app/registration",
                headers=headers,
                data=form_body,
            )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _feishu_post(self, path: str, *, json_body: dict[str, Any], token: str | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with httpx.Client(timeout=max(5, self.config.timeout_seconds), trust_env=http_trust_env()) as client:
            response = client.post(f"{FEISHU_API_BASE}{path}", headers=headers, json=json_body)
        response.raise_for_status()
        return response.json()

    def _feishu_get(self, path: str, *, token: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.Client(timeout=max(5, self.config.timeout_seconds), trust_env=http_trust_env()) as client:
            response = client.get(f"{FEISHU_API_BASE}{path}", headers=headers)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _extract_feishu_data(payload: dict[str, Any], *, code: str) -> dict[str, Any]:
        raw_code = payload.get("code")
        if raw_code not in (None, 0):
            message = str(payload.get("msg") or payload.get("message") or "飞书接口返回错误")
            raise AppError(code, message, status_code=502, details={"feishu_code": raw_code})
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def _resolve_target(self, state: dict[str, Any]) -> dict[str, Any]:
        linked = state.get("feishu") if isinstance(state.get("feishu"), dict) else {}
        open_id = str(linked.get("open_id") or "").strip()
        if open_id:
            return {
                "kind": "user",
                "source": "oauth",
                "receive_id": open_id,
                "receive_id_type": "open_id",
                "name": str(linked.get("name") or open_id),
            }
        return {"kind": "missing", "source": "missing", "receive_id": "", "receive_id_type": "open_id", "name": None}

    def _resolve_app_credentials(self, state: dict[str, Any]) -> dict[str, str]:
        if self.config.app_id and self.config.app_secret:
            return {"app_id": self.config.app_id, "app_secret": self.config.app_secret, "source": "env"}
        app = state.get("feishu_app") if isinstance(state.get("feishu_app"), dict) else {}
        app_id = str(app.get("app_id") or "").strip()
        app_secret = str(app.get("app_secret") or "").strip()
        if app_id and app_secret:
            return {"app_id": app_id, "app_secret": app_secret, "source": "onboard"}
        return {}

    def _masked_app_id(self) -> str | None:
        app_id = self._resolve_app_credentials(read_json(self.config.state_path, {})).get("app_id", "")
        if not app_id:
            return None
        if len(app_id) <= 8:
            return app_id
        return f"{app_id[:6]}...{app_id[-4:]}"

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _state_result(self, status: str, *, now: datetime, last_error: str | None, rows: list[dict[str, Any]]) -> dict[str, Any]:
        sent_rows = [row for row in rows if row.get("status") == "sent"]
        return {
            "created_at": now.isoformat(timespec="seconds"),
            "status": status,
            "sent_count": len(sent_rows),
            "failed_count": len([row for row in rows if str(row.get("status") or "").startswith("failed")]),
            "last_error": last_error,
            "rows": rows,
        }

    def _write_state(self, prior: dict[str, Any], result: dict[str, Any], *, now: datetime) -> None:
        cooldowns = prior.get("cooldowns") if isinstance(prior.get("cooldowns"), dict) else {}
        active = {
            key: value
            for key, value in cooldowns.items()
            if (self._parse_time(value) is not None and self._parse_time(value) > now)
        }
        state = {
            **prior,
            "notice_provider": "feishu_openapi",
            "updated_at": now.isoformat(timespec="seconds"),
            "last_scan_at": now.isoformat(timespec="seconds"),
            "last_status": result.get("status"),
            "last_error": result.get("last_error"),
            "cooldowns": active,
        }
        if result.get("sent_count"):
            state["last_send_at"] = now.isoformat(timespec="seconds")
        write_json(self.config.state_path, state)
