#Requires -Version 5.1
<#
.SYNOPSIS
  Operator pre-flight for testnet validation (plan: clean account + API auth).

.DESCRIPTION
  1) Prints manual steps: close all Binance USD-M Futures testnet positions/orders;
     ensure no other client uses the same API keys.
  2) Runs scripts/verify_binance_testnet_auth.py (requires .env with testnet keys).

  Does not start the dashboard — use scripts/testnet_validation_dashboard.py for that.
#>
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host "=== Manual pre-flight ===" -ForegroundColor Cyan
Write-Host "1. Binance USD-M Futures testnet UI: close ALL open positions and cancel open orders."
Write-Host "2. Do not run any other bot or manual trader using the SAME API keys."
Write-Host ""
Write-Host "=== REST auth check ===" -ForegroundColor Cyan
py -3 scripts/verify_binance_testnet_auth.py
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
Write-Host ""
Write-Host "OK. Next: py -3 scripts/testnet_validation_dashboard.py" -ForegroundColor Green
Write-Host "  (optional) py -3 scripts/poll_paper_snapshots.py --output-dir evidence/snapshots"
exit 0
