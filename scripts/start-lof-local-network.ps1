$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Python = $env:LOF_PYTHON
if (-not $Python) {
    $Python = "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
}

$env:FUND_ESTIMATOR_FORCE_MOCK = "0"
$env:FUND_ESTIMATOR_ALLOW_MOCK_FALLBACK = "0"
$env:FUND_ESTIMATOR_DB = Join-Path $Root "data\lof-real-test.sqlite3"
$env:FUND_ESTIMATOR_BACKGROUND_SCAN = "1"
$env:FUND_ESTIMATOR_SCAN_INTERVAL_SECONDS = "60"
$env:LOF_NOTICE_ENABLED = "1"
$env:LOF_NOTICE_DIR = Join-Path $Root "data"
$env:LOF_NOTICE_DAILY_SUMMARY_TIME = "10:00"
$env:LOF_NOTICE_SEND_EMPTY_DAILY_SUMMARY = "1"

Set-Location $Root
& $Python -m uvicorn fund_estimator.api.app:app --host 127.0.0.1 --port 8011
