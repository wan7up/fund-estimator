from __future__ import annotations

from datetime import date, timedelta

from fund_estimator.models.schema import ConfidenceLevel
from fund_estimator.services.confidence import assess_confidence, is_potentially_suitable


def test_confidence_high_for_recent_high_coverage_stock_fund():
    level, notes = assess_confidence(
        fund_type="偏股混合型",
        holdings_date=date.today() - timedelta(days=60),
        top10_weight_sum=72.0,
        usable_weight_sum=72.0,
        missing_quote_count=0,
        unmapped_count=0,
    )

    assert level == ConfidenceLevel.HIGH
    assert any("覆盖率较高" in note for note in notes)


def test_confidence_medium_for_average_coverage():
    level, _ = assess_confidence(
        fund_type="混合型",
        holdings_date=date.today() - timedelta(days=220),
        top10_weight_sum=45.0,
        usable_weight_sum=45.0,
        missing_quote_count=0,
        unmapped_count=0,
    )

    assert level == ConfidenceLevel.MEDIUM


def test_confidence_low_for_unsuitable_fund_type():
    level, notes = assess_confidence(
        fund_type="债券型",
        holdings_date=date.today() - timedelta(days=20),
        top10_weight_sum=80.0,
        usable_weight_sum=80.0,
        missing_quote_count=0,
        unmapped_count=0,
    )

    assert level == ConfidenceLevel.LOW
    assert not is_potentially_suitable("QDII")
    assert any("不完全适合" in note for note in notes)
