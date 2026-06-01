from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from fund_estimator.models.lof import LofProxyMove


class EtfMarketQuote(BaseModel):
    code: str
    name: str
    latest_price: float | None = None
    previous_close: float | None = None
    change_pct: float | None = None
    turnover_yuan: float | None = None
    iopv: float | None = None
    quote_time: datetime
    market: Literal["SH", "SZ", "UNKNOWN"] = "UNKNOWN"
    source: str = "unknown"


class EtfPremiumItem(BaseModel):
    code: str
    name: str
    theme: str | None = None
    exchange_price: float | None = None
    exchange_change_pct: float | None = None
    exchange_turnover_yuan: float | None = None
    iopv: float | None = None
    iopv_premium_pct: float | None = None
    official_nav: float | None = None
    official_nav_date: str | None = None
    official_premium_pct: float | None = None
    reference_change_pct: float | None = None
    reference_period_start: str | None = None
    reference_period_end: str | None = None
    reference_basis: str = "auxiliary"
    signal_basis: Literal["iopv", "official", "none"] = "none"
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


class EtfOpportunityResponse(BaseModel):
    scanned_at: datetime
    normal_threshold_pct: float
    strong_threshold_pct: float
    min_turnover_yuan: float
    core_count: int
    candidate_count: int
    items: list[EtfPremiumItem]
    errors: list[str] = Field(default_factory=list)
