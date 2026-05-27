from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fund_estimator import __version__
from fund_estimator.data_sources.eastmoney import (
    EastmoneyFundDataSource,
    EastmoneyHoldingsDataSource,
    EastmoneyQuoteDataSource,
)
from fund_estimator.data_sources.composite import FallbackQuoteDataSource
from fund_estimator.data_sources.mock import (
    MockFundDataSource,
    MockHoldingsDataSource,
    MockQuoteDataSource,
)
from fund_estimator.data_sources.sina import SinaQuoteDataSource
from fund_estimator.models.schema import (
    ApiError,
    ApiErrorResponse,
    BatchEstimateItem,
    BatchEstimateRequest,
    EstimateResponse,
    FundHoldings,
    FundProfile,
    FundSearchResult,
    HealthResponse,
    SourceStatus,
    WatchlistItem,
    WatchlistReorderRequest,
)
from fund_estimator.services.cache import SQLiteCache
from fund_estimator.services.estimator import FundEstimatorService
from fund_estimator.services.exceptions import AppError
from fund_estimator.services.watchlist import WatchlistService


WEB_DIR = Path(__file__).resolve().parents[1] / "web"
DEVICE_ID_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")


def normalize_device_id(device_id: str | None) -> str:
    value = (device_id or "default").strip()
    if not value:
        return "default"
    value = DEVICE_ID_PATTERN.sub("-", value)[:80].strip("-")
    return value or "default"


def get_runtime_config() -> dict[str, object]:
    allow_mock_fallback = os.getenv("FUND_ESTIMATOR_ALLOW_MOCK_FALLBACK", "0") == "1"
    force_mock = os.getenv("FUND_ESTIMATOR_FORCE_MOCK", "0") == "1"
    cache_path = os.getenv("FUND_ESTIMATOR_DB")
    if cache_path is None:
        cache_path = "data/fund_estimator.mock.sqlite3" if force_mock else "data/fund_estimator.sqlite3"
    return {
        "cache_path": cache_path,
        "allow_mock_fallback": allow_mock_fallback,
        "force_mock": force_mock,
        "provider": "mock" if force_mock else "eastmoney+sina",
        "mode": "mock" if force_mock else "real",
    }


def create_estimator_service() -> FundEstimatorService:
    config = get_runtime_config()
    cache_path = str(config["cache_path"])
    allow_mock_fallback = bool(config["allow_mock_fallback"])
    force_mock = bool(config["force_mock"])
    cache = SQLiteCache(cache_path)
    if force_mock:
        return FundEstimatorService(
            fund_source=MockFundDataSource(),
            holdings_source=MockHoldingsDataSource(),
            quote_source=MockQuoteDataSource(),
            cache=cache,
            allow_mock_fallback=False,
        )
    return FundEstimatorService(
        fund_source=EastmoneyFundDataSource(),
        holdings_source=EastmoneyHoldingsDataSource(),
        quote_source=FallbackQuoteDataSource([EastmoneyQuoteDataSource(timeout=3), SinaQuoteDataSource(timeout=5)]),
        cache=cache,
        mock_fund_source=MockFundDataSource(),
        mock_holdings_source=MockHoldingsDataSource(),
        mock_quote_source=MockQuoteDataSource(),
        allow_mock_fallback=allow_mock_fallback,
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="场外基金实时估值预测器",
        version=__version__,
        description="基于公开持仓和实时行情计算盘中估算净值，不是官方净值。",
    )
    estimator = create_estimator_service()
    watchlist = WatchlistService(estimator.cache)
    runtime_config = get_runtime_config()

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ApiErrorResponse(
                error=ApiError(code=exc.code, message=exc.message, details=exc.details)
            ).model_dump(mode="json"),
        )

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service="fund-estimator", version=__version__)

    @app.get("/api/source/status", response_model=SourceStatus)
    async def source_status() -> SourceStatus:
        return SourceStatus(
            mode=str(runtime_config["mode"]),
            provider=str(runtime_config["provider"]),
            mock_fallback_enabled=bool(runtime_config["allow_mock_fallback"]),
            cache_path=str(runtime_config["cache_path"]),
        )

    @app.get("/api/funds/search", response_model=list[FundSearchResult])
    async def search_funds(q: str = Query(..., min_length=1, max_length=64)) -> list[FundSearchResult]:
        return await estimator.search_funds(q)

    @app.get("/api/funds/{code}/nav", response_model=FundProfile)
    async def get_nav(code: str) -> FundProfile:
        return await estimator.get_profile(code)

    @app.get("/api/funds/{code}/holdings", response_model=FundHoldings)
    async def get_holdings(code: str) -> FundHoldings:
        return await estimator.get_holdings(code)

    @app.get("/api/estimate", response_model=EstimateResponse)
    async def estimate(
        code: str = Query(..., min_length=6, max_length=6),
        mode: Literal["raw", "normalized", "both"] = "both",
    ) -> EstimateResponse:
        return await estimator.estimate(code, mode=mode)

    @app.post("/api/estimate/batch", response_model=list[BatchEstimateItem])
    async def estimate_batch(request: BatchEstimateRequest) -> list[BatchEstimateItem]:
        results: list[BatchEstimateItem] = []
        for code in request.codes:
            try:
                item = await estimator.estimate(code, mode=request.mode)
                results.append(BatchEstimateItem(code=code, ok=True, estimate=item))
            except AppError as exc:
                profile: FundProfile | None = None
                try:
                    profile = await estimator.get_profile(code)
                except AppError:
                    profile = None
                results.append(BatchEstimateItem(code=code, ok=False, profile=profile, error=exc.to_error()))
        return results

    @app.get("/api/watchlist", response_model=list[WatchlistItem])
    async def get_watchlist(x_device_id: str | None = Header(None, alias="X-Device-Id")) -> list[WatchlistItem]:
        return watchlist.list_items(normalize_device_id(x_device_id))

    @app.post("/api/watchlist/{code}", response_model=WatchlistItem)
    async def add_watchlist_item(
        code: str,
        x_device_id: str | None = Header(None, alias="X-Device-Id"),
    ) -> WatchlistItem:
        profile = await estimator.get_profile(code)
        return watchlist.add(code, profile.name, normalize_device_id(x_device_id))

    @app.delete("/api/watchlist/{code}")
    async def delete_watchlist_item(
        code: str,
        x_device_id: str | None = Header(None, alias="X-Device-Id"),
    ) -> dict[str, bool]:
        return {"deleted": watchlist.delete(code, normalize_device_id(x_device_id))}

    @app.put("/api/watchlist/order", response_model=list[WatchlistItem])
    async def reorder_watchlist_items(
        request: WatchlistReorderRequest,
        x_device_id: str | None = Header(None, alias="X-Device-Id"),
    ) -> list[WatchlistItem]:
        return watchlist.reorder(request.codes, normalize_device_id(x_device_id))

    return app


app = create_app()
