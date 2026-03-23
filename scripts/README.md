# Scripts Layout

This directory is split conceptually into two groups:

- `ops tools` (persistent operational tools)
- `forensics` (one-off validation/proof helpers and archives)

## Ops Tools (persistent)

- `compose_health_snapshot.py` - Compose service health snapshot
- `ops_tools/deploy_analytics_with_release.py` - Release metadata aware deploy
- `migrate.py` - Database migration runner
- `start_dashboard.sh` - Local dashboard bootstrap
- `validate_data.py` - Data quality checks
- `verify_binance_testnet_auth.py` - Testnet auth verification
- `smoke_bybit_demo.py` - Bybit demo smoke check

## Forensics / Validation (archive-style)

- `run_testnet_e2e_proof.py`
- `testnet_validation_dashboard.py`
- `testnet_validation_report.py`
- `validation_audit_runner.py`
- `verify_testnet_entry_logs.py`
- `poll_paper_snapshots.py`
- `collect_validation_snapshot.sh`
- `testnet_validation_preflight.ps1`
- `test_runner_entry.py`

If a forensics script becomes part of recurring operations, move it into the ops tools list.
