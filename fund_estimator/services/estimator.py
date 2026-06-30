from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from fund_estimator.data_sources.eastmoney import infer_market, to_eastmoney_secid
from fund_estimator.models.schema import (
    EstimateModeResult,
    EstimateResponse,
    FundHoldings,
    FundProfile,
    HoldingEstimate,
    StockQuote,
    ThemeProxyEstimate,
)
from fund_estimator.services.cache import SQLiteCache
from fund_estimator.services.confidence import assess_confidence
from fund_estimator.services.exceptions import AppError, DataSourceError
from fund_estimator.services.theme_proxy import infer_theme_proxy

TModel = TypeVar("TModel", bound=BaseModel)


PROFILE_TTL_SECONDS = 10 * 60
HOLDINGS_TTL_SECONDS = 24 * 60 * 60
QUOTE_TTL_SECONDS = 15
MARKET_TZ = ZoneInfo("Asia/Shanghai")
MARKET_OPEN_TIME = time(9, 30)
PROXY_FUND_CODES: dict[str, str] = {
    # 易方达标普信息科技指数(QDII-LOF)A(美元现汇) -> 易方达标普信息科技指数(QDII-LOF)A人民币
    "003721": "161128",
}
EXCHANGE_TRADED_FUND_PREFIXES = ("159", "16", "50", "51", "52", "53", "56", "58", "588")


class FundEstimatorService:
    def __init__(
        self,
        *,
        fund_source: Any,
        holdings_source: Any,
        quote_source: Any,
        cache: SQLiteCache,
        mock_fund_source: Any | None = None,
        mock_holdings_source: Any | None = None,
        mock_quote_source: Any | None = None,
        allow_mock_fallback: bool = True,
        allow_mock_cache: bool | None = None,
    ) -> None:
        self.fund_source = fund_source
        self.holdings_source = holdings_source
        self.quote_source = quote_source
        self.cache = cache
        self.mock_fund_source = mock_fund_source
        self.mock_holdings_source = mock_holdings_source
        self.mock_quote_source = mock_quote_source
        self.allow_mock_fallback = allow_mock_fallback
        self.allow_mock_cache = allow_mock_fallback if allow_mock_cache is None else allow_mock_cache

    async def search_funds(self, query: str) -> list[Any]:
        try:
            results = await self.fund_source.search_funds(query)
            if results:
                return results
        except AppError:
            if not self.allow_mock_fallback or self.mock_fund_source is None:
                raise
        if self.allow_mock_fallback and self.mock_fund_source is not None:
            return await self.mock_fund_source.search_funds(query)
        return []

    async def list_funds(self) -> list[Any]:
        if hasattr(self.fund_source, "list_funds"):
            try:
                return await self.fund_source.list_funds()
            except AppError:
                if not self.allow_mock_fallback or self.mock_fund_source is None:
                    raise
        if self.allow_mock_fallback and self.mock_fund_source is not None:
            if hasattr(self.mock_fund_source, "list_funds"):
                return await self.mock_fund_source.list_funds()
            return await self.mock_fund_source.search_funds("")
        return []

    async def get_profile(self, code: str) -> FundProfile:
        self._validate_fund_code(code)
        return await self._cached_model(
            namespace="fund_profile",
            key=code,
            ttl_seconds=PROFILE_TTL_SECONDS,
            model_cls=FundProfile,
            fetcher=lambda: self._fetch_profile(code),
            allow_stale=True,
        )

    async def get_holdings(self, code: str) -> FundHoldings:
        self._validate_fund_code(code)
        return await self._cached_model(
            namespace="fund_holdings",
            key=code,
            ttl_seconds=HOLDINGS_TTL_SECONDS,
            model_cls=FundHoldings,
            fetcher=lambda: self._fetch_holdings(code),
            allow_stale=True,
        )

    async def estimate(
        self,
        code: str,
        *,
        mode: Literal["raw", "normalized", "enhanced", "both"] = "both",
    ) -> EstimateResponse:
        self._validate_fund_code(code)
        profile = await self.get_profile(code)
        official_nav_available = self._is_current_day_official_nav(profile)

        if official_nav_available:
            try:
                return await self._build_market_estimate_response(
                    code,
                    profile,
                    mode=mode,
                    official_nav_available=True,
                )
            except AppError as exc:
                response = self._build_official_nav_response(profile)
                response.warnings.append(f"未生成预估复盘值：{exc.message}")
                return response

        return await self._build_market_estimate_response(
            code,
            profile,
            mode=mode,
            official_nav_available=False,
        )

    async def _build_market_estimate_response(
        self,
        code: str,
        profile: FundProfile,
        *,
        mode: Literal["raw", "normalized", "enhanced", "both"],
        official_nav_available: bool,
    ) -> EstimateResponse:
        estimate_date = self._current_estimate_date()
        if official_nav_available:
            if profile.previous_nav is None or profile.previous_nav <= 0:
                raise AppError(
                    "PREVIOUS_NAV_NOT_AVAILABLE",
                    "缺少上一期官方净值，无法生成模型估值对比",
                    status_code=422,
                    details={"fund_code": code},
                )
            estimate_base_nav = profile.previous_nav
        else:
            estimate_base_nav = profile.last_nav
        try:
            holdings = await self.get_holdings(code)
        except AppError as exc:
            if exc.code == "HOLDINGS_NOT_AVAILABLE":
                return await self._build_proxy_estimate_response(
                    code,
                    profile,
                    mode=mode,
                    official_nav_available=official_nav_available,
                    estimate_base_nav=estimate_base_nav,
                )
            raise
        if not holdings.items:
            return await self._build_proxy_estimate_response(
                code,
                profile,
                mode=mode,
                official_nav_available=official_nav_available,
                estimate_base_nav=estimate_base_nav,
            )

        quoteable_codes = [
            item.stock_code
            for item in holdings.items
            if to_eastmoney_secid(item.stock_code) is not None
        ]
        unmapped = [item for item in holdings.items if item.stock_code not in quoteable_codes]
        if not quoteable_codes:
            raise AppError(
                "UNSUPPORTED_FUND_TYPE",
                "持仓资产无法映射到支持的实时行情代码",
                status_code=422,
                details={"fund_code": code},
            )

        quotes = await self._get_quotes(quoteable_codes)
        missing_quote_codes = [stock_code for stock_code in quoteable_codes if stock_code not in quotes]
        if not quotes:
            raise DataSourceError(
                "QUOTE_FETCH_FAILED",
                "实时股票行情获取失败",
                details={"fund_code": code, "stock_codes": quoteable_codes},
            )

        holding_estimates: list[HoldingEstimate] = []
        raw_return = 0.0
        usable_weight_sum = 0.0
        warnings: list[str] = []
        for item in holdings.items:
            quote = quotes.get(item.stock_code)
            if quote is None:
                warning = "无法获取实时行情" if item.stock_code in missing_quote_codes else "无法映射行情代码"
                holding_estimates.append(
                    HoldingEstimate(
                        stock_code=item.stock_code,
                        stock_name=item.stock_name,
                        market=item.market if item.market != "UNKNOWN" else infer_market(item.stock_code),
                        weight_pct=item.weight_pct,
                        used=False,
                        warning=warning,
                    )
                )
                continue

            contribution_pct = item.weight_pct * quote.change_pct / 100
            raw_return += (item.weight_pct / 100) * quote.change_ratio
            usable_weight_sum += item.weight_pct
            display_name = item.stock_name
            if not display_name or display_name.isdigit():
                display_name = quote.stock_name
            holding_estimates.append(
                HoldingEstimate(
                    stock_code=item.stock_code,
                    stock_name=display_name,
                    market=item.market if item.market != "UNKNOWN" else quote.market,
                    weight_pct=item.weight_pct,
                    latest_price=quote.latest_price,
                    previous_close=quote.previous_close,
                    change_pct=quote.change_pct,
                    contribution_pct=round(contribution_pct, 4),
                    used=True,
                )
            )

        if missing_quote_codes:
            warnings.append(f"以下持仓未获取到实时行情：{', '.join(missing_quote_codes)}")
        if unmapped:
            warnings.append(f"以下持仓无法映射行情代码：{', '.join(item.stock_code for item in unmapped)}")
        if profile.stale:
            warnings.append("基金净值使用了过期缓存数据")
        if holdings.stale:
            warnings.append("基金持仓使用了过期缓存数据")

        raw_result = self._build_mode_result("raw", estimate_base_nav, raw_return)
        normalized_result: EstimateModeResult | None = None
        if usable_weight_sum > 0:
            normalized_return = raw_return / (usable_weight_sum / 100)
            normalized_result = self._build_mode_result("normalized", estimate_base_nav, normalized_return)
        enhanced_result: EstimateModeResult | None = None
        theme_proxy: ThemeProxyEstimate | None = None
        enhanced = await self._build_enhanced_result(
            profile=profile,
            holdings=holdings,
            estimate_base_nav=estimate_base_nav,
            raw_return=raw_return,
            usable_weight_sum=usable_weight_sum,
        )
        if enhanced is not None:
            enhanced_result, theme_proxy = enhanced

        confidence, notes = assess_confidence(
            fund_type=profile.fund_type,
            holdings_date=holdings.holdings_date,
            top10_weight_sum=holdings.top10_weight_sum,
            usable_weight_sum=usable_weight_sum,
            missing_quote_count=len(missing_quote_codes),
            unmapped_count=len(unmapped),
            today=datetime.now(MARKET_TZ).date(),
        )

        if profile.source == "mock" or holdings.source == "mock" or any(q.source == "mock" for q in quotes.values()):
            notes.append("当前结果包含内置演示数据，真实投资研究应以实时数据源返回为准")
        if theme_proxy is not None:
            notes.append(
                f"增强估值使用关联板块“{theme_proxy.theme}”（{theme_proxy.proxy_name}）"
                f"代理未披露股票仓位约 {theme_proxy.weight_pct:.2f}%"
            )

        if official_nav_available:
            base_date = profile.previous_nav_date.isoformat() if profile.previous_nav_date else "上一期"
            notes = [
                "当天官方净值已经更新，当前主展示以官方净值为准",
                f"模型估值以 {base_date} 官方净值为基准，仅用于复盘对比",
            ] + notes

        selected = self._select_primary_result(
            mode=mode,
            raw_result=raw_result,
            normalized_result=normalized_result,
            enhanced_result=enhanced_result,
            top10_weight_sum=holdings.top10_weight_sum,
            usable_weight_sum=usable_weight_sum,
        )
        return EstimateResponse(
            fund_code=profile.code,
            fund_name=profile.name,
            fund_type=profile.fund_type,
            fund_details=profile.details,
            official_nav=profile.last_nav,
            official_nav_date=profile.nav_date,
            nav_date=profile.nav_date,
            last_nav=profile.last_nav,
            previous_nav_date=profile.previous_nav_date,
            previous_nav=profile.previous_nav,
            accumulated_nav=profile.accumulated_nav,
            estimate_time=datetime.now(UTC),
            valuation_status="official_nav" if official_nav_available else "estimated",
            is_official_nav=official_nav_available,
            holdings_date=holdings.holdings_date,
            top10_weight_sum=holdings.top10_weight_sum,
            usable_weight_sum=round(usable_weight_sum, 4),
            primary_mode=selected.mode,
            estimated_nav=selected.estimated_nav,
            estimated_nav_date=estimate_date,
            estimated_change_pct=selected.estimated_change_pct,
            actual_change_pct=profile.actual_change_pct,
            actual_change_date=profile.nav_date,
            raw=raw_result if mode in {"raw", "both"} else None,
            normalized=normalized_result if mode in {"normalized", "both"} else None,
            enhanced=enhanced_result if mode in {"enhanced", "both"} else None,
            theme_proxy=theme_proxy,
            confidence=confidence,
            notes=notes,
            warnings=warnings,
            holdings=holding_estimates,
            data_source=self._merge_sources(profile.source, holdings.source, quotes.values()),
        )

    async def _build_proxy_estimate_response(
        self,
        code: str,
        profile: FundProfile,
        *,
        mode: Literal["raw", "normalized", "enhanced", "both"],
        official_nav_available: bool,
        estimate_base_nav: float,
    ) -> EstimateResponse:
        estimate_date = self._current_estimate_date()
        proxy_quote = await self._find_proxy_quote(profile)
        if proxy_quote is None:
            raise AppError("HOLDINGS_NOT_AVAILABLE", f"基金 {code} 没有可解析的前十大持仓", status_code=422)

        return_ratio = proxy_quote.change_ratio
        raw_result = self._build_mode_result(
            "raw",
            estimate_base_nav,
            return_ratio,
            method="proxy_exchange_traded_fund_return",
        )
        normalized_result = self._build_mode_result(
            "normalized",
            estimate_base_nav,
            return_ratio,
            method="proxy_exchange_traded_fund_return",
        )
        enhanced_result = self._build_mode_result(
            "enhanced",
            estimate_base_nav,
            return_ratio,
            method="proxy_exchange_traded_fund_return",
        )
        selected = enhanced_result if mode in {"enhanced", "both"} else normalized_result if mode == "normalized" else raw_result
        warnings: list[str] = []
        if profile.stale:
            warnings.append("基金净值使用了过期缓存数据")

        notes = [
            f"未取得可解析前十大持仓，使用场内代理 {proxy_quote.stock_code} 的实时涨跌估算大致走势",
            "代理估算可能受汇率、溢价率、跟踪误差和跨市场交易时间影响，置信度较低",
            "非官方净值，仅供研究和参考，不构成投资建议",
        ]
        if official_nav_available:
            base_date = profile.previous_nav_date.isoformat() if profile.previous_nav_date else "上一期"
            notes.insert(0, "当天官方净值已经更新，当前主展示以官方净值为准")
            notes.insert(1, f"模型估值以 {base_date} 官方净值为基准，仅用于复盘对比")

        proxy_holding = HoldingEstimate(
            stock_code=proxy_quote.stock_code,
            stock_name=f"代理标的：{proxy_quote.stock_name}",
            market=proxy_quote.market,
            weight_pct=100,
            latest_price=proxy_quote.latest_price,
            previous_close=proxy_quote.previous_close,
            change_pct=proxy_quote.change_pct,
            contribution_pct=proxy_quote.change_pct,
            used=True,
            warning="场内代理走势，不是基金真实持仓",
        )
        return EstimateResponse(
            fund_code=profile.code,
            fund_name=profile.name,
            fund_type=profile.fund_type,
            fund_details=profile.details,
            official_nav=profile.last_nav,
            official_nav_date=profile.nav_date,
            nav_date=profile.nav_date,
            last_nav=profile.last_nav,
            previous_nav_date=profile.previous_nav_date,
            previous_nav=profile.previous_nav,
            accumulated_nav=profile.accumulated_nav,
            estimate_time=datetime.now(UTC),
            valuation_status="official_nav" if official_nav_available else "estimated",
            is_official_nav=official_nav_available,
            holdings_date=None,
            top10_weight_sum=0,
            usable_weight_sum=0,
            primary_mode=selected.mode,
            estimated_nav=selected.estimated_nav,
            estimated_nav_date=estimate_date,
            estimated_change_pct=selected.estimated_change_pct,
            actual_change_pct=profile.actual_change_pct,
            actual_change_date=profile.nav_date,
            raw=raw_result if mode in {"raw", "both"} else None,
            normalized=normalized_result if mode in {"normalized", "both"} else None,
            enhanced=enhanced_result if mode in {"enhanced", "both"} else None,
            confidence="low",
            notes=notes,
            warnings=warnings,
            holdings=[proxy_holding],
            data_source=f"profile:{profile.source}, proxy_quote:{proxy_quote.source}",
        )

    async def _find_proxy_quote(self, profile: FundProfile) -> StockQuote | None:
        proxy_codes = await self._proxy_candidates(profile)
        for proxy_code in proxy_codes:
            try:
                quotes = await self._get_quotes([proxy_code])
            except AppError:
                continue
            quote = quotes.get(proxy_code)
            if quote is not None:
                return quote
        return None

    async def _proxy_candidates(self, profile: FundProfile) -> list[str]:
        candidates: list[str] = []
        override = PROXY_FUND_CODES.get(profile.code)
        if override:
            candidates.append(override)
        name = profile.name or ""
        if "标普信息科技" in name and "161128" not in candidates:
            candidates.append("161128")
        query = self._proxy_search_query(name)
        if query:
            try:
                results = await self.fund_source.search_funds(query)
            except AppError:
                results = []
            for item in results:
                if item.code != profile.code and self._looks_exchange_traded_fund_code(item.code):
                    if item.code not in candidates:
                        candidates.append(item.code)
        return candidates

    @staticmethod
    def _proxy_search_query(name: str) -> str:
        base = name.split("(")[0].split("（")[0]
        base = base.replace("美元现汇", "").replace("美元现钞", "").replace("人民币", "")
        return base.strip()[:16]

    @staticmethod
    def _looks_exchange_traded_fund_code(code: str) -> bool:
        return code.startswith(EXCHANGE_TRADED_FUND_PREFIXES)

    def _build_official_nav_response(self, profile: FundProfile) -> EstimateResponse:
        warnings: list[str] = []
        if profile.stale:
            warnings.append("基金净值使用了过期缓存数据")
        return EstimateResponse(
            fund_code=profile.code,
            fund_name=profile.name,
            fund_type=profile.fund_type,
            fund_details=profile.details,
            official_nav=profile.last_nav,
            official_nav_date=profile.nav_date,
            nav_date=profile.nav_date,
            last_nav=profile.last_nav,
            previous_nav_date=profile.previous_nav_date,
            previous_nav=profile.previous_nav,
            accumulated_nav=profile.accumulated_nav,
            estimate_time=datetime.now(UTC),
            valuation_status="official_nav",
            is_official_nav=True,
            holdings_date=None,
            top10_weight_sum=0,
            usable_weight_sum=0,
            primary_mode="raw",
            estimated_nav=round(profile.last_nav, 4),
            estimated_nav_date=None,
            estimated_change_pct=None,
            actual_change_pct=profile.actual_change_pct,
            actual_change_date=profile.nav_date,
            raw=None,
            normalized=None,
            enhanced=None,
            theme_proxy=None,
            confidence="high",
            notes=[
                "官方净值已经更新，当前返回基金公司/数据源披露的正式净值",
                "开市前不重新计算盘中预估值",
            ],
            warnings=warnings,
            holdings=[],
            data_source=f"profile:{profile.source}",
        )

    @staticmethod
    def _select_primary_result(
        *,
        mode: Literal["raw", "normalized", "enhanced", "both"],
        raw_result: EstimateModeResult,
        normalized_result: EstimateModeResult | None,
        enhanced_result: EstimateModeResult | None,
        top10_weight_sum: float,
        usable_weight_sum: float,
    ) -> EstimateModeResult:
        if mode == "raw" or normalized_result is None:
            return raw_result
        if mode == "normalized":
            return normalized_result
        if mode == "enhanced":
            return enhanced_result or raw_result
        if enhanced_result is not None:
            return enhanced_result
        return raw_result

    async def _fetch_profile(self, code: str) -> FundProfile:
        try:
            return await self.fund_source.get_profile(code)
        except AppError as exc:
            if not self.allow_mock_fallback or self.mock_fund_source is None:
                raise
            try:
                return await self.mock_fund_source.get_profile(code)
            except AppError:
                if isinstance(exc, DataSourceError):
                    raise exc
                raise

    async def _fetch_holdings(self, code: str) -> FundHoldings:
        try:
            return await self.holdings_source.get_holdings(code)
        except AppError as exc:
            if not self.allow_mock_fallback or self.mock_holdings_source is None:
                raise
            try:
                return await self.mock_holdings_source.get_holdings(code)
            except AppError:
                if isinstance(exc, DataSourceError):
                    raise exc
                raise

    async def _get_quotes(self, stock_codes: list[str]) -> dict[str, StockQuote]:
        cached: dict[str, StockQuote] = {}
        missing: list[str] = []
        for code in stock_codes:
            payload = self.cache.get("stock_quote", code)
            if payload and self._cache_payload_allowed(payload):
                cached[code] = StockQuote.model_validate(payload)
            else:
                missing.append(code)

        fetched: dict[str, StockQuote] = {}
        if missing:
            try:
                fetched = await self.quote_source.get_quotes(missing)
            except AppError:
                if not self.allow_mock_fallback or self.mock_quote_source is None:
                    raise
                fetched = await self.mock_quote_source.get_quotes(missing)
            for code, quote in fetched.items():
                self.cache.set("stock_quote", code, quote.model_dump(mode="json"), QUOTE_TTL_SECONDS)

        return {**cached, **fetched}

    async def _cached_model(
        self,
        *,
        namespace: str,
        key: str,
        ttl_seconds: int,
        model_cls: type[TModel],
        fetcher: Callable[[], Awaitable[TModel]],
        allow_stale: bool,
    ) -> TModel:
        cached = self.cache.get(namespace, key)
        if cached and self._cache_payload_allowed(cached):
            return model_cls.model_validate(cached)

        try:
            model = await fetcher()
            self.cache.set(namespace, key, model.model_dump(mode="json"), ttl_seconds)
            return model
        except AppError:
            if not allow_stale:
                raise
            stale_payload = self.cache.get(namespace, key, include_expired=True)
            if stale_payload and self._cache_payload_allowed(stale_payload):
                stale_payload["stale"] = True
                return model_cls.model_validate(stale_payload)
            raise

    def _cache_payload_allowed(self, payload: dict[str, Any]) -> bool:
        if self.allow_mock_cache:
            return True
        return payload.get("source") != "mock"

    @staticmethod
    def _build_mode_result(
        mode: Literal["raw", "normalized", "enhanced"],
        last_nav: float,
        return_ratio: float,
        *,
        method: str = "top10_holdings_weighted_return",
    ) -> EstimateModeResult:
        estimated_nav = last_nav * (1 + return_ratio)
        return EstimateModeResult(
            mode=mode,
            estimated_nav=round(estimated_nav, 4),
            estimated_change_pct=round(return_ratio * 100, 4),
            portfolio_return_pct=round(return_ratio * 100, 4),
            method=method,
        )

    async def _build_enhanced_result(
        self,
        *,
        profile: FundProfile,
        holdings: FundHoldings,
        estimate_base_nav: float,
        raw_return: float,
        usable_weight_sum: float,
    ) -> tuple[EstimateModeResult, ThemeProxyEstimate] | None:
        stock_pct = profile.details.asset_allocation.stock_pct
        if stock_pct is None:
            return None
        residual_stock_weight = max(0.0, min(float(stock_pct), 100.0) - usable_weight_sum)
        if residual_stock_weight < 1:
            return None

        candidate = infer_theme_proxy(profile, holdings)
        if candidate is None:
            return None
        try:
            quotes = await self._get_quotes([candidate.proxy_code])
        except AppError:
            return None
        proxy_quote = quotes.get(candidate.proxy_code)
        if proxy_quote is None:
            return None

        proxy_contribution_pct = residual_stock_weight * proxy_quote.change_pct / 100
        enhanced_return = raw_return + (residual_stock_weight / 100) * proxy_quote.change_ratio
        theme_proxy = ThemeProxyEstimate(
            theme=candidate.theme,
            proxy_code=proxy_quote.stock_code,
            proxy_name=proxy_quote.stock_name,
            change_pct=round(proxy_quote.change_pct, 4),
            weight_pct=round(residual_stock_weight, 4),
            contribution_pct=round(proxy_contribution_pct, 4),
            source=proxy_quote.source,
        )
        return (
            self._build_mode_result(
                "enhanced",
                estimate_base_nav,
                enhanced_return,
                method="top10_holdings_plus_theme_proxy",
            ),
            theme_proxy,
        )

    @staticmethod
    def _is_current_day_official_nav(profile: FundProfile) -> bool:
        estimate_date = FundEstimatorService._current_estimate_date()
        return profile.nav_date >= estimate_date

    @staticmethod
    def _current_estimate_date(now: datetime | None = None) -> date:
        now = now or datetime.now(MARKET_TZ)
        current_date = now.date()
        if now.timetz().replace(tzinfo=None) < MARKET_OPEN_TIME:
            return FundEstimatorService._previous_trading_day(current_date)
        return current_date

    @staticmethod
    def _previous_trading_day(current_date: date) -> date:
        candidate = current_date - timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        return candidate

    @staticmethod
    def _validate_fund_code(code: str) -> None:
        if not code.isdigit() or len(code) != 6:
            raise AppError(
                "INVALID_FUND_CODE",
                "基金代码必须是6位数字",
                status_code=422,
                details={"code": code},
            )

    @staticmethod
    def _merge_sources(profile_source: str, holdings_source: str, quotes: Any) -> str:
        quote_sources = sorted({quote.source for quote in quotes})
        parts = [f"profile:{profile_source}", f"holdings:{holdings_source}"]
        if quote_sources:
            parts.append(f"quotes:{'/'.join(quote_sources)}")
        return ", ".join(parts)
