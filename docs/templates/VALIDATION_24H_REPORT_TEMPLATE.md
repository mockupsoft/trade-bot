# 24h validation report — v1 long-only strategy

**Operator:**  
**Date range (UTC):** start `________________` → end `________________`  
**Git commit:** `________________`  
**Host / environment:** (e.g. Docker `analytics`, Windows, Linux)  

**Scope:** Long-only strategy (`OPEN_LONG`). Short strategy **out of scope** for this report.

---

## Configuration (names only — no secrets)

| Variable | Value (masked or enum) |
|----------|-------------------------|
| `CTE_ENGINE_MODE` | |
| `CTE_EXECUTION_MODE` | |
| `CTE_DASHBOARD_EXECUTION_VENUE` | |
| `CTE_DASHBOARD_VENUE_PROOF_SYMBOL` | |
| Proof-window overrides active? | yes / no (if yes, list **names** only; remove after campaign) |

---

## Snapshot artifacts

| Path / reference | Description |
|------------------|-------------|
| | Directory containing hourly `collect_validation_snapshot.sh` outputs |
| | Optional: `POST /api/campaign/snapshot` responses aggregated |

---

## Outcomes (end of window)

| Metric | Source | Value |
|--------|--------|-------|
| `ticks_ok` (approx) | `/api/paper/status` | |
| `entries_total` | `/api/paper/status` | |
| `exits_recorded` | `/api/paper/status` | |
| Closed trades (paper epoch) | `/api/analytics/summary?epoch=crypto_v1_paper` | |
| Closed trades (demo epoch) | `/api/analytics/summary?epoch=crypto_v1_demo` | |
| `demo_exchange` trade count | `/api/analytics/trades?source=demo_exchange&epoch=crypto_v1_demo` | |

---

## Blockers / incidents

| Time (UTC) | Symptom | Resolution |
|------------|---------|------------|
| | | |

---

## Classification

- [ ] Feed stable (connected, acceptable latency)
- [ ] No sustained ops halt required
- [ ] Analytics journal consistent with expected `source` / `epoch`
- [ ] Proof-window env removed or scheduled for removal if this was a production-like host

**Sign-off:** ________________
