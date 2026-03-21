#!/usr/bin/env bash
# Start CTE dashboard on port 8080 (background). Logs: /tmp/cte_dashboard.log
# Usage: from repo root — ./scripts/start_dashboard.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if command -v curl >/dev/null 2>&1; then
  if curl -sf "http://127.0.0.1:8080/api/dashboard/meta" >/dev/null 2>&1; then
    echo "Dashboard already responding on http://127.0.0.1:8080"
    exit 0
  fi
fi
nohup python3 -m cte.dashboard >> /tmp/cte_dashboard.log 2>&1 &
echo "Started PID $! — open http://127.0.0.1:8080/ (log: /tmp/cte_dashboard.log)"
sleep 2
curl -sf "http://127.0.0.1:8080/api/dashboard/meta" && echo "" || echo "Wait a few seconds and refresh; check log if needed."
