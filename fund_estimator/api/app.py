from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
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
    MockHaoEtfDataSource,
    MockEtfMarketDataSource,
    MockLofMarketDataSource,
    MockLofProxyDataSource,
    MockLofTradingStatusDataSource,
    MockQuoteDataSource,
)
from fund_estimator.data_sources.sina import SinaLofMarketDataSource, SinaQuoteDataSource
from fund_estimator.models.schema import (
    ApiError,
    ApiErrorResponse,
    BatchEstimateItem,
    BatchEstimateRequest,
    CompareRequest,
    CompareResponse,
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
from fund_estimator.services.comparison import FundComparisonService
from fund_estimator.services.estimator import FundEstimatorService
from fund_estimator.services.exceptions import AppError
from fund_estimator.services.etf import EastmoneyEtfMarketDataSource, EtfMonitorService
from fund_estimator.services.lof import EastmoneyLofMarketDataSource, LofMonitorService
from fund_estimator.services.lof_notifications import LofNoticeService
from fund_estimator.services.watchlist import WatchlistService
from fund_estimator.models.lof import (
    LofFeishuConnectResponse,
    LofNoticeSettingsUpdate,
    LofNoticeStatus,
    LofOpportunityResponse,
    LofPremiumItem,
    LofWatchlistItem,
)
from fund_estimator.models.etf import EtfOpportunityResponse


WEB_DIR = Path(__file__).resolve().parents[1] / "web"
DEVICE_ID_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")
logger = logging.getLogger(__name__)


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
        "background_scan_enabled": os.getenv("FUND_ESTIMATOR_BACKGROUND_SCAN", "0") == "1",
        "background_scan_interval_seconds": int(os.getenv("FUND_ESTIMATOR_SCAN_INTERVAL_SECONDS", "60")),
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
            allow_mock_cache=True,
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
        allow_mock_cache=allow_mock_fallback,
    )


def create_lof_monitor_service(
    estimator: FundEstimatorService,
    *,
    notice_service: LofNoticeService | None = None,
) -> LofMonitorService:
    if bool(get_runtime_config()["force_mock"]):
        return LofMonitorService(
            estimator=estimator,
            cache=estimator.cache,
            market_source=MockLofMarketDataSource(),
            discovery_market_source=MockLofMarketDataSource(),
            status_source=MockLofTradingStatusDataSource(),
            proxy_source=MockLofProxyDataSource(),
            haoetf_source=MockHaoEtfDataSource(),
            notice_cooldown_reader=notice_service.active_cooldown_keys if notice_service else None,
        )
    return LofMonitorService(
        estimator=estimator,
        cache=estimator.cache,
        market_source=FallbackQuoteDataSource(
            [EastmoneyLofMarketDataSource(timeout=3), SinaLofMarketDataSource(timeout=5)]
        ),
        discovery_market_source=EastmoneyLofMarketDataSource(timeout=2.5),
        notice_cooldown_reader=notice_service.active_cooldown_keys if notice_service else None,
    )


def create_etf_monitor_service(estimator: FundEstimatorService) -> EtfMonitorService:
    if bool(get_runtime_config()["force_mock"]):
        return EtfMonitorService(
            estimator=estimator,
            cache=estimator.cache,
            market_source=MockEtfMarketDataSource(),
            status_source=MockLofTradingStatusDataSource(),
            proxy_source=MockLofProxyDataSource(),
        )
    return EtfMonitorService(
        estimator=estimator,
        cache=estimator.cache,
        market_source=EastmoneyEtfMarketDataSource(timeout=6),
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="基金工具箱",
        version=__version__,
        description="提供场外基金估值、套利监控和基金对比研究辅助。",
    )
    estimator = create_estimator_service()
    watchlist = WatchlistService(estimator.cache)
    comparison = FundComparisonService(estimator)
    lof_notice = LofNoticeService()
    lof_monitor = create_lof_monitor_service(estimator, notice_service=lof_notice)
    etf_monitor = create_etf_monitor_service(estimator)
    runtime_config = get_runtime_config()

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    async def background_scan_loop() -> None:
        interval = max(15, int(runtime_config["background_scan_interval_seconds"]))
        while True:
            try:
                await etf_monitor.get_opportunities(refresh=True, limit=500)
            except Exception:
                logger.exception("ETF background scan failed")
            try:
                await lof_monitor.get_opportunities(refresh=True, limit=500)
            except Exception:
                logger.exception("LOF background scan failed")
            await asyncio.sleep(interval)

    @app.on_event("startup")
    async def start_background_scan() -> None:
        if bool(runtime_config["background_scan_enabled"]):
            app.state.background_scan_task = asyncio.create_task(background_scan_loop())

    @app.on_event("shutdown")
    async def stop_background_scan() -> None:
        task = getattr(app.state, "background_scan_task", None)
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ApiErrorResponse(
                error=ApiError(code=exc.code, message=exc.message, details=exc.details)
            ).model_dump(mode="json"),
        )

    @app.get("/", include_in_schema=False)
    @app.get("/estimate", include_in_schema=False)
    @app.get("/arbitrage", include_in_schema=False)
    @app.get("/compare", include_in_schema=False)
    @app.get("/monitor", include_in_schema=False)
    async def shell_page() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/tool/estimate", include_in_schema=False)
    async def estimate_tool_page() -> FileResponse:
        return FileResponse(WEB_DIR / "estimate.html")

    @app.get("/tool/arbitrage", include_in_schema=False)
    async def arbitrage_tool_page() -> FileResponse:
        return FileResponse(WEB_DIR / "arbitrage.html")

    @app.get("/tool/compare", include_in_schema=False)
    async def compare_tool_page() -> FileResponse:
        return FileResponse(WEB_DIR / "compare.html")

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
            background_scan_enabled=bool(runtime_config["background_scan_enabled"]),
            background_scan_interval_seconds=int(runtime_config["background_scan_interval_seconds"]),
            background_scan_task_running=bool(getattr(app.state, "background_scan_task", None)),
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

    @app.post("/api/compare", response_model=CompareResponse)
    async def compare_funds(request: CompareRequest) -> CompareResponse:
        return await comparison.compare(request)

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

    @app.get("/api/lof/opportunities", response_model=LofOpportunityResponse)
    async def lof_opportunities(
        normal_threshold_pct: float = Query(2.0, ge=0),
        strong_threshold_pct: float = Query(5.0, ge=0),
        min_turnover_yuan: float = Query(3_000_000, ge=0),
        limit: int = Query(80, ge=1, le=500),
        refresh: bool = Query(False),
        x_device_id: str | None = Header(None, alias="X-Device-Id"),
    ) -> LofOpportunityResponse:
        return await lof_monitor.get_opportunities(
            device_id=normalize_device_id(x_device_id),
            normal_threshold_pct=normal_threshold_pct,
            strong_threshold_pct=strong_threshold_pct,
            min_turnover_yuan=min_turnover_yuan,
            limit=limit,
            refresh=refresh,
        )

    @app.get("/api/etf/opportunities", response_model=EtfOpportunityResponse)
    async def etf_opportunities(
        normal_threshold_pct: float = Query(0.5, ge=0),
        strong_threshold_pct: float = Query(2.0, ge=0),
        min_turnover_yuan: float = Query(3_000_000, ge=0),
        limit: int = Query(80, ge=1, le=500),
        refresh: bool = Query(False),
    ) -> EtfOpportunityResponse:
        return await etf_monitor.get_opportunities(
            normal_threshold_pct=normal_threshold_pct,
            strong_threshold_pct=strong_threshold_pct,
            min_turnover_yuan=min_turnover_yuan,
            limit=limit,
            refresh=refresh,
        )

    @app.get("/api/lof/search", response_model=list[FundSearchResult])
    async def search_lofs(q: str = Query(..., min_length=1, max_length=64)) -> list[FundSearchResult]:
        return await lof_monitor.search_lofs(q)

    @app.get("/api/lof/watchlist", response_model=list[LofWatchlistItem])
    async def get_lof_watchlist(x_device_id: str | None = Header(None, alias="X-Device-Id")) -> list[LofWatchlistItem]:
        return lof_monitor.list_watchlist(normalize_device_id(x_device_id))

    @app.post("/api/lof/watchlist/{code}", response_model=LofWatchlistItem)
    async def add_lof_watchlist_item(
        code: str,
        x_device_id: str | None = Header(None, alias="X-Device-Id"),
    ) -> LofWatchlistItem:
        return await lof_monitor.add_watchlist(code, normalize_device_id(x_device_id))

    @app.delete("/api/lof/watchlist/{code}")
    async def delete_lof_watchlist_item(
        code: str,
        x_device_id: str | None = Header(None, alias="X-Device-Id"),
    ) -> dict[str, bool]:
        return {"deleted": lof_monitor.delete_watchlist(code, normalize_device_id(x_device_id))}

    @app.put("/api/lof/watchlist/order", response_model=list[LofWatchlistItem])
    async def reorder_lof_watchlist_items(
        request: WatchlistReorderRequest,
        x_device_id: str | None = Header(None, alias="X-Device-Id"),
    ) -> list[LofWatchlistItem]:
        return lof_monitor.reorder_watchlist(request.codes, normalize_device_id(x_device_id))

    @app.get("/api/lof/notice/status", response_model=LofNoticeStatus)
    async def lof_notice_status() -> LofNoticeStatus:
        return lof_notice.status()

    @app.put("/api/lof/notice/settings", response_model=LofNoticeStatus)
    async def lof_notice_settings(request: LofNoticeSettingsUpdate) -> LofNoticeStatus:
        return lof_notice.update_settings(
            enabled=request.enabled,
            daily_summary_time=request.daily_summary_time,
        )

    @app.post("/api/lof/notice/feishu/connect", response_model=LofFeishuConnectResponse)
    async def lof_notice_feishu_connect(request: Request) -> LofFeishuConnectResponse:
        callback_url = str(request.url_for("lof_notice_feishu_callback"))
        return lof_notice.begin_feishu_connect(callback_url=callback_url)

    @app.post("/api/lof/notice/feishu/poll", response_model=LofFeishuConnectResponse)
    async def lof_notice_feishu_poll() -> LofFeishuConnectResponse:
        return lof_notice.poll_feishu_connect()

    @app.get("/api/lof/notice/feishu/callback", include_in_schema=False)
    async def lof_notice_feishu_callback() -> HTMLResponse:
        html = f"""<!doctype html>
<html lang="zh-CN">
  <head><meta charset="utf-8" /><title>飞书接入完成</title></head>
  <body style="font-family: system-ui, sans-serif; padding: 32px;">
    <h1>飞书接入流程已切换为扫码模式</h1>
    <p>请回到监控页，点击“接入飞书”后扫描页面二维码。</p>
    <p><a href="/">返回监控页</a></p>
  </body>
</html>"""
        return HTMLResponse(html)

    @app.post("/api/lof/notice/feishu/disconnect", response_model=LofNoticeStatus)
    async def lof_notice_feishu_disconnect() -> LofNoticeStatus:
        return lof_notice.disconnect_feishu()

    @app.post("/api/lof/notice/test")
    async def lof_notice_test() -> dict[str, object]:
        return lof_notice.send_test()

    @app.get("/api/lof/{code}", response_model=LofPremiumItem)
    async def get_lof_item(
        code: str,
        x_device_id: str | None = Header(None, alias="X-Device-Id"),
    ) -> LofPremiumItem:
        return await lof_monitor.get_item(code, device_id=normalize_device_id(x_device_id))

    return app


app = create_app()
