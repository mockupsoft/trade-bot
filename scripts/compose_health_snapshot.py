#!/usr/bin/env python3
"""Print a quick health snapshot for docker compose services.

Examples:
  py scripts/compose_health_snapshot.py
  py scripts/compose_health_snapshot.py --service analytics --service grafana
  py scripts/compose_health_snapshot.py --strict
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ServiceStatus:
    service: str
    container: str
    state: str
    health: str
    restart_count: int
    ports: str


def _run(cmd: list[str]) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        msg = out.stderr.strip() or out.stdout.strip() or "unknown error"
        raise RuntimeError(f"command failed ({' '.join(cmd)}): {msg}")
    return out.stdout


def _compose_ps(compose_file: Path, services: list[str]) -> list[dict]:
    cmd = ["docker", "compose", "-f", str(compose_file), "ps", "--format", "json"]
    cmd.extend(services)
    raw = _run(cmd)
    rows: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        rows.append(json.loads(line))
    return rows


def _restart_count(container_name: str) -> int:
    raw = _run(["docker", "inspect", "--format", "{{.RestartCount}}", container_name]).strip()
    try:
        return int(raw)
    except ValueError:
        return 0


def _status_from_row(row: dict) -> ServiceStatus:
    container = str(row.get("Name") or row.get("Names") or "")
    return ServiceStatus(
        service=str(row.get("Service") or "unknown"),
        container=container,
        state=str(row.get("State") or "unknown"),
        health=str(row.get("Health") or ""),
        restart_count=_restart_count(container) if container else 0,
        ports=str(row.get("Ports") or ""),
    )


def _print_table(statuses: list[ServiceStatus]) -> None:
    headers = ("service", "state", "health", "restarts", "ports")
    widths = {
        "service": max(len(headers[0]), *(len(s.service) for s in statuses)),
        "state": max(len(headers[1]), *(len(s.state) for s in statuses)),
        "health": max(len(headers[2]), *(len(s.health or "-") for s in statuses)),
        "restarts": max(len(headers[3]), *(len(str(s.restart_count)) for s in statuses)),
        "ports": max(len(headers[4]), *(len(s.ports or "-") for s in statuses)),
    }

    def row(service: str, state: str, health: str, restarts: str, ports: str) -> str:
        return (
            f"{service:<{widths['service']}}  "
            f"{state:<{widths['state']}}  "
            f"{health:<{widths['health']}}  "
            f"{restarts:>{widths['restarts']}}  "
            f"{ports:<{widths['ports']}}"
        )

    print(row(*headers))
    print(
        row(
            "-" * widths["service"],
            "-" * widths["state"],
            "-" * widths["health"],
            "-" * widths["restarts"],
            "-" * widths["ports"],
        )
    )
    for s in sorted(statuses, key=lambda x: x.service):
        print(row(s.service, s.state, s.health or "-", str(s.restart_count), s.ports or "-"))


def _is_ok(s: ServiceStatus) -> bool:
    if s.state != "running":
        return False
    if s.health and s.health not in ("healthy", ""):
        return False
    if s.restart_count > 0:
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--compose-file", default="deploy/docker-compose.yml")
    ap.add_argument("--service", action="append", default=[])
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    compose_file = Path(args.compose_file)
    rows = _compose_ps(compose_file, args.service)
    if not rows:
        print("No compose services found.")
        return 1

    statuses = [_status_from_row(r) for r in rows]
    _print_table(statuses)

    ok = all(_is_ok(s) for s in statuses)
    print()
    print("Overall:", "OK" if ok else "DEGRADED")
    if args.strict and not ok:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
