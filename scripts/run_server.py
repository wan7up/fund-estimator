from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FUND_ESTIMATOR_FORCE_MOCK", "0")
os.environ.setdefault("FUND_ESTIMATOR_ALLOW_MOCK_FALLBACK", "0")
host = os.environ.get("FUND_ESTIMATOR_HOST", "127.0.0.1")
port = int(os.environ.get("PORT", "8000"))

log_file = (DATA_DIR / "uvicorn.log").open("a", encoding="utf-8", buffering=1)
sys.stdout = log_file
sys.stderr = log_file

uvicorn.run(
    "fund_estimator.api.app:app",
    host=host,
    port=port,
    reload=False,
    log_level="info",
)
