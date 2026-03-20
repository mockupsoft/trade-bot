"""Run the CTE web dashboard (uvicorn).

Used by Docker (`python -m cte.dashboard`) and the `cte-dashboard` console script.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path


def _load_repo_dotenv() -> None:
    """Populate ``os.environ`` from repo-root ``.env`` (local dev; Compose injects env)."""
    from dotenv import load_dotenv

    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    load_dotenv(repo_root / ".env")


def _bind_dual_stack_http(port: int, backlog: int) -> socket.socket:
    """Bind TCP port on ``::`` with ``IPV6_V6ONLY=0`` so IPv4 and IPv6 clients both work.

    Browsers often resolve ``localhost`` to ``::1`` first; uvicorn's default ``0.0.0.0`` bind
    accepts IPv4 only, which yields connection refused / "can't open page" for ``http://localhost``.
    """
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("::", port))
    sock.listen(backlog)
    sock.set_inheritable(True)
    return sock


def main() -> None:
    """Start FastAPI dashboard on ``CTE_SERVICE_PORT`` (default 8080)."""
    _load_repo_dotenv()
    import uvicorn
    from uvicorn import Config, Server

    host = os.environ.get("CTE_DASHBOARD_HOST", "0.0.0.0")
    port = int(os.environ.get("CTE_SERVICE_PORT", "8080"))
    log_level = os.environ.get("CTE_DASHBOARD_LOG_LEVEL", "info").lower()
    app_str = "cte.dashboard.app:app"

    if host in ("0.0.0.0", "") and os.environ.get("CTE_DASHBOARD_DUAL_STACK", "1") not in (
        "0",
        "false",
        "no",
    ):
        try:
            sock = _bind_dual_stack_http(port, backlog=2048)
        except OSError:
            uvicorn.run(app_str, host="0.0.0.0", port=port, log_level=log_level)
            return
        config = Config(app_str, host="::", port=port, log_level=log_level)
        Server(config).run(sockets=[sock])
        return

    uvicorn.run(app_str, host=host or "0.0.0.0", port=port, log_level=log_level)


if __name__ == "__main__":
    main()
