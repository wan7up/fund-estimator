#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export FUND_ESTIMATOR_FORCE_MOCK="${FUND_ESTIMATOR_FORCE_MOCK:-0}"
export FUND_ESTIMATOR_ALLOW_MOCK_FALLBACK="${FUND_ESTIMATOR_ALLOW_MOCK_FALLBACK:-0}"
export FUND_ESTIMATOR_DB="${FUND_ESTIMATOR_DB:-data/fund_estimator.sqlite3}"

exec python -m uvicorn fund_estimator.api.app:app --host 0.0.0.0 --port "${PORT:-8000}"
