# Demo/Testnet Execution Engine — Design Document

## Table of Contents

1. [Adapter Architecture](#1-adapter-architecture)
2. [Order Lifecycle State Machine](#2-order-lifecycle-state-machine)
3. [Error/Reject Taxonomy](#3-errorreject-taxonomy)
4. [Demo Position Reconciliation Logic](#4-demo-position-reconciliation-logic)
5. [Acceptance Tests for 50 Demo Trades](#5-acceptance-tests-for-50-demo-trades)
6. [Risk Controls Before Phase 3 Live](#6-risk-controls-before-phase-3-live)

---

## 1. Adapter Architecture

### Common Interface

```python
class ExecutionAdapter(ABC):
    venue_name: str

    async def place_order(request: OrderRequest) -> OrderResult
    async def cancel_order(symbol, client_order_id) -> OrderResult
    async def get_order(symbol, client_order_id) -> OrderResult | None
    async def get_open_orders(symbol?) -> list[OrderResult]
    async def get_positions(symbol?) -> list[VenuePosition]
    async def close_position(symbol, quantity, side) -> OrderResult
    async def health() -> AdapterHealth
    async def start() -> None
    async def stop() -> None
```

Every execution backend implements this interface. The `ExecutionEngine` coordinator
dispatches to the active backend without knowing which venue it's talking to.

### Implementations

```
ExecutionAdapter (ABC)
├── PaperExecutionEngine  ── simulated fills, no network I/O
├── BinanceTestnetAdapter ── HMAC-SHA256 REST, /fapi/v1
└── BybitDemoAdapter      ── HMAC-SHA256 REST, /v5 unified
```

### Shared Data Types

| Type | Purpose |
|---|---|
| `OrderRequest` | Input for place_order (symbol, side, qty, price, reduce_only, idempotency_key) |
| `OrderResult` | Output from any order operation (status, fills, errors, raw_response) |
| `VenuePosition` | Position as reported by venue (for reconciliation) |
| `VenueOrderStatus` | Canonical status across all venues (11 states) |
| `AdapterHealth` | Connectivity, rate limits, error counts |

### Request Signing

**Binance**: `HMAC-SHA256(query_string, api_secret)` appended as `&signature=` parameter.
Timestamp and recvWindow included in every request.

**Bybit**: `HMAC-SHA256(timestamp + api_key + recv_window + body, api_secret)` sent in
`X-BAPI-SIGN` header. Timestamp in `X-BAPI-TIMESTAMP` header.

### Rate Limiting

Token bucket rate limiter with exponential backoff:

| Venue | Max Tokens | Window | Backoff Base | Backoff Max |
|---|---|---|---|---|
| Binance | 2400 weight | 60s | 0.5s | 30s |
| Bybit | 120 requests | 60s | 1.0s | 30s |

On HTTP 429: bucket is drained, consecutive wait counter increased by 2.
Tokens refill continuously. Callers await token availability before sending.

---

## 2. Order Lifecycle State Machine

### State Diagram

```
PENDING ──→ SUBMITTING ──→ SUBMITTED ──→ PARTIAL ──→ FILLED
                │               │            │
                ▼               ▼            ▼
           SUBMIT_FAILED   CANCELLING    FILLED
                │           │    │
                ▼           ▼    ▼
           SUBMITTING   CANCELLED  CANCEL_FAILED
           (retry)                      │
                                        ▼
                                   CANCELLING (retry)

SUBMITTED ──→ REJECTED (venue late-reject)
SUBMITTED ──→ EXPIRED  (FOK/IOC unfilled, TTL)
```

### State Definitions

| State | Meaning | Terminal? |
|---|---|---|
| PENDING | Created locally, not yet sent | No |
| SUBMITTING | HTTP request in flight | No |
| SUBMITTED | Venue accepted (Binance: NEW, Bybit: New) | No |
| PARTIAL | Some quantity filled, rest pending | No |
| FILLED | Fully filled | **Yes** |
| CANCELLING | Cancel request in flight | No |
| CANCELLED | Confirmed cancelled | **Yes** |
| REJECTED | Venue rejected (bad qty, insufficient margin) | **Yes** |
| EXPIRED | TTL expired or FOK/IOC unfilled | **Yes** |
| SUBMIT_FAILED | Network error on submit (retryable) | No |
| CANCEL_FAILED | Network error on cancel (retryable) | No |

### Transition Rules

Every transition is validated against `VALID_TRANSITIONS`. Invalid transitions
are logged with `"INVALID"` in the audit trail but not applied (defensive programming).

All transitions record: `from_state`, `to_state`, `timestamp`, `reason`, `venue_data`.

### Idempotency

Every `OrderRequest` carries a `client_order_id` (UUID by default) that acts as
the idempotency key. If a submit fails and we retry, we use the same `client_order_id`.
The venue deduplicates based on this key (both Binance and Bybit support `newClientOrderId`
/ `orderLinkId`).

---

## 3. Error/Reject Taxonomy

### Classification

| Category | Examples | Action |
|---|---|---|
| **Rate Limit** (429) | Too many requests | Backoff, retry after cooldown |
| **Network** (timeout, connection reset) | Infrastructure failure | Retry with backoff (max 3 attempts) |
| **Insufficient Balance** (-2019, 110007) | Not enough margin | Reject order, log, alert |
| **Invalid Quantity** (-4131, 110012) | Lot size violation, min notional | Reject, fix quantity, retry once |
| **Order Not Found** (-2013) | Stale cancel attempt | Log and continue (order already filled/cancelled) |
| **Signature Error** (-1022, 10004) | Bad API key/secret | Halt all operations, alert |
| **Unknown** | Unmapped error codes | Log raw response, reject order |

### Exception Hierarchy

```
CTEError
└── ExecutionError
    ├── OrderRejectedError
    │   ├── InsufficientBalanceError
    │   └── InvalidQuantityError
    ├── RateLimitError
    └── ReconciliationError
```

### Retry Policy

| Error Type | Retries | Backoff | Notes |
|---|---|---|---|
| Network timeout | 3 | 1s, 2s, 4s | Exponential |
| HTTP 429 | Indefinite | Token bucket drain + refill | Wait for tokens |
| Venue reject | 0 | — | Immediately terminal |
| Signature error | 0 | — | Halt all operations |

---

## 4. Demo Position Reconciliation Logic

### Purpose

Detect divergence between our local position tracking and the venue's actual state.
Discrepancies indicate bugs, missed fills, or network partitions.

### Reconciliation Types

| Type | Local State | Venue State | Severity |
|---|---|---|---|
| PHANTOM_LOCAL | We have position | Venue doesn't | HIGH — we think we're exposed but aren't |
| PHANTOM_VENUE | We don't track it | Venue has position | CRITICAL — unmanaged exposure |
| QUANTITY_MISMATCH | Both agree, qty differs | | MEDIUM — partial fill missed |
| SIDE_MISMATCH | Both agree, side differs | | CRITICAL — directional confusion |

### Reconciliation Flow

```
Every 60 seconds:
  1. Query venue: GET /fapi/v2/positionRisk (Binance) or /v5/position/list (Bybit)
  2. Build venue position map: {symbol: (side, qty)}
  3. Build local position map: {symbol: (side, qty)}
  4. Compare:
     - Symbols in local but not venue → PHANTOM_LOCAL
     - Symbols in venue but not local → PHANTOM_VENUE
     - Both exist, side differs → SIDE_MISMATCH
     - Both exist, qty differs > 1% → QUANTITY_MISMATCH
  5. Log discrepancies, increment Prometheus counters
  6. If PHANTOM_VENUE or SIDE_MISMATCH → alert, consider emergency close
```

### Tolerance

Quantity match uses 1% tolerance to account for rounding differences
between our Decimal precision and venue's floating-point.

---

## 5. Acceptance Tests for 50 Demo Trades

### Pre-conditions

Before running the 50-trade acceptance test:
- [ ] Binance testnet API key configured and verified
- [ ] Bybit demo API key configured and verified
- [ ] Engine mode set to `testnet`
- [ ] Leverage locked to 1x
- [ ] Max position size: 0.01 BTC / 0.1 ETH
- [ ] Rate limiter configured and tested
- [ ] Reconciliation running every 60s

### Test Protocol

Run 50 trades over 48 hours with the full signal → risk → sizing → execution pipeline:

| Trade # | Type | Validation |
|---|---|---|
| 1-10 | Market BUY (open long) | Fills within 2s, venue confirms |
| 11-20 | Market SELL (close long) | Reduce-only flag set, position zeroed |
| 21-25 | Limit BUY (test limit flow) | Order submitted, eventually filled or cancelled |
| 26-30 | Cancel open limit orders | Cancel confirmed within 2s |
| 31-35 | Partial fill simulation | Track cumulative fill qty |
| 36-40 | Reject handling | Intentionally bad qty → graceful rejection |
| 41-45 | Rate limit behavior | Burst 10 orders → backoff without failure |
| 46-50 | Reconciliation | Every trade reconciles within 60s |

### Acceptance Criteria

| Metric | Threshold | Method |
|---|---|---|
| Order submit success rate | ≥ 95% | (successful submits) / (total attempts) |
| Fill confirmation latency | < 5s | Venue timestamp - local submit timestamp |
| Local ↔ venue reconciliation | 100% clean after each trade | Reconciliation check |
| Reject handling | 0 unhandled exceptions | Error log review |
| Rate limit violations | 0 HTTP 429s | Prometheus counter |
| State machine violations | 0 invalid transitions | Audit trail review |
| Position leaks | 0 phantom positions | Final reconciliation |
| Idempotency | 0 duplicate orders | Client order ID uniqueness |

### Post-Test Analysis

```sql
-- Verify all 50 trades have complete lifecycle
SELECT status, COUNT(*)
FROM cte.orders
WHERE time > '2024-01-15' AND venue IN ('binance_testnet', 'bybit_demo')
GROUP BY status;
-- Expected: 50 FILLED, 5 CANCELLED, 5 REJECTED

-- Verify no phantom positions
SELECT * FROM cte.positions WHERE status = 'open' AND closed_at IS NULL;
-- Expected: 0 rows (all positions closed)

-- Verify state machine audit trail is complete
SELECT client_order_id, jsonb_array_length(state_transitions) as transitions
FROM cte.orders WHERE time > '2024-01-15'
ORDER BY transitions DESC;
-- Expected: every order has ≥ 2 transitions (PENDING → SUBMITTING → ...)
```

---

## 6. Risk Controls Before Phase 3 Live

### Gate Requirements (ALL must pass)

| # | Control | Threshold | Why |
|---|---|---|---|
| 1 | 50-trade acceptance test | Pass | Proves adapter works end-to-end |
| 2 | 7 consecutive days on testnet | No crashes | Proves stability under real market conditions |
| 3 | Reconciliation clean rate | 100% for 7 days | Proves state tracking is accurate |
| 4 | No unhandled exceptions | 0 for 7 days | Proves error handling is complete |
| 5 | Order state machine | 0 invalid transitions for 7 days | Proves FSM is correct |
| 6 | Fill latency p99 | < 5 seconds | Proves execution speed is acceptable |
| 7 | Paper → testnet PnL comparison | Within 5% for same signals | Proves paper model is realistic |
| 8 | Rate limit violations | 0 for 7 days | Proves limiter is correctly tuned |
| 9 | Emergency stop test | Successfully closes all positions | Proves kill switch works |
| 10 | Manual review | Team sign-off | Human judgment on readiness |

### Phase 3 Live Constraints (Pre-configured)

When transitioning from testnet to live:

| Constraint | Value | Enforcement |
|---|---|---|
| Initial capital | $100 max | Config + code guard |
| Max single position | $50 | Sizing engine cap |
| Leverage | 1x only | Config lock |
| Symbols | BTCUSDT only (initially) | Config whitelist |
| Kill switch | API + CLI available | Tested in testnet phase |
| Monitoring | 24/7 alerts configured | Grafana + PagerDuty/Slack |
| First 7 days | Manual review of every trade | Ops process |
| Daily drawdown halt | 2% warning, 3% halt new, 5% close all | Risk manager (unchanged) |

### Live Adapter Notes (Phase 3 — not built yet)

The live adapter will be nearly identical to the testnet adapter, with these differences:
- Base URL: `https://fapi.binance.com` instead of testnet
- Real API keys with IP whitelisting
- Additional pre-flight checks (balance verification, leverage confirmation)
- Mandatory reconciliation every 30s (vs 60s on testnet)
- Hard-coded capital limits in the adapter itself (defense in depth)
