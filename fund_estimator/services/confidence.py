from __future__ import annotations

from datetime import date

from fund_estimator.models.schema import ConfidenceLevel


UNSUITABLE_KEYWORDS = ("债券", "货币", "QDII", "FOF", "商品", "REIT")
SUITABLE_KEYWORDS = ("股票", "混合", "指数", "ETF", "联接")


def is_potentially_suitable(fund_type: str | None) -> bool:
    if not fund_type:
        return True
    upper_type = fund_type.upper()
    if any(keyword.upper() in upper_type for keyword in UNSUITABLE_KEYWORDS):
        return False
    return True


def assess_confidence(
    *,
    fund_type: str | None,
    holdings_date: date,
    top10_weight_sum: float,
    usable_weight_sum: float,
    missing_quote_count: int,
    unmapped_count: int,
    today: date | None = None,
) -> tuple[ConfidenceLevel, list[str]]:
    today = today or date.today()
    age_days = max((today - holdings_date).days, 0)
    notes = ["基于最近一期前十大持仓估算", "非官方净值，仅供研究和参考，不构成投资建议"]

    suitable = is_potentially_suitable(fund_type)
    if not suitable:
        notes.append(f"基金类型为 {fund_type}，不完全适合用A股持仓实时估值")
    elif fund_type and any(keyword.upper() in fund_type.upper() for keyword in SUITABLE_KEYWORDS):
        notes.append("基金类型与持仓穿透估值方法基本匹配")

    if age_days > 365:
        notes.append(f"持仓披露距今约 {age_days} 天，时效性较弱")
    elif age_days > 180:
        notes.append(f"持仓披露距今约 {age_days} 天，可能已有较大调仓")
    else:
        notes.append("持仓披露时间相对较近")

    if top10_weight_sum >= 60:
        notes.append("前十大持仓覆盖率较高")
    elif top10_weight_sum >= 40:
        notes.append("前十大持仓覆盖率一般")
    else:
        notes.append("前十大持仓覆盖率偏低")

    if missing_quote_count or unmapped_count:
        notes.append("部分持仓无法纳入实时估值，置信度已下调")

    if (
        suitable
        and age_days <= 180
        and top10_weight_sum >= 60
        and usable_weight_sum >= 55
        and missing_quote_count == 0
        and unmapped_count == 0
    ):
        return ConfidenceLevel.HIGH, notes

    if suitable and age_days <= 365 and top10_weight_sum >= 40 and usable_weight_sum >= 30:
        return ConfidenceLevel.MEDIUM, notes

    return ConfidenceLevel.LOW, notes
