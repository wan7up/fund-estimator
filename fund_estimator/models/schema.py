from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FundInfo(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)
    name: str
    fund_type: str | None = None
    source: str = "unknown"


class FundStageReturns(BaseModel):
    one_month_pct: float | None = None
    three_month_pct: float | None = None
    six_month_pct: float | None = None
    one_year_pct: float | None = None


class FundAssetAllocation(BaseModel):
    report_date: date | None = None
    stock_pct: float | None = None
    bond_pct: float | None = None
    cash_pct: float | None = None
    net_asset_billion: float | None = None


class FundManagerInfo(BaseModel):
    name: str
    work_time: str | None = None
    fund_size: str | None = None
    star: int | None = None


class FundTradingInfo(BaseModel):
    source_rate_pct: float | None = None
    current_rate_pct: float | None = None
    min_purchase_amount: float | None = None


class FundSimilarRank(BaseModel):
    rank_date: date | None = None
    rank: int | None = None
    total: int | None = None
    percentile_pct: float | None = None


class FundDetailInfo(BaseModel):
    stage_returns: FundStageReturns = Field(default_factory=FundStageReturns)
    asset_allocation: FundAssetAllocation = Field(default_factory=FundAssetAllocation)
    trading: FundTradingInfo = Field(default_factory=FundTradingInfo)
    managers: list[FundManagerInfo] = Field(default_factory=list)
    similar_rank: FundSimilarRank = Field(default_factory=FundSimilarRank)
    scale_date: date | None = None
    scale_billion: float | None = None


class FundProfile(FundInfo):
    nav_date: date
    last_nav: float
    previous_nav_date: date | None = None
    previous_nav: float | None = None
    actual_change_pct: float | None = None
    accumulated_nav: float | None = None
    details: FundDetailInfo = Field(default_factory=FundDetailInfo)
    stale: bool = False


class FundSearchResult(FundInfo):
    pinyin: str | None = None


class HoldingItem(BaseModel):
    stock_code: str
    stock_name: str
    weight_pct: float = Field(..., ge=0)
    market: Literal["SH", "SZ", "BJ", "HK", "UNKNOWN"] = "UNKNOWN"

    @field_validator("stock_code")
    @classmethod
    def normalize_stock_code(cls, value: str) -> str:
        return value.strip().upper()


class FundHoldings(BaseModel):
    fund_code: str
    holdings_date: date
    items: list[HoldingItem]
    source: str = "unknown"
    stale: bool = False

    @property
    def top10_weight_sum(self) -> float:
        return round(sum(item.weight_pct for item in self.items), 4)


class StockQuote(BaseModel):
    stock_code: str
    stock_name: str
    latest_price: float
    previous_close: float
    change_pct: float
    quote_time: datetime
    market: Literal["SH", "SZ", "BJ", "HK", "UNKNOWN"] = "UNKNOWN"
    source: str = "unknown"

    @property
    def change_ratio(self) -> float:
        return self.change_pct / 100


class HoldingEstimate(BaseModel):
    stock_code: str
    stock_name: str
    market: str
    weight_pct: float
    latest_price: float | None = None
    previous_close: float | None = None
    change_pct: float | None = None
    contribution_pct: float | None = None
    used: bool = False
    warning: str | None = None


class EstimateModeResult(BaseModel):
    mode: Literal["raw", "normalized"]
    estimated_nav: float
    estimated_change_pct: float
    portfolio_return_pct: float
    method: str


class EstimateResponse(BaseModel):
    fund_code: str
    fund_name: str
    fund_type: str | None
    fund_details: FundDetailInfo = Field(default_factory=FundDetailInfo)
    official_nav: float
    official_nav_date: date
    nav_date: date
    last_nav: float
    previous_nav_date: date | None = None
    previous_nav: float | None = None
    accumulated_nav: float | None = None
    estimate_time: datetime
    valuation_status: Literal["estimated", "official_nav"] = "estimated"
    is_official_nav: bool = False
    holdings_date: date | None = None
    top10_weight_sum: float
    usable_weight_sum: float
    primary_mode: Literal["raw", "normalized"] = "raw"
    estimated_nav: float
    estimated_nav_date: date | None = None
    estimated_change_pct: float | None
    actual_change_pct: float | None = None
    actual_change_date: date | None = None
    raw: EstimateModeResult | None = None
    normalized: EstimateModeResult | None = None
    confidence: ConfidenceLevel
    notes: list[str]
    warnings: list[str] = Field(default_factory=list)
    holdings: list[HoldingEstimate]
    data_source: str


class BatchEstimateRequest(BaseModel):
    codes: list[str] = Field(..., min_length=1, max_length=100)
    mode: Literal["raw", "normalized", "both"] = "both"


class BatchEstimateItem(BaseModel):
    code: str
    ok: bool
    estimate: EstimateResponse | None = None
    profile: FundProfile | None = None
    error: dict[str, Any] | None = None


class WatchlistItem(BaseModel):
    code: str
    name: str | None = None
    added_at: datetime
    sort_order: int = 0


class WatchlistReorderRequest(BaseModel):
    codes: list[str] = Field(..., min_length=1, max_length=100)


class ApiError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ApiErrorResponse(BaseModel):
    error: ApiError


class HealthResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    status: Literal["ok"]
    service: str
    version: str


class SourceStatus(BaseModel):
    mode: Literal["real", "mock"]
    provider: str
    mock_fallback_enabled: bool
    cache_path: str
