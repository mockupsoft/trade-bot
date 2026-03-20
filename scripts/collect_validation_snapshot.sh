#!/usr/bin/env bash
# Collect a point-in-time validation snapshot from a running CTE dashboard (demo mode).
# Usage: BASE_URL=http://127.0.0.1:8080 ./scripts/collect_validation_snapshot.sh [outdir]
set -euo pipefail
BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
OUT="${1:-./validation_snapshots}"
mkdir -p "$OUT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DIR="$OUT/run_$STAMP"
mkdir -p "$DIR"

curl -sS "$BASE_URL/api/dashboard/meta" | tee "$DIR/meta.json" >/dev/null
curl -sS "$BASE_URL/api/market/health" | tee "$DIR/market_health.json" >/dev/null
curl -sS "$BASE_URL/api/market/tickers" | tee "$DIR/market_tickers.json" >/dev/null
curl -sS "$BASE_URL/api/paper/status" | tee "$DIR/paper_status.json" >/dev/null
curl -sS "$BASE_URL/api/paper/warmup" | tee "$DIR/paper_warmup.json" >/dev/null
curl -sS "$BASE_URL/api/paper/entry-diagnostics" | tee "$DIR/paper_entry_diagnostics.json" >/dev/null
curl -sS "$BASE_URL/api/paper/positions" | tee "$DIR/paper_positions.json" >/dev/null
curl -sS "$BASE_URL/api/analytics/summary?epoch=crypto_v1_demo" | tee "$DIR/analytics_summary.json" >/dev/null
curl -sS "$BASE_URL/api/analytics/trades?epoch=crypto_v1_demo&limit=200" | tee "$DIR/analytics_trades.json" >/dev/null
curl -sS -X POST "$BASE_URL/api/campaign/snapshot?period=hourly" | tee "$DIR/campaign_snapshot_hourly.json" >/dev/null
curl -sS "$BASE_URL/api/reconciliation/status" | tee "$DIR/reconciliation_status.json" >/dev/null
curl -sS "$BASE_URL/api/ops/status" | tee "$DIR/ops_status.json" >/dev/null
curl -sS "$BASE_URL/api/readiness/campaign" | tee "$DIR/readiness_campaign.json" >/dev/null

echo "Wrote $DIR"
