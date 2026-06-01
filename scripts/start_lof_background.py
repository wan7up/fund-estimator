from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = os.environ.get("LOF_PYTHON") or sys.executable
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

env = dict(os.environ)
local_deps = ROOT / ".local_python"
if local_deps.exists():
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(local_deps) if not existing_pythonpath else f"{local_deps}{os.pathsep}{existing_pythonpath}"
env["FUND_ESTIMATOR_FORCE_MOCK"] = "0"
env["FUND_ESTIMATOR_ALLOW_MOCK_FALLBACK"] = "0"
env["FUND_ESTIMATOR_DB"] = str(DATA_DIR / "lof-real-test.sqlite3")
env["FUND_ESTIMATOR_BACKGROUND_SCAN"] = "1"
env["FUND_ESTIMATOR_SCAN_INTERVAL_SECONDS"] = "60"
env["LOF_NOTICE_ENABLED"] = "1"
env["LOF_NOTICE_DIR"] = str(DATA_DIR)
env["LOF_NOTICE_DAILY_SUMMARY_TIME"] = "10:00"
env["LOF_NOTICE_SEND_EMPTY_DAILY_SUMMARY"] = "1"

stdout = open(DATA_DIR / "lof-local.out.log", "ab")
stderr = open(DATA_DIR / "lof-local.err.log", "ab")

creationflags = 0
if os.name == "nt":
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW

process = subprocess.Popen(
    [
        PYTHON,
        "-m",
        "uvicorn",
        "fund_estimator.api.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8011",
    ],
    cwd=ROOT,
    env=env,
    stdout=stdout,
    stderr=stderr,
    creationflags=creationflags,
    close_fds=True,
)

print(process.pid)
time.sleep(1.5)
returncode = process.poll()
if returncode is not None:
    print(f"exited:{returncode}")
