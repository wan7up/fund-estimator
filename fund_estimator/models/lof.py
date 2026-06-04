from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LofProxyMove(BaseModel):
    symbol: str
    label: str
    weight: float
    change_pct: float | None = None
    period_start: str | None = None
    period_end: str | None = None
    change_basis: str = "unknown"
    source: str = "unknown"
    warning: str | None = None


class LofTradingStatus(BaseModel):
    purchase_status: str = "unknown"
    redemption_status: str = "unknown"
    daily_purchase_limit_yuan: float | None = None
    fee_rate_pct: float | None = None
    source: str = "unknown"
    warning: str | None = None


class LofMarketQuote(BaseModel):
    code: str
    name: str
    latest_price: float | None = None
    previous_close: float | None = None
    change_pct: float | None = None
    turnover_yuan: float | None = None
    quote_time: datetime
    market: Literal["SH", "SZ", "UNKNOWN"] = "UNKNOWN"
    source: str = "unknown"


class LofPremiumItem(BaseModel):
    code: str
    name: str
    fund_type: str | None = None
    theme: str | None = None
    is_qdii: bool = False
    official_nav: float | None = None
    official_nav_date: str | None = None
    estimated_nav: float | None = None
    estimated_nav_time: datetime | None = None
    exchange_price: float | None = None
    exchange_change_pct: float | None = None
    exchange_turnover_yuan: float | None = None
    reference_change_pct: float | None = None
    reference_period_start: str | None = None
    reference_period_end: str | None = None
    reference_basis: str = "unknown"
    estimated_premium_pct: float | None = None
    official_premium_pct: float | None = None
    signal_basis: Literal["estimated", "official", "none"] = "none"
    direction: Literal["premium", "discount", "neutral", "unknown"] = "unknown"
    level: Literal["strong", "normal", "none"] = "none"
    is_opportunity: bool = False
    actionable: bool = False
    purchase_status: str = "unknown"
    redemption_status: str = "unknown"
    daily_purchase_limit_yuan: float | None = None
    fee_rate_pct: float | None = None
    risks: list[str] = Field(default_factory=list)
    proxy_moves: list[LofProxyMove] = Field(default_factory=list)
    data_source: str = "unknown"
    updated_at: datetime


class LofOpportunityResponse(BaseModel):
    scanned_at: datetime
    normal_threshold_pct: float
    strong_threshold_pct: float
    min_turnover_yuan: float
    core_count: int
    watchlist_count: int
    items: list[LofPremiumItem]
    errors: list[str] = Field(default_factory=list)


class LofWatchlistItem(BaseModel):
    code: str
    name: str | None = None
    added_at: datetime
    sort_order: int = 0


class LofNoticeStatus(BaseModel):
    enabled: bool
    provider: Literal["feishu_openapi"] = "feishu_openapi"
    app_configured: bool
    connected: bool
    target_set: bool
    target_kind: Literal["chat", "user", "missing"]
    target_name: str | None = None
    app_id: str | None = None
    redirect_uri: str | None = None
    setup_hint: str | None = None
    last_scan_at: str | None = None
    last_send_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    daily_summary_time: str | None = None
    ipo_reminder_enabled: bool = False
    last_daily_summary_date: str | None = None
    last_afternoon_check_date: str | None = None
    last_afternoon_check_at: str | None = None
    last_ipo_reminder_date: str | None = None
    cooldown_count: int = 0
    state_path: str
    ledger_path: str


class LofNoticeSettingsUpdate(BaseModel):
    enabled: bool | None = None
    daily_summary_time: str | None = None
    ipo_reminder_enabled: bool | None = None


class LofFeishuConnectResponse(BaseModel):
    configured: bool
    status: Literal["pending", "connected", "expired", "failed", "not_configured"] = "pending"
    authorize_url: str | None = None
    qr_url: str | None = None
    device_code: str | None = None
    redirect_uri: str | None = None
    expires_at: str | None = None
    interval_seconds: int = 5
    setup_hint: str | None = None
