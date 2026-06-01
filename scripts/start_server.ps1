$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location -LiteralPath $root

$env:FUND_ESTIMATOR_FORCE_MOCK = "0"
$env:FUND_ESTIMATOR_ALLOW_MOCK_FALLBACK = "0"

& "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "$PSScriptRoot\run_server.py"
