# Demo Validation Campaign v1 — Runbook

## Prerequisites

### 1. Binance Futures Testnet Account

1. Go to https://testnet.binancefuture.com
2. Register/login with a Binance account
3. Navigate to API Management
4. Create a new API key pair
5. Note both `API Key` and `Secret Key`

### 2. Environment Setup

```bash
# Clone and install
git clone <repo-url> && cd trade-bot
pip install -e ".[dev]"

# Create .env from template
cp .env.example .env
```

Edit `.env`:
```bash
CTE_ENGINE_MODE=demo
CTE_EXECUTION_MODE=testnet
CTE_DASHBOARD_VENUE_LOOP=1
CTE_BINANCE_TESTNET_API_KEY=your_testnet_api_key_here
CTE_BINANCE_TESTNET_API_SECRET=your_testnet_api_secret_here
CTE_BINANCE_TESTNET_REST_URL=https://testnet.binancefuture.com
```

For a **single-symbol, minimal-notional** dashboard smoke test (`demo_exchange` path), see [TESTNET_SMOKE_TEST_REPORT.md](TESTNET_SMOKE_TEST_REPORT.md). Use `CTE_ENGINE_SYMBOLS='["SOLUSDT"]'` (JSON array) and optional `CTE_SIGNALS_COOLDOWN_SECONDS=0` while debugging venue errors so cooldown does not mask REST failures.

### 3. Pre-Start Safety Verification

```bash
# Run tests
pytest tests/ -v

# Verify safety guards (this should FAIL if keys are missing)
CTE_ENGINE_MODE=demo python -c "
from cte.ops.safety import validate_environment
results = validate_environment(
    'demo',
    binance_rest_url='https://testnet.binancefuture.com',
    binance_api_key='YOUR_KEY',
    binance_api_secret='YOUR_SECRET',
)
for r in results:
    print(f'[{\"PASS\" if r.passed else \"FAIL\"}] {r.check}: {r.detail}')
"

# This should ABORT (safety guard blocks production URL in demo mode)
CTE_ENGINE_MODE=demo CTE_BINANCE_TESTNET_REST_URL=https://fapi.binance.com \
    python -c "from cte.ops.safety import enforce_safety; enforce_safety('demo', binance_rest_url='https://fapi.binance.com')"
# Expected: "SAFETY BLOCK" and exit code 1
```

---

## Startup

### Paper Mode (Live Data, Simulated Fills)

```bash
CTE_ENGINE_MODE=paper uvicorn cte.dashboard.app:app --host 0.0.0.0 --port 8080
```

Expected startup output:
```
========================================
  CTE - PAPER TRADING MODE
  Live market data + simulated fills.
  NO real orders. NO real capital.
========================================
```

### Demo Mode (Live Data, Testnet Orders)

```bash
CTE_ENGINE_MODE=demo \
CTE_BINANCE_TESTNET_API_KEY=xxx \
CTE_BINANCE_TESTNET_API_SECRET=xxx \
uvicorn cte.dashboard.app:app --host 0.0.0.0 --port 8080
```

Expected startup output:
```
=============================================
  CTE - DEMO / TESTNET MODE
  Live market data + TESTNET orders.
  DEMO WALLET ONLY. NO REAL CAPITAL.
  Exchange: Binance Futures Testnet
=============================================
```

---

## Verification Checklist

### Step 1: Live Market Data

Open `http://localhost:8080` and go to **Market Feeds** page.

| Check | Expected | API Endpoint |
|---|---|---|
| BTC last price | Non-zero, updating | `GET /api/market/tickers` |
| ETH last price | Non-zero, updating | `GET /api/market/tickers` |
| Spread (BTC) | < 5 bps | `GET /api/market/tickers` |
| Data age | < 2000ms | `GET /api/market/tickers` |
| Feed connected | true | `GET /api/market/health` |
| Latency | < 500ms | `GET /api/market/health` |
| Stale indicator | "LIVE" (green) | Dashboard |

```bash
# CLI verification
curl -s http://localhost:8080/api/market/tickers | python -m json.tool
curl -s http://localhost:8080/api/market/health | python -m json.tool
```

### Step 2: System Mode Verification

| Check | Expected |
|---|---|
| Dashboard header | Shows "PAPER MODE" or "DEMO MODE" badge |
| Feed indicator | Shows "FEED LIVE" (green) |
| `/api/ops/status` | `system_mode` = "paper" or "demo" |
| `/api/config` | `system_mode` matches |

### Step 3: Demo Balance Sync (Demo Mode Only)

```bash
# Fetch testnet account balance
curl -s http://localhost:8080/api/demo/balance
# Expected: USDT balance from testnet account
```

### Step 4: Test Order Placement (Demo Mode Only)

```bash
# Place a small testnet market buy
curl -X POST http://localhost:8080/api/demo/test_order \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BTCUSDT", "side": "buy", "quantity": "0.001"}'

# Check order status
curl -s http://localhost:8080/api/demo/orders
```

### Step 5: Campaign Snapshot

```bash
# Take a metric snapshot
curl -X POST http://localhost:8080/api/campaign/snapshot?period=hourly
curl -s http://localhost:8080/api/campaign/summary | python -m json.tool
```

---

## Daily Monitoring (7-Day Campaign)

### Every Hour

```bash
# Take hourly snapshot
curl -X POST http://localhost:8080/api/campaign/snapshot?period=hourly
```

### Every Day (at end of day)

```bash
# Take daily snapshot
curl -X POST http://localhost:8080/api/campaign/snapshot?period=daily

# Check campaign status
curl -s http://localhost:8080/api/campaign/summary | python -m json.tool

# Check readiness gates
curl -s http://localhost:8080/api/readiness/campaign | python -m json.tool
```

### What To Watch

| Metric | Threshold | Action if Breached |
|---|---|---|
| Feed connected | always true | Restart if disconnected >5min |
| Reconciliation | 0 mismatches | Investigate immediately |
| Max drawdown | < 5% | Review strategy, consider pause |
| Reject rate | < 5% | Check order parameters |
| Stale data events | < 10/day | Check network stability |
| Latency p95 | < 5000ms | Check exchange load |

---

## Emergency Procedures

### Emergency Stop

```bash
# Via dashboard: Operations → EMERGENCY STOP button

# Via API
curl -X POST "http://localhost:8080/api/ops/emergency_stop?reason=Manual+campaign+stop"

# Verify
curl -s http://localhost:8080/api/ops/status | python -c "
import sys, json; d=json.load(sys.stdin)
print(f'Mode: {d[\"mode\"]}')  # Should be 'halted'
"
```

### Resume After Emergency

```bash
curl -X POST http://localhost:8080/api/ops/resume
```

---

## Pass/Fail Criteria (After 7 Days)

### PASS (all must be true)

- [ ] 7 daily snapshots collected
- [ ] >= 100 trades (paper or demo)
- [ ] 0 seed trades in campaign data
- [ ] 100% reconciliation clean
- [ ] Max drawdown < 5%
- [ ] Latency p95 < 5000ms
- [ ] Stale data ratio < 1%
- [ ] Reject ratio < 5%
- [ ] 0 critical errors
- [ ] Positive expectancy (> $0/trade)

### Automated Check

```bash
curl -s http://localhost:8080/api/readiness/campaign | python -c "
import sys, json
d = json.load(sys.stdin)
print(f'Ready: {d[\"ready\"]}')
print(f'Passed: {d[\"passed\"]}/{d[\"total\"]}')
for b in d.get('blockers', []):
    print(f'  BLOCKER: {b[\"name\"]}: {b[\"description\"]} (value: {b[\"value\"]})')
"
```

### Final GO/NO-GO

```bash
curl -s http://localhost:8080/api/report/go_no_go | python -m json.tool
```

---

## Common Issues

| Issue | Cause | Fix |
|---|---|---|
| "SAFETY BLOCK" on startup | Production URL in demo mode | Fix `CTE_BINANCE_TESTNET_REST_URL` |
| "API keys required" | Missing env vars | Set `CTE_BINANCE_TESTNET_API_KEY` |
| Feed disconnects frequently | Network instability | Check VPN/firewall, reconnect is automatic |
| All trades show source=seed | Running in seed mode | Switch to `CTE_ENGINE_MODE=paper` or `demo` |
| Stale data warnings | Exchange maintenance | Wait and verify auto-reconnect |
