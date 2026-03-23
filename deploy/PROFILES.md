# Deploy Profiles

## Production-safe baseline (default)

Runs `analytics` on `:8080` with conservative defaults from `deploy/env/prod-defaults.env`.

```bash
docker compose -f deploy/docker-compose.yml up -d --build analytics
```

## Validation / proof profile

Runs `analytics-validation` on `:8081` with relaxed validation settings from
`deploy/env/validation-overrides.env`.

```bash
docker compose -f deploy/docker-compose.yml --profile validation up -d --build analytics-validation
```

Use this profile only for controlled validation/proof runs.

## Release metadata injection

Use the helper below so `CTE_RELEASE_COMMIT`, `CTE_RELEASE_IMAGE`, and `CTE_RELEASE_TAG`
are injected automatically during deploy:

```bash
py scripts/ops_tools/deploy_analytics_with_release.py
py scripts/ops_tools/deploy_analytics_with_release.py --profile validation --service analytics-validation
```

Rollback marker can be attached explicitly:

```bash
py scripts/ops_tools/deploy_analytics_with_release.py --rollback-from <previous-commit>
```

## GitHub Actions deploy

Manual workflow: `.github/workflows/deploy.yml`

- Trigger from **Actions -> Deploy -> Run workflow**
- Choose `profile` (`prod` or `validation`)
- Optional `rollback_from`

The workflow uses the same deploy helper and injects identical release metadata env vars.
