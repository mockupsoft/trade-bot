"""Run the CTE web dashboard (uvicorn).

Used by Docker (`python -m cte.dashboard`) and the `cte-dashboard` console script.
"""
from __future__ import annotations

import os
from pathlib import Path


def _load_repo_dotenv() -> None:
    """Populate ``os.environ`` from repo-root ``.env`` (local dev; Compose injects env)."""
    from dotenv import load_dotenv

    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    load_dotenv(repo_root / ".env")


def main() -> None:
    """Start FastAPI dashboard on ``CTE_SERVICE_PORT`` (default 8080)."""
    _load_repo_dotenv()
    import uvicorn

    host = os.environ.get("CTE_DASHBOARD_HOST", "0.0.0.0")
    port = int(os.environ.get("CTE_SERVICE_PORT", "8080"))
    log_level = os.environ.get("CTE_DASHBOARD_LOG_LEVEL", "info").lower()
    uvicorn.run("cte.dashboard.app:app", host=host, port=port, log_level=log_level)


if __name__ == "__main__":
    main()
