"""Run the CTE web dashboard (uvicorn).

Used by Docker (`python -m cte.dashboard`) and the `cte-dashboard` console script.
"""
from __future__ import annotations

import os


def main() -> None:
    """Start FastAPI dashboard on ``CTE_SERVICE_PORT`` (default 8080)."""
    import uvicorn

    host = os.environ.get("CTE_DASHBOARD_HOST", "0.0.0.0")
    port = int(os.environ.get("CTE_SERVICE_PORT", "8080"))
    log_level = os.environ.get("CTE_DASHBOARD_LOG_LEVEL", "info").lower()
    uvicorn.run("cte.dashboard.app:app", host=host, port=port, log_level=log_level)


if __name__ == "__main__":
    main()
