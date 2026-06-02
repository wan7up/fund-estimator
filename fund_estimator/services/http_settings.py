from __future__ import annotations

import os


def http_trust_env() -> bool:
    value = os.getenv("FUND_ESTIMATOR_HTTP_TRUST_ENV")
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}
