# CTE Operations Runbook

## Environment Segregation

| Environment | Config | Database | Venue | Dashboard |
|---|---|---|---|---|
| **dev** | `.env.dev` | `cte_dev` | — (unit tests only) | localhost:8080 |
| **paper** | `.env.paper` | `cte_paper` | Binance/Bybit WS (read-only) | :8080 |
| **demo** | `.env.demo` | `cte_demo` | Binance testnet / Bybit demo | :8080 |
| **live** | `.env.live` | `cte_live` | Binance production | :8080 (restricted) |

Each environment uses a separate database and epoch. Never share databases across environments.

## Secret Management

| Secret | Storage | Rotation |
|---|---|---|
| DB password | `CTE_DB_PASSWORD` env var | 90 days |
| Redis password | `CTE_REDIS_PASSWORD` env var | 90 days |
| Binance testnet API key | `CTE_BINANCE_TESTNET_API_KEY` | On compromise |
| Binance testnet secret | `CTE_BINANCE_TESTNET_API_SECRET` | On compromise |
| Bybit demo API key | `CTE_BYBIT_DEMO_API_KEY` | On compromise |
| Bybit demo secret | `CTE_BYBIT_DEMO_API_SECRET` | On compromise |
| Grafana admin password | `GRAFANA_PASSWORD` | 30 days |

Secrets are NEVER committed to git, logged, or stored in config files.
Use a secrets manager (Vault, AWS Secrets Manager) in production.

## Emergency Procedures

### EMERGENCY STOP (P0)
**Trigger**: Daily drawdown > 5%, or manual decision.
1. Dashboard → Operations → EMERGENCY STOP
2. OR: `curl -X POST http://localhost:8080/api/ops/emergency_stop`
3. Verify: all positions closed, mode = "halted"
4. Investigate root cause before resuming

### Stale Data (P1)
**Trigger**: Feature freshness < 0.3 for > 5 minutes.
1. Check venue WebSocket connections in dashboard
2. If disconnected: connections will auto-reconnect
3. If reconnect fails > 5 times: restart connector service
4. If data stale but connected: check normalizer service logs

### Reconciliation Failure (P1)
**Trigger**: Local vs venue position mismatch.
1. Check `/api/ops/status` for discrepancy details
2. PHANTOM_VENUE (untracked position): manually close on venue
3. PHANTOM_LOCAL (stale local state): clear local state, restart
4. QUANTITY_MISMATCH: investigate partial fills in order log

### Drawdown Breach (P2)
**Trigger**: Daily drawdown > 2% (warning), > 3% (halt).
1. At 2%: review recent trades, check for regime change
2. At 3%: engine auto-halts new entries; review and decide
3. Resume only after root cause identified

## Database Maintenance

### TimescaleDB Compression
```sql
-- Enable compression on tables older than 7 days
ALTER TABLE cte.trades SET (timescaledb.compress);
SELECT add_compression_policy('cte.trades', INTERVAL '7 days');

ALTER TABLE cte.streaming_features SET (timescaledb.compress);
SELECT add_compression_policy('cte.streaming_features', INTERVAL '3 days');
```

### Data Retention
| Table | Retention | Policy |
|---|---|---|
| `cte.trades` | 90 days | Drop chunks older than 90 days |
| `cte.streaming_features` | 30 days | Drop chunks older than 30 days |
| `cte.trade_log` | 365 days | Archive to cold storage |
| `cte.positions` | Forever | Never delete |
| `cte.epoch_daily_summary` | Forever | Never delete |

### Backup
```bash
# Daily backup
pg_dump -h localhost -U cte cte | gzip > backup_$(date +%Y%m%d).sql.gz

# Restore
gunzip < backup_20240315.sql.gz | psql -h localhost -U cte cte
```

## Deployment

### Rolling Update (Zero Downtime)
1. Build new image: `docker compose build`
2. Update one service at a time: `docker compose up -d --no-deps <service>`
3. Verify health: `curl http://localhost:<port>/api/<service>/health`
4. Proceed to next service

### Rollback
1. `docker compose down <service>`
2. `docker compose up -d --no-deps <service>` (with previous image tag)
3. Verify health

## Monitoring Checklist (Daily)

- [ ] All services healthy (dashboard → Overview)
- [ ] WebSocket connections stable (0 reconnects in last hour)
- [ ] Feature freshness > 0.9 for all symbols
- [ ] No reconciliation discrepancies
- [ ] Daily PnL within expected range
- [ ] No unacknowledged alerts
- [ ] Rate limit headroom > 50%
