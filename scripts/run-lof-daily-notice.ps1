$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Python = $env:LOF_PYTHON
if (-not $Python) {
    $Python = "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
}

$LocalDeps = Join-Path $Root ".local_python"
if (Test-Path $LocalDeps) {
    $env:PYTHONPATH = $LocalDeps
}

$env:FUND_ESTIMATOR_FORCE_MOCK = "0"
$env:FUND_ESTIMATOR_ALLOW_MOCK_FALLBACK = "0"
$env:FUND_ESTIMATOR_DB = Join-Path $Root "data\lof-real-test.sqlite3"
$env:LOF_NOTICE_ENABLED = "1"
$env:LOF_NOTICE_DIR = Join-Path $Root "data"
$env:LOF_NOTICE_DAILY_SUMMARY_TIME = "10:00"
$env:LOF_NOTICE_SEND_EMPTY_DAILY_SUMMARY = "1"

$LogDir = Join-Path $Root "data"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogPath = Join-Path $LogDir "lof-daily-notice.log"

Set-Location $Root
& $Python -m fund_estimator.lof_worker daily-summary 2>&1 | Tee-Object -FilePath $LogPath -Append
