#!/usr/bin/env python3
"""Deploy compose service with release metadata environment.

Examples:
  py scripts/ops_tools/deploy_analytics_with_release.py
  py scripts/ops_tools/deploy_analytics_with_release.py --profile validation --service analytics-validation
  py scripts/ops_tools/deploy_analytics_with_release.py --rollback-from 9f2b1a4
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def _run(
    cmd: list[str], *, check: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, env=env)


def _git_commit(repo_root: Path) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        return "unknown"
    value = out.stdout.strip()
    return value if value else "unknown"


def _default_tag(commit: str) -> str:
    short = commit[:8] if commit and commit != "unknown" else "local"
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"{short}-{stamp}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--compose-file", default="deploy/docker-compose.yml")
    ap.add_argument("--service", default="analytics")
    ap.add_argument("--profile", default="")
    ap.add_argument("--image", default="deploy-analytics:latest")
    ap.add_argument("--tag", default="")
    ap.add_argument("--rollback-from", default="")
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    commit = _git_commit(repo_root)
    tag = args.tag.strip() or _default_tag(commit)

    env = dict(os.environ)
    env["CTE_RELEASE_COMMIT"] = commit
    env["CTE_RELEASE_IMAGE"] = args.image.strip() or "deploy-analytics:latest"
    env["CTE_RELEASE_TAG"] = tag
    if args.rollback_from.strip():
        env["CTE_RELEASE_ROLLBACK_FROM"] = args.rollback_from.strip()

    cmd = ["docker", "compose", "-f", args.compose_file]
    if args.profile.strip():
        cmd.extend(["--profile", args.profile.strip()])
    cmd.extend(["up", "-d"])
    if not args.no_build:
        cmd.append("--build")
    cmd.append(args.service)

    print("Release metadata:")
    print("  CTE_RELEASE_COMMIT=", env["CTE_RELEASE_COMMIT"])
    print("  CTE_RELEASE_IMAGE=", env["CTE_RELEASE_IMAGE"])
    print("  CTE_RELEASE_TAG=", env["CTE_RELEASE_TAG"])
    print("  CTE_RELEASE_ROLLBACK_FROM=", env.get("CTE_RELEASE_ROLLBACK_FROM", ""))
    print("Command:")
    print(" ", " ".join(cmd))

    if args.dry_run:
        return 0

    _run(cmd, env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
