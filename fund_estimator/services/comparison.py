from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import combinations

import httpx
from lxml import html

from fund_estimator.data_sources.eastmoney import DEFAULT_HEADERS
from fund_estimator.models.schema import (
    CompareConclusion,
    CompareFundResult,
    CompareFundSnapshot,
    ComparePairSimilarity,
    CompareRequest,
    CompareResponse,
    CompareScoreFactor,
    CompareScoreBreakdown,
    CompareStrategy,
    EstimateResponse,
    FundHoldings,
    FundProfile,
)
from fund_estimator.services.estimator import FundEstimatorService
from fund_estimator.services.exceptions import AppError
from fund_estimator.services.http_settings import http_trust_env


THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "科技": ("科技", "信息技术", "软件", "计算机", "云计算", "人工智能", "ai", "通信", "电子"),
    "半导体": ("半导体", "芯片", "集成电路", "电子"),
    "新能源": ("新能源", "电池", "光伏", "锂电", "储能", "电动车", "新能源汽车"),
    "医药": ("医药", "医疗", "生物", "创新药", "健康"),
    "消费": ("消费", "食品", "饮料", "白酒", "家电"),
    "金融": ("金融", "银行", "证券", "保险"),
    "互联网": ("互联网", "中概", "港股通互联网", "海外中国互联网"),
    "军工": ("军工", "国防"),
    "红利": ("红利", "股息", "高股息"),
    "油气": ("原油", "油气", "石油", "能源"),
    "黄金": ("黄金", "贵金属"),
    "债券": ("债", "信用债", "利率债", "可转债"),
}

STRATEGY_LABELS: dict[CompareStrategy, str] = {
    "balanced": "稳健综合",
    "aggressive": "收益进攻",
    "low_cost": "低波动稳健",
}

STRATEGY_WEIGHTS: dict[CompareStrategy, dict[str, float]] = {
    "balanced": {
        "performance": 0.25,
        "ranking": 0.20,
        "scale": 0.13,
        "allocation": 0.14,
        "holdings": 0.11,
        "manager": 0.09,
        "similarity": 0.08,
    },
    "aggressive": {
        "performance": 0.42,
        "ranking": 0.22,
        "scale": 0.07,
        "allocation": 0.10,
        "holdings": 0.05,
        "manager": 0.09,
        "similarity": 0.05,
    },
    "low_cost": {
        "performance": 0.08,
        "ranking": 0.08,
        "scale": 0.21,
        "allocation": 0.25,
        "holdings": 0.18,
        "manager": 0.08,
        "similarity": 0.12,
    },
}

SCORE_FACTOR_LABELS: dict[str, str] = {
    "performance": "历史收益",
    "ranking": "同类表现",
    "scale": "规模",
    "allocation": "配置风险",
    "holdings": "持仓结构",
    "manager": "基金经理",
    "similarity": "可比性",
}

SCORE_FACTOR_BASIS: dict[str, str] = {
    "performance": "近1月、近3月、近6月、近1年阶段收益，结合绝对表现和候选基金内相对表现。",
    "ranking": "基金在同类中的排名或百分位，用来避免只和不同赛道基金直接比收益。",
    "scale": "规模过小有流动性/清盘风险，过大可能影响策略弹性，中等规模更优。",
    "allocation": "股票、债券、现金仓位与当前策略口径的匹配度，也作为风险暴露的代理指标。",
    "holdings": "前十大持仓占比反映集中度；过度集中会降分，缺失时只给中性偏低分。",
    "manager": "基金经理任职年限、管理规模和星级等披露信息；缺失时不直接判负。",
    "similarity": "候选基金之间的主题、类型、资产配置和持仓相似度，只作为小权重校准。",
}


@dataclass
class CompareCandidate:
    profile: FundProfile
    holdings: FundHoldings | None = None
    estimate: EstimateResponse | None = None
    purchase_limit_yuan: float | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def code(self) -> str:
        return self.profile.code

    @property
    def name(self) -> str:
        return self.profile.name

    @property
    def fund_type(self) -> str | None:
        return self.profile.fund_type


class FundComparisonService:
    def __init__(self, estimator: FundEstimatorService) -> None:
        self.estimator = estimator

    async def compare(self, request: CompareRequest) -> CompareResponse:
        candidates = [await self._load_candidate(code) for code in request.codes]
        pair_similarities = self._build_pair_similarities(candidates, request.theme_hint)
        conclusion = self._classify_group(pair_similarities)
        score_inputs = self._build_score_inputs(candidates, pair_similarities, request.strategy)
        fund_results = self._build_fund_results(candidates, score_inputs, conclusion, request.strategy)
        recommendation_code, recommendation = self._build_recommendation(conclusion, fund_results, pair_similarities, request.strategy)
        warnings = self._global_warnings(candidates, pair_similarities, conclusion)
        return CompareResponse(
            generated_at=datetime.now(UTC),
            strategy=request.strategy,
            theme_hint=request.theme_hint,
            conclusion=conclusion,
            conclusion_title=self._conclusion_title(conclusion),
            recommendation_code=recommendation_code,
            recommendation=recommendation,
            funds=fund_results,
            pair_similarities=pair_similarities,
            score_factors=self._score_factors(request.strategy),
            warnings=warnings,
        )

    async def _load_candidate(self, code: str) -> CompareCandidate:
        profile = await self.estimator.get_profile(code)
        candidate = CompareCandidate(profile=profile)
        try:
            candidate.holdings = await self.estimator.get_holdings(code)
        except AppError as exc:
            candidate.warnings.append(f"持仓数据不可用：{exc.message}")
        try:
            candidate.estimate = await self.estimator.estimate(code, mode="both")
        except AppError:
            candidate.estimate = None
        candidate.purchase_limit_yuan = await self._load_purchase_limit_yuan(code, profile)
        if candidate.purchase_limit_yuan is None:
            candidate.purchase_limit_yuan = self._profile_purchase_limit_yuan(profile)
        if profile.stale:
            candidate.warnings.append("基金画像使用了过期缓存数据")
        return candidate

    async def _load_purchase_limit_yuan(self, code: str, profile: FundProfile) -> float | None:
        if profile.source != "eastmoney":
            return None
        url = f"https://fundf10.eastmoney.com/jjfl_{code}.html"
        try:
            async with httpx.AsyncClient(timeout=4.0, headers=DEFAULT_HEADERS, trust_env=http_trust_env()) as client:
                response = await client.get(url)
                response.raise_for_status()
        except httpx.HTTPError:
            return None
        return self._extract_purchase_limit_yuan(response.text)

    @staticmethod
    def _extract_purchase_limit_yuan(text: str) -> float | None:
        try:
            doc = html.fromstring(text)
            visible_text = " ".join(part.strip() for part in doc.xpath("//text()") if part.strip())
        except (ValueError, TypeError):
            visible_text = text
        compact = re.sub(r"\s+", "", visible_text)
        limit_keys = (
            r"(?:日累计(?:申购限额|购买限额|购买上限)|"
            r"单日累计(?:申购限额|购买限额|购买上限)|"
            r"单日单账户累计(?:申购|购买)?(?:限额|上限)|"
            r"单日(?:申购限额|购买限额|购买上限)|"
            r"每日(?:申购限额|购买限额|购买上限)|"
            r"(?:暂停|限制)?大额(?:申购|购买)(?:限额|上限)?|"
            r"申购限额|购买限额|申购上限|购买上限|限购)"
        )
        min_purchase_terms = r"(?:最低|起购|起点|申购起点|购买起点|定投起点|首次申购|追加申购|首次购买|追加购买|最小)"
        separator = r"(?:[:：=为是]|不超过|不高于|最高|上限为|限制为|人民币)?"
        patterns = (
            ("key_before", rf"(?P<key>{limit_keys})(?P<sep>{separator})(?P<value>\d+(?:\.\d+)?)(?P<unit>万|元)"),
            ("value_before", rf"(?P<value>\d+(?:\.\d+)?)(?P<unit>万|元)(?P<sep>{separator})(?P<key>{limit_keys})"),
        )
        for direction, pattern in patterns:
            match = re.search(pattern, compact)
            if not match:
                continue
            prefix_context = compact[max(0, match.start() - 16) : match.start()]
            if direction == "value_before" and re.search(min_purchase_terms, prefix_context):
                continue
            value = float(match.group("value"))
            unit = match.group("unit")
            return value * 10_000 if unit == "万" else value
        return None

    @staticmethod
    def _profile_purchase_limit_yuan(profile: FundProfile) -> float | None:
        limit = profile.details.trading.purchase_limit_yuan
        if limit is None or limit <= 0:
            return None
        min_purchase = profile.details.trading.min_purchase_amount
        if min_purchase is not None and abs(float(limit) - float(min_purchase)) < 0.01:
            return None
        return limit

    def _build_pair_similarities(
        self,
        candidates: list[CompareCandidate],
        theme_hint: str | None,
    ) -> list[ComparePairSimilarity]:
        pairs: list[ComparePairSimilarity] = []
        for left, right in combinations(candidates, 2):
            holdings_similarity = self._holdings_similarity(left.holdings, right.holdings)
            allocation_similarity = self._allocation_similarity(left.profile, right.profile)
            profile_similarity = self._profile_similarity(left, right)
            theme_similarity = self._theme_similarity(left, right, theme_hint)
            if holdings_similarity is None:
                overall = 0.45 * profile_similarity + 0.25 * (allocation_similarity or profile_similarity) + 0.30 * theme_similarity
            else:
                overall = (
                    0.50 * holdings_similarity
                    + 0.25 * profile_similarity
                    + 0.15 * (allocation_similarity or profile_similarity)
                    + 0.10 * theme_similarity
                )
            relation = self._classify_pair(overall, holdings_similarity, profile_similarity, theme_similarity)
            pairs.append(
                ComparePairSimilarity(
                    code_a=left.code,
                    code_b=right.code,
                    overall_similarity=round(overall * 100, 2),
                    holdings_similarity=self._round_pct(holdings_similarity),
                    profile_similarity=round(profile_similarity * 100, 2),
                    allocation_similarity=self._round_pct(allocation_similarity),
                    theme_similarity=round(theme_similarity * 100, 2),
                    relation=relation,
                    reasons=self._pair_reasons(holdings_similarity, profile_similarity, allocation_similarity, theme_similarity),
                )
            )
        return pairs

    @staticmethod
    def _holdings_similarity(left: FundHoldings | None, right: FundHoldings | None) -> float | None:
        if left is None or right is None or not left.items or not right.items:
            return None
        left_weights = {item.stock_code: item.weight_pct for item in left.items}
        right_weights = {item.stock_code: item.weight_pct for item in right.items}
        all_codes = set(left_weights) | set(right_weights)
        common_codes = set(left_weights) & set(right_weights)
        if not all_codes:
            return None
        left_sum = sum(left_weights.values())
        right_sum = sum(right_weights.values())
        denominator = max(left_sum + right_sum, 1)
        total_diff = sum(abs(left_weights.get(code, 0.0) - right_weights.get(code, 0.0)) for code in all_codes)
        closeness = 1 - total_diff / denominator
        overlap_count = len(common_codes) / max(len(left_weights), len(right_weights), 1)
        weighted_overlap = sum(min(left_weights[code], right_weights[code]) for code in common_codes) / max(left_sum, right_sum, 1)
        return _clamp(0.45 * closeness + 0.35 * weighted_overlap + 0.20 * overlap_count, 0, 1)

    @staticmethod
    def _allocation_similarity(left: FundProfile, right: FundProfile) -> float | None:
        left_allocation = left.details.asset_allocation
        right_allocation = right.details.asset_allocation
        pairs = [
            (left_allocation.stock_pct, right_allocation.stock_pct),
            (left_allocation.bond_pct, right_allocation.bond_pct),
            (left_allocation.cash_pct, right_allocation.cash_pct),
        ]
        usable = [(float(a), float(b)) for a, b in pairs if a is not None and b is not None]
        if len(usable) < 2:
            return None
        avg_diff = sum(abs(a - b) for a, b in usable) / len(usable)
        return _clamp(1 - avg_diff / 100, 0, 1)

    def _profile_similarity(self, left: CompareCandidate, right: CompareCandidate) -> float:
        left_asset, left_tags = self._type_tags(left)
        right_asset, right_tags = self._type_tags(right)
        if left_asset == right_asset:
            asset_score = 0.92
        elif left_asset in {"equity", "mixed", "index"} and right_asset in {"equity", "mixed", "index"}:
            asset_score = 0.66
        else:
            asset_score = 0.12
        tag_score = len(left_tags & right_tags) / max(len(left_tags | right_tags), 1)
        return _clamp(0.75 * asset_score + 0.25 * tag_score, 0, 1)

    @staticmethod
    def _type_tags(candidate: CompareCandidate) -> tuple[str, set[str]]:
        text = f"{candidate.name} {candidate.fund_type or ''}".lower()
        tags: set[str] = set()
        if "qdii" in text:
            tags.add("qdii")
        if "lof" in text:
            tags.add("lof")
        if "etf" in text:
            tags.add("etf")
        if "指数" in text or "index" in text:
            tags.add("index")
        if "债" in text:
            return "bond", tags | {"bond"}
        if "货币" in text:
            return "money", tags | {"money"}
        if "黄金" in text or "原油" in text or "油气" in text:
            return "commodity", tags | {"commodity"}
        if "qdii" in tags:
            return "qdii", tags
        if "股票" in text:
            return "equity", tags | {"equity"}
        if "混合" in text:
            return "mixed", tags | {"mixed"}
        if "index" in tags or "etf" in tags or "lof" in tags:
            return "index", tags
        return "unknown", tags

    def _theme_similarity(self, left: CompareCandidate, right: CompareCandidate, theme_hint: str | None) -> float:
        left_text = self._search_text(left)
        right_text = self._search_text(right)
        left_themes = self._theme_tokens(left_text)
        right_themes = self._theme_tokens(right_text)
        if theme_hint:
            hint = theme_hint.strip().lower()
            left_matches_hint = hint in left_text
            right_matches_hint = hint in right_text
            if left_matches_hint and right_matches_hint:
                return 1.0
            if left_matches_hint or right_matches_hint:
                return 0.35
        if left_themes or right_themes:
            return len(left_themes & right_themes) / max(len(left_themes | right_themes), 1)
        return 0.45

    @staticmethod
    def _search_text(candidate: CompareCandidate) -> str:
        managers = " ".join(manager.name for manager in candidate.profile.details.managers)
        return f"{candidate.name} {candidate.fund_type or ''} {managers}".lower()

    @staticmethod
    def _theme_tokens(text: str) -> set[str]:
        return {theme for theme, keywords in THEME_KEYWORDS.items() if any(keyword.lower() in text for keyword in keywords)}

    @staticmethod
    def _classify_pair(
        overall: float,
        holdings_similarity: float | None,
        profile_similarity: float,
        theme_similarity: float,
    ) -> CompareConclusion:
        if overall >= 0.72 and (holdings_similarity is None or holdings_similarity >= 0.50):
            return "very_similar"
        if overall >= 0.42 or (profile_similarity >= 0.62 and theme_similarity >= 0.50):
            return "same_theme_different"
        return "not_comparable"

    @staticmethod
    def _classify_group(pair_similarities: list[ComparePairSimilarity]) -> CompareConclusion:
        if not pair_similarities:
            return "not_comparable"
        relations = [pair.relation for pair in pair_similarities]
        average = sum(pair.overall_similarity for pair in pair_similarities) / len(pair_similarities)
        not_comparable_ratio = relations.count("not_comparable") / len(relations)
        if all(relation == "very_similar" for relation in relations):
            return "very_similar"
        if not_comparable_ratio >= 0.34 or average < 42:
            return "not_comparable"
        return "same_theme_different"

    def _build_score_inputs(
        self,
        candidates: list[CompareCandidate],
        pair_similarities: list[ComparePairSimilarity],
        strategy: CompareStrategy,
    ) -> dict[str, dict[str, float]]:
        performance_values = {candidate.code: self._performance_metric(candidate.profile) for candidate in candidates}
        performance_relative = _relative_scores(performance_values, higher_better=True, default=55)
        similarity_scores = self._candidate_similarity_scores(candidates, pair_similarities)
        scores: dict[str, dict[str, float]] = {}
        for candidate in candidates:
            details = candidate.profile.details
            performance_score = 0.45 * self._absolute_performance_score(performance_values[candidate.code]) + 0.55 * performance_relative[candidate.code]
            breakdown = {
                "performance": performance_score,
                "ranking": self._rank_score(details.similar_rank.percentile_pct, details.similar_rank.rank, details.similar_rank.total),
                "scale": self._scale_score(details.scale_billion or details.asset_allocation.net_asset_billion),
                "allocation": self._allocation_score(details.asset_allocation.stock_pct, details.asset_allocation.bond_pct, strategy),
                "holdings": self._holdings_score(candidate.holdings.top10_weight_sum if candidate.holdings else None),
                "manager": self._manager_score(details.managers),
                "similarity": similarity_scores.get(candidate.code, 45.0),
            }
            scores[candidate.code] = {key: round(value, 2) for key, value in breakdown.items()}
        return scores

    @staticmethod
    def _performance_metric(profile: FundProfile) -> float | None:
        returns = profile.details.stage_returns
        weighted_values = [
            (returns.one_month_pct, 0.20),
            (returns.three_month_pct, 0.30),
            (returns.six_month_pct, 0.25),
            (returns.one_year_pct, 0.25),
        ]
        usable = [(float(value), weight) for value, weight in weighted_values if value is not None]
        if not usable:
            return None
        total_weight = sum(weight for _, weight in usable)
        return sum(value * weight for value, weight in usable) / total_weight

    @staticmethod
    def _absolute_performance_score(value: float | None) -> float:
        if value is None:
            return 55
        return _clamp(50 + value * 1.2, 0, 100)

    @staticmethod
    def _rank_score(percentile: float | None, rank: int | None, total: int | None) -> float:
        if percentile is not None:
            return _clamp(float(percentile), 0, 100)
        if rank is not None and total:
            return _clamp((1 - (rank - 1) / total) * 100, 0, 100)
        return 55

    @staticmethod
    def _scale_score(scale_billion: float | None) -> float:
        if scale_billion is None:
            return 55
        scale = float(scale_billion)
        if scale < 0.5:
            return 45
        if scale < 2:
            return 65
        if scale <= 80:
            return 90
        if scale <= 150:
            return 78
        if scale <= 300:
            return 62
        return 50

    @staticmethod
    def _allocation_score(stock_pct: float | None, bond_pct: float | None, strategy: CompareStrategy) -> float:
        if stock_pct is None:
            return 55
        stock = float(stock_pct)
        bond = float(bond_pct or 0)
        if strategy == "aggressive":
            if 80 <= stock <= 98:
                return 92
            if 60 <= stock < 80:
                return 76
            if stock > 98:
                return 70
            return 42 if bond < 50 else 35
        if strategy == "low_cost":
            if 45 <= stock <= 85:
                return 88
            if 85 < stock <= 95:
                return 72
            if stock > 95:
                return 55
            return 76 if bond >= 35 else 62
        if 65 <= stock <= 92:
            return 90
        if 45 <= stock < 65:
            return 72
        if 92 < stock <= 98:
            return 78
        return 56

    @staticmethod
    def _holdings_score(top10_weight_sum: float | None) -> float:
        if top10_weight_sum is None:
            return 55
        weight = float(top10_weight_sum)
        if 30 <= weight <= 65:
            return 90
        if 20 <= weight < 30:
            return 78
        if 65 < weight <= 78:
            return 72
        if 78 < weight <= 90:
            return 58
        return 52

    @staticmethod
    def _manager_score(managers: list) -> float:
        if not managers:
            return 55
        scores: list[float] = []
        for manager in managers:
            tenure_years = _parse_manager_tenure_years(getattr(manager, "work_time", None))
            tenure_score = 55.0
            if tenure_years is not None:
                if tenure_years >= 7:
                    tenure_score = 92
                elif tenure_years >= 5:
                    tenure_score = 86
                elif tenure_years >= 3:
                    tenure_score = 76
                elif tenure_years >= 1:
                    tenure_score = 62
                else:
                    tenure_score = 48
            star = getattr(manager, "star", None)
            if star is not None:
                star_score = _clamp(40 + float(star) * 12, 40, 100)
                scores.append(0.65 * tenure_score + 0.35 * star_score)
            else:
                scores.append(tenure_score)
        return sum(scores) / len(scores)

    @staticmethod
    def _candidate_similarity_scores(
        candidates: list[CompareCandidate],
        pair_similarities: list[ComparePairSimilarity],
    ) -> dict[str, float]:
        scores: dict[str, list[float]] = {candidate.code: [] for candidate in candidates}
        for pair in pair_similarities:
            scores[pair.code_a].append(pair.overall_similarity)
            scores[pair.code_b].append(pair.overall_similarity)
        return {code: round(sum(values) / len(values), 2) if values else 45.0 for code, values in scores.items()}

    def _build_fund_results(
        self,
        candidates: list[CompareCandidate],
        score_inputs: dict[str, dict[str, float]],
        conclusion: CompareConclusion,
        strategy: CompareStrategy,
    ) -> list[CompareFundResult]:
        weights = STRATEGY_WEIGHTS[strategy]
        results: list[CompareFundResult] = []
        for candidate in candidates:
            scores = score_inputs[candidate.code]
            total_score = round(sum(scores[key] * weight for key, weight in weights.items()), 2)
            snapshot = self._snapshot(candidate)
            results.append(
                CompareFundResult(
                    code=candidate.code,
                    name=candidate.name,
                    fund_type=candidate.fund_type,
                    rank=None,
                    total_score=total_score,
                    score_breakdown=CompareScoreBreakdown(**scores),
                    snapshot=snapshot,
                    reasons=self._fund_reasons(scores, candidate),
                    warnings=candidate.warnings,
                    recommended=False,
                )
            )
        results.sort(key=lambda item: item.total_score, reverse=True)
        for index, item in enumerate(results, start=1):
            item.rank = index
        if conclusion != "not_comparable" and results:
            results[0].recommended = True
        return results

    @staticmethod
    def _snapshot(candidate: CompareCandidate) -> CompareFundSnapshot:
        details = candidate.profile.details
        estimate = candidate.estimate
        return CompareFundSnapshot(
            code=candidate.code,
            name=candidate.name,
            fund_type=candidate.fund_type,
            official_nav=candidate.profile.last_nav,
            official_nav_date=candidate.profile.nav_date,
            one_month_pct=details.stage_returns.one_month_pct,
            three_month_pct=details.stage_returns.three_month_pct,
            six_month_pct=details.stage_returns.six_month_pct,
            one_year_pct=details.stage_returns.one_year_pct,
            stock_pct=details.asset_allocation.stock_pct,
            bond_pct=details.asset_allocation.bond_pct,
            cash_pct=details.asset_allocation.cash_pct,
            scale_billion=details.scale_billion or details.asset_allocation.net_asset_billion,
            current_rate_pct=details.trading.current_rate_pct,
            purchase_limit_yuan=candidate.purchase_limit_yuan,
            similar_rank=details.similar_rank.rank,
            similar_rank_total=details.similar_rank.total,
            similar_rank_percentile_pct=details.similar_rank.percentile_pct,
            manager_names=[manager.name for manager in details.managers],
            holdings_date=candidate.holdings.holdings_date if candidate.holdings else None,
            top10_weight_sum=candidate.holdings.top10_weight_sum if candidate.holdings else None,
            estimated_change_pct=estimate.estimated_change_pct if estimate else None,
        )

    @staticmethod
    def _fund_reasons(scores: dict[str, float], candidate: CompareCandidate) -> list[str]:
        reasons: list[str] = []
        if scores["performance"] >= 75:
            reasons.append("阶段收益表现靠前")
        if scores["ranking"] >= 80:
            reasons.append("同类排名较好")
        if scores["scale"] >= 80:
            reasons.append("基金规模处于较舒适区间")
        if scores["holdings"] >= 80:
            reasons.append("前十大持仓集中度较适中")
        if scores["allocation"] >= 80:
            reasons.append("资产配置与当前策略口径匹配")
        if scores["manager"] >= 80:
            reasons.append("基金经理任职或星级信息较有优势")
        if not candidate.holdings:
            reasons.append("缺少可解析前十大持仓，持仓相似度参考价值有限")
        return reasons[:5] or ["基础数据完整度一般，建议结合外部基金档案复核"]

    @staticmethod
    def _pair_reasons(
        holdings_similarity: float | None,
        profile_similarity: float,
        allocation_similarity: float | None,
        theme_similarity: float,
    ) -> list[str]:
        reasons: list[str] = []
        if holdings_similarity is None:
            reasons.append("缺少双方完整持仓，主要按产品画像判断")
        elif holdings_similarity >= 0.70:
            reasons.append("前十大持仓高度重合")
        elif holdings_similarity >= 0.40:
            reasons.append("前十大持仓有一定重合但权重不同")
        else:
            reasons.append("前十大持仓重合较低")
        if profile_similarity >= 0.70:
            reasons.append("基金类型接近")
        elif profile_similarity < 0.35:
            reasons.append("基金类型差异明显")
        if allocation_similarity is not None:
            if allocation_similarity >= 0.80:
                reasons.append("股票/债券/现金配置接近")
            elif allocation_similarity < 0.55:
                reasons.append("资产配置差异较大")
        if theme_similarity >= 0.70:
            reasons.append("名称或关注主题较接近")
        elif theme_similarity < 0.35:
            reasons.append("主题关键词差异较大")
        return reasons

    def _build_recommendation(
        self,
        conclusion: CompareConclusion,
        fund_results: list[CompareFundResult],
        pair_similarities: list[ComparePairSimilarity],
        strategy: CompareStrategy,
    ) -> tuple[str | None, str]:
        if not fund_results:
            return None, "没有可用于比较的基金数据。"
        strategy_label = STRATEGY_LABELS[strategy]
        similar_pairs = self._pair_labels(fund_results, pair_similarities, relation="very_similar", limit=3)
        unrelated_pairs = self._pair_labels(fund_results, pair_similarities, relation="not_comparable", limit=4)
        outlier_labels = self._outlier_labels(fund_results, pair_similarities)
        style_text = self._style_section(fund_results)
        if conclusion == "not_comparable":
            parts = ["整体判断：这组基金不能作为一个整体强行排名。"]
            if similar_pairs:
                parts.append(f"其中 {self._join_labels(similar_pairs)} 高度相似，可以单独放在一组里择优。")
            if outlier_labels:
                parts.append(f"明显离群的是 {self._join_labels(outlier_labels)}，它与多数候选的相似度偏低。")
            if unrelated_pairs:
                parts.append(f"低相关组合包括 {self._join_labels(unrelated_pairs)}。")
            overview = "".join(parts)
            advice = "选择建议：先剔除不相关基金，或按板块/资产类型拆成小组后再比较；当前分数只用于看各自基础条件，不给出谁更好的强结论。"
            return None, "\n\n".join([overview, style_text, advice])
        best = fund_results[0]
        runner_up = fund_results[1] if len(fund_results) > 1 else None
        reason_text = "、".join(best.reasons[:3])
        if conclusion == "very_similar":
            gap = best.total_score - (runner_up.total_score if runner_up else 0)
            overview = (
                f"整体判断：这些基金非常类似，主要是在同一类资产/主题下做细微差别选择。"
                f"按{strategy_label}口径，当前优先考虑 {best.name}（{best.code}），综合分领先 {gap:.2f} 分。"
                f"它的主要优势是{reason_text}。"
            )
            advice = (
                "选择建议：如果只想保留一只，优先看综合分更高者；如果两只分别是 A/C 或同指数不同份额，"
                "再结合费率、限购金额和你实际买入渠道确认，费率与限购只作为交易便利性参考，不参与评分。"
            )
            return best.code, "\n\n".join([overview, style_text, advice])
        relation_hint = ""
        if similar_pairs:
            relation_hint += f"其中 {self._join_labels(similar_pairs)} 高度相似；"
        if unrelated_pairs:
            relation_hint += f"但 {self._join_labels(unrelated_pairs)} 相关性偏低；"
        overview = (
            f"整体判断：这些基金属于可比较但风格不同的候选，{relation_hint}"
            f"按{strategy_label}口径当前排序第一的是 {best.name}（{best.code}），主要优势是{reason_text}。"
        )
        advice = (
            "选择建议：如果你的目标是更高弹性，优先看股票仓位高、阶段收益和同类排名更强的基金；"
            "如果更在意持有体验，优先看规模适中、前十大集中度不过高、风格更清晰且限购不影响买入的基金。"
            "最终要和你的板块暴露目标对齐，而不是只看总分。"
        )
        return best.code, "\n\n".join([overview, style_text, advice])

    def _style_section(self, fund_results: list[CompareFundResult]) -> str:
        lines = [f"- {self._fund_style_summary(item)}" for item in fund_results]
        return "逐只风格：\n" + "\n".join(lines)

    def _fund_style_summary(self, item: CompareFundResult) -> str:
        snapshot = item.snapshot
        theme_text = self._theme_summary(item)
        allocation_text = self._allocation_style(snapshot.stock_pct, snapshot.bond_pct)
        holdings_text = self._holdings_style(snapshot.top10_weight_sum)
        scale_text = self._scale_style(snapshot.scale_billion)
        performance_text = self._performance_style(snapshot.one_year_pct, snapshot.similar_rank, snapshot.similar_rank_total)
        manager_text = self._manager_style(snapshot.manager_names)
        trade_text = self._trade_style(snapshot.current_rate_pct, snapshot.purchase_limit_yuan)
        role_text = self._role_hint(item)
        return (
            f"{item.name}（{item.code}）：{item.fund_type or '类型未披露'}，{theme_text}，{allocation_text}，"
            f"{holdings_text}，{scale_text}，{performance_text}，{manager_text}，{trade_text}。{role_text}"
        )

    def _theme_summary(self, item: CompareFundResult) -> str:
        text = f"{item.name} {item.fund_type or ''}".lower()
        themes = sorted(self._theme_tokens(text))
        if themes:
            return f"主题上偏{'、'.join(themes[:2])}"
        lowered = item.name.lower()
        if "指数" in lowered or "etf" in lowered:
            return "定位更偏指数工具"
        if "成长" in item.name:
            return "风格上偏成长"
        if "价值" in item.name:
            return "风格上偏价值"
        if "红利" in item.name:
            return "风格上偏红利/股息"
        return "主题特征需要结合持仓进一步确认"

    @staticmethod
    def _allocation_style(stock_pct: float | None, bond_pct: float | None) -> str:
        if stock_pct is None:
            return "仓位披露不足"
        stock = float(stock_pct)
        bond = float(bond_pct or 0)
        if bond >= 65:
            return f"债券仓位约{bond:.1f}%，偏防守"
        if stock >= 92:
            return f"股票仓位约{stock:.1f}%，弹性和波动都偏高"
        if stock >= 75:
            return f"股票仓位约{stock:.1f}%，偏进攻型"
        if stock >= 45:
            return f"股票仓位约{stock:.1f}%，偏均衡配置"
        return f"股票仓位约{stock:.1f}%，权益暴露较低"

    @staticmethod
    def _holdings_style(top10_weight_sum: float | None) -> str:
        if top10_weight_sum is None:
            return "前十大集中度缺失"
        weight = float(top10_weight_sum)
        if weight >= 78:
            return f"前十大占比约{weight:.1f}%，持仓较集中"
        if weight >= 45:
            return f"前十大占比约{weight:.1f}%，集中度适中"
        return f"前十大占比约{weight:.1f}%，相对分散"

    @staticmethod
    def _scale_style(scale_billion: float | None) -> str:
        if scale_billion is None:
            return "规模缺失"
        scale = float(scale_billion)
        if scale < 2:
            return f"规模约{scale:.2f}亿，偏小"
        if scale <= 80:
            return f"规模约{scale:.2f}亿，处于较舒适区间"
        if scale <= 150:
            return f"规模约{scale:.2f}亿，偏大但仍可观察"
        return f"规模约{scale:.2f}亿，较大"

    @staticmethod
    def _performance_style(one_year_pct: float | None, rank: int | None, total: int | None) -> str:
        parts: list[str] = []
        if one_year_pct is not None:
            value = float(one_year_pct)
            if value >= 30:
                parts.append(f"近1年{value:+.2f}%，收益弹性强")
            elif value >= 0:
                parts.append(f"近1年{value:+.2f}%，表现为正")
            else:
                parts.append(f"近1年{value:+.2f}%，阶段承压")
        if rank is not None and total:
            parts.append(f"同类排名{rank}/{total}")
        return "，".join(parts) if parts else "阶段收益/同类排名缺失"

    @staticmethod
    def _manager_style(manager_names: list[str]) -> str:
        if not manager_names:
            return "基金经理信息缺失"
        if len(manager_names) == 1:
            return f"基金经理为{manager_names[0]}"
        return f"基金经理为{'、'.join(manager_names[:2])}等"

    @staticmethod
    def _trade_style(current_rate_pct: float | None, purchase_limit_yuan: float | None) -> str:
        rate = "--" if current_rate_pct is None else f"{float(current_rate_pct):.2f}%"
        limit = "--" if purchase_limit_yuan is None else _format_yuan(purchase_limit_yuan)
        return f"费率{rate}、限购金额{limit}"

    @staticmethod
    def _role_hint(item: CompareFundResult) -> str:
        text = f"{item.name} {item.fund_type or ''}".lower()
        stock = item.snapshot.stock_pct
        top10 = item.snapshot.top10_weight_sum
        if "债" in text:
            return "更适合作为低权益波动的防守/固收候选。"
        if "qdii" in text or "海外" in text or "全球" in text:
            return "更像海外资产或跨市场敞口工具，需要单独看汇率和海外市场风险。"
        if "指数" in text or "etf" in text:
            return "更像工具型配置，重点看跟踪标的、费率、规模和限购便利性。"
        if stock is not None and stock >= 85 and top10 is not None and top10 >= 65:
            return "更像主动进攻型候选，适合追求主题弹性但要接受集中波动。"
        if stock is not None and stock >= 75:
            return "更像偏股主动型候选，适合确认主题方向后择优配置。"
        if stock is not None and stock < 45:
            return "更像低权益暴露候选，不宜和高股仓主题基金直接比收益。"
        return "更适合作为同主题候选中的风格补充项来观察。"

    @staticmethod
    def _pair_labels(
        fund_results: list[CompareFundResult],
        pair_similarities: list[ComparePairSimilarity],
        *,
        relation: CompareConclusion,
        limit: int,
    ) -> list[str]:
        name_map = {item.code: item.name for item in fund_results}
        filtered = [pair for pair in pair_similarities if pair.relation == relation]
        reverse = relation != "not_comparable"
        filtered.sort(key=lambda pair: pair.overall_similarity, reverse=reverse)
        return [
            f"{name_map.get(pair.code_a, pair.code_a)}（{pair.code_a}）和{name_map.get(pair.code_b, pair.code_b)}（{pair.code_b}）"
            for pair in filtered[:limit]
        ]

    @staticmethod
    def _outlier_labels(
        fund_results: list[CompareFundResult],
        pair_similarities: list[ComparePairSimilarity],
    ) -> list[str]:
        if len(fund_results) <= 2:
            return []
        name_map = {item.code: item.name for item in fund_results}
        stats: dict[str, dict[str, float]] = {
            item.code: {"total": 0, "count": 0, "unrelated": 0} for item in fund_results
        }
        for pair in pair_similarities:
            for code in (pair.code_a, pair.code_b):
                stats[code]["total"] += pair.overall_similarity
                stats[code]["count"] += 1
                if pair.relation == "not_comparable":
                    stats[code]["unrelated"] += 1
        labels: list[str] = []
        for code, item in stats.items():
            count = item["count"] or 1
            avg_similarity = item["total"] / count
            unrelated_ratio = item["unrelated"] / count
            if unrelated_ratio >= 0.67 or avg_similarity < 38:
                labels.append(f"{name_map.get(code, code)}（{code}）")
        return labels

    @staticmethod
    def _join_labels(labels: list[str]) -> str:
        if len(labels) <= 2:
            return "、".join(labels)
        return "、".join(labels[:-1]) + f"，以及{labels[-1]}"

    @staticmethod
    def _score_factors(strategy: CompareStrategy) -> list[CompareScoreFactor]:
        weights = STRATEGY_WEIGHTS[strategy]
        return [
            CompareScoreFactor(
                key=key,
                label=SCORE_FACTOR_LABELS[key],
                weight_pct=round(weight * 100, 2),
                basis=SCORE_FACTOR_BASIS[key],
            )
            for key, weight in weights.items()
        ]

    @staticmethod
    def _conclusion_title(conclusion: CompareConclusion) -> str:
        if conclusion == "very_similar":
            return "高度相似，可直接择优"
        if conclusion == "same_theme_different":
            return "同类可比，但风格不同"
        return "相关性不足，不建议强行比较"

    @staticmethod
    def _global_warnings(
        candidates: list[CompareCandidate],
        pair_similarities: list[ComparePairSimilarity],
        conclusion: CompareConclusion,
    ) -> list[str]:
        warnings: list[str] = []
        for candidate in candidates:
            for warning in candidate.warnings:
                warnings.append(f"{candidate.code}：{warning}")
        if any(pair.holdings_similarity is None for pair in pair_similarities):
            warnings.append("部分基金缺少前十大持仓，持仓相似度会降级为产品画像判断。")
        if conclusion == "not_comparable":
            warnings.append("不可比结论下不会给出强推荐，分数仅用于查看各自基础条件。")
        warnings.append("比较结果基于公开披露和本工具模型估算，仅供研究参考，不构成投资建议。")
        return warnings

    @staticmethod
    def _round_pct(value: float | None) -> float | None:
        return round(value * 100, 2) if value is not None else None


def _relative_scores(values: dict[str, float | None], *, higher_better: bool, default: float) -> dict[str, float]:
    usable = [float(value) for value in values.values() if value is not None]
    if not usable:
        return {code: default for code in values}
    low = min(usable)
    high = max(usable)
    if high == low:
        return {code: 70 if value is not None else default for code, value in values.items()}
    scores: dict[str, float] = {}
    for code, value in values.items():
        if value is None:
            scores[code] = default
            continue
        ratio = (float(value) - low) / (high - low)
        if not higher_better:
            ratio = 1 - ratio
        scores[code] = 30 + 70 * ratio
    return scores


def _parse_manager_tenure_years(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip()
    years = 0.0
    year_match = re.search(r"(\d+(?:\.\d+)?)\s*年", text)
    day_match = re.search(r"(\d+(?:\.\d+)?)\s*天", text)
    month_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:月|个月)", text)
    if year_match:
        years += float(year_match.group(1))
    if month_match:
        years += float(month_match.group(1)) / 12
    if day_match:
        years += float(day_match.group(1)) / 365
    if years:
        return years
    number_match = re.search(r"\d+(?:\.\d+)?", text)
    return float(number_match.group(0)) if number_match else None


def _format_yuan(value: float | None) -> str:
    if value is None:
        return "--"
    amount = float(value)
    if abs(amount) >= 100_000_000:
        return f"{amount / 100_000_000:.2f}亿"
    if abs(amount) >= 10_000:
        return f"{amount / 10_000:.0f}万"
    return f"{amount:.0f}元"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
