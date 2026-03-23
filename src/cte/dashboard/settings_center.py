"""Versioned settings center (draft -> approve -> schedule/apply)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from cte.db.pool import DatabasePool

_CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS cte;"
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS cte.settings_revisions (
    revision_id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    changes JSONB NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT 'dashboard_user',
    created_at TIMESTAMPTZ NOT NULL,
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    scheduled_by TEXT,
    scheduled_at TIMESTAMPTZ,
    scheduled_for TIMESTAMPTZ,
    applied_by TEXT,
    applied_at TIMESTAMPTZ,
    supersedes_revision_id UUID
);
"""
_ALTERS = (
    "ALTER TABLE cte.settings_revisions ADD COLUMN IF NOT EXISTS scheduled_by TEXT;",
    "ALTER TABLE cte.settings_revisions ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMPTZ;",
    "ALTER TABLE cte.settings_revisions ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMPTZ;",
    "ALTER TABLE cte.settings_revisions ADD COLUMN IF NOT EXISTS supersedes_revision_id UUID;",
)
_CREATE_IDX = "CREATE INDEX IF NOT EXISTS idx_settings_rev_created ON cte.settings_revisions (created_at DESC);"


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    txt = value.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    dt = datetime.fromisoformat(txt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _validate_changes(changes: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in changes.items():
        key = str(k).strip().upper()
        if not key.startswith("CTE_"):
            raise ValueError(f"Unsupported key: {k}")
        if len(key) > 128:
            raise ValueError(f"Key too long: {k}")
        val = str(v)
        if len(val) > 4000:
            raise ValueError(f"Value too long: {k}")
        out[key] = val
    if not out:
        raise ValueError("changes cannot be empty")
    return out


def _coerce_changes(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except Exception:
            return {}
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items()}
        return {}
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {}


class InMemorySettingsCenter:
    def __init__(self) -> None:
        self._revisions: list[dict[str, Any]] = []

    async def ensure_ready(self) -> None:
        return

    async def create_draft(
        self,
        changes: dict[str, str],
        *,
        name: str = "draft",
        note: str = "",
        created_by: str = "dashboard_user",
    ) -> dict[str, Any]:
        clean = _validate_changes(changes)
        row = {
            "revision_id": str(uuid4()),
            "name": name.strip() or "draft",
            "status": "draft",
            "changes": clean,
            "note": note,
            "created_by": created_by,
            "created_at": _iso_now(),
            "approved_by": None,
            "approved_at": None,
            "scheduled_by": None,
            "scheduled_at": None,
            "scheduled_for": None,
            "applied_by": None,
            "applied_at": None,
            "supersedes_revision_id": None,
        }
        self._revisions.append(row)
        return row

    async def list_revisions(
        self, *, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = list(reversed(self._revisions))
        if status:
            rows = [r for r in rows if str(r.get("status")) == status]
        return rows[: max(1, min(limit, 200))]

    async def get_revision(self, revision_id: str) -> dict[str, Any] | None:
        for row in self._revisions:
            if str(row.get("revision_id")) == revision_id:
                return row
        return None

    async def approve(
        self, revision_id: str, *, approved_by: str = "dashboard_user"
    ) -> dict[str, Any]:
        row = await self.get_revision(revision_id)
        if row is None:
            raise KeyError("revision not found")
        if row.get("status") != "draft":
            raise ValueError("only draft revisions can be approved")
        if row.get("created_by") == approved_by:
            raise ValueError("approval must be performed by a different actor")
        row["status"] = "approved"
        row["approved_by"] = approved_by
        row["approved_at"] = _iso_now()
        return row

    async def schedule_apply(
        self,
        revision_id: str,
        *,
        scheduled_for: datetime,
        scheduled_by: str = "dashboard_user",
    ) -> dict[str, Any]:
        row = await self.get_revision(revision_id)
        if row is None:
            raise KeyError("revision not found")
        if row.get("status") not in {"approved", "scheduled"}:
            raise ValueError("only approved revisions can be scheduled")
        row["status"] = "scheduled"
        row["scheduled_by"] = scheduled_by
        row["scheduled_at"] = _iso_now()
        row["scheduled_for"] = (
            scheduled_for.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        return row

    async def apply(
        self, revision_id: str, *, applied_by: str = "dashboard_user"
    ) -> dict[str, Any]:
        row = await self.get_revision(revision_id)
        if row is None:
            raise KeyError("revision not found")
        if row.get("status") not in {"approved", "scheduled"}:
            raise ValueError("only approved/scheduled revisions can be applied")
        for k, v in _coerce_changes(row.get("changes")).items():
            os.environ[str(k)] = str(v)
        row["status"] = "applied"
        row["applied_by"] = applied_by
        row["applied_at"] = _iso_now()
        return row

    async def rollback_to(
        self, revision_id: str, *, actor: str = "dashboard_user"
    ) -> dict[str, Any]:
        target = await self.get_revision(revision_id)
        if target is None:
            raise KeyError("revision not found")
        draft = await self.create_draft(
            _coerce_changes(target.get("changes")),
            name=f"rollback-{revision_id[:8]}",
            note=f"rollback to {revision_id}",
            created_by=actor,
        )
        draft["status"] = "applied"
        draft["approved_by"] = actor
        draft["approved_at"] = _iso_now()
        draft["applied_by"] = actor
        draft["applied_at"] = _iso_now()
        draft["supersedes_revision_id"] = revision_id
        for k, v in _coerce_changes(draft.get("changes")).items():
            os.environ[k] = v
        return draft

    async def active_revision(self) -> dict[str, Any] | None:
        for row in reversed(self._revisions):
            if row.get("status") == "applied":
                return row
        return None

    async def pending_schedules(self) -> list[dict[str, Any]]:
        rows = [
            r for r in self._revisions if r.get("status") == "scheduled" and r.get("scheduled_for")
        ]
        return sorted(rows, key=lambda x: str(x.get("scheduled_for") or ""))


class DbSettingsCenter:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def ensure_ready(self) -> None:
        await self._db.execute(_CREATE_SCHEMA)
        await self._db.execute(_CREATE_TABLE)
        for sql in _ALTERS:
            await self._db.execute(sql)
        await self._db.execute(_CREATE_IDX)

    async def create_draft(
        self,
        changes: dict[str, str],
        *,
        name: str = "draft",
        note: str = "",
        created_by: str = "dashboard_user",
    ) -> dict[str, Any]:
        clean = _validate_changes(changes)
        rid = str(uuid4())
        now = datetime.now(UTC)
        q = """
        INSERT INTO cte.settings_revisions (revision_id, name, status, changes, note, created_by, created_at)
        VALUES ($1::uuid, $2, 'draft', $3::jsonb, $4, $5, $6)
        """
        await self._db.execute(
            q, rid, name.strip() or "draft", json.dumps(clean), note, created_by, now
        )
        row = await self.get_revision(rid)
        if row is None:
            raise RuntimeError("failed to create revision")
        return row

    async def list_revisions(
        self, *, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        lim = max(1, min(limit, 200))
        if status:
            rows = await self._db.fetch(
                "SELECT * FROM cte.settings_revisions WHERE status=$1 ORDER BY created_at DESC LIMIT $2",
                status,
                lim,
            )
        else:
            rows = await self._db.fetch(
                "SELECT * FROM cte.settings_revisions ORDER BY created_at DESC LIMIT $1",
                lim,
            )
        return [self._to_dict(r) for r in rows]

    async def get_revision(self, revision_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT * FROM cte.settings_revisions WHERE revision_id=$1::uuid",
            revision_id,
        )
        return self._to_dict(row) if row else None

    async def approve(
        self, revision_id: str, *, approved_by: str = "dashboard_user"
    ) -> dict[str, Any]:
        row = await self.get_revision(revision_id)
        if row is None:
            raise KeyError("revision not found")
        if row.get("status") != "draft":
            raise ValueError("only draft revisions can be approved")
        if row.get("created_by") == approved_by:
            raise ValueError("approval must be performed by a different actor")
        q = """
        UPDATE cte.settings_revisions
        SET status='approved', approved_by=$2, approved_at=$3
        WHERE revision_id=$1::uuid AND status='draft'
        """
        out = await self._db.execute(q, revision_id, approved_by, datetime.now(UTC))
        if out.endswith("0"):
            raise ValueError("only draft revisions can be approved")
        row = await self.get_revision(revision_id)
        if row is None:
            raise RuntimeError("revision not found after approve")
        return row

    async def schedule_apply(
        self,
        revision_id: str,
        *,
        scheduled_for: datetime,
        scheduled_by: str = "dashboard_user",
    ) -> dict[str, Any]:
        q = """
        UPDATE cte.settings_revisions
        SET status='scheduled', scheduled_by=$2, scheduled_at=$3, scheduled_for=$4
        WHERE revision_id=$1::uuid AND status IN ('approved','scheduled')
        """
        out = await self._db.execute(
            q,
            revision_id,
            scheduled_by,
            datetime.now(UTC),
            scheduled_for,
        )
        if out.endswith("0"):
            row = await self.get_revision(revision_id)
            if row is None:
                raise KeyError("revision not found")
            raise ValueError("only approved revisions can be scheduled")
        row = await self.get_revision(revision_id)
        if row is None:
            raise RuntimeError("revision not found after schedule")
        return row

    async def apply(
        self, revision_id: str, *, applied_by: str = "dashboard_user"
    ) -> dict[str, Any]:
        row = await self.get_revision(revision_id)
        if row is None:
            raise KeyError("revision not found")
        if row.get("status") not in {"approved", "scheduled"}:
            raise ValueError("only approved/scheduled revisions can be applied")
        for k, v in _coerce_changes(row.get("changes")).items():
            os.environ[k] = v
        q = """
        UPDATE cte.settings_revisions
        SET status='applied', applied_by=$2, applied_at=$3
        WHERE revision_id=$1::uuid
        """
        await self._db.execute(q, revision_id, applied_by, datetime.now(UTC))
        row = await self.get_revision(revision_id)
        if row is None:
            raise RuntimeError("revision not found after apply")
        return row

    async def rollback_to(
        self, revision_id: str, *, actor: str = "dashboard_user"
    ) -> dict[str, Any]:
        target = await self.get_revision(revision_id)
        if target is None:
            raise KeyError("revision not found")
        clean = _validate_changes(_coerce_changes(target.get("changes")))
        rid = str(uuid4())
        now = datetime.now(UTC)
        q = """
        INSERT INTO cte.settings_revisions (
            revision_id, name, status, changes, note, created_by, created_at,
            approved_by, approved_at, applied_by, applied_at, supersedes_revision_id
        ) VALUES ($1::uuid, $2, 'applied', $3::jsonb, $4, $5, $6, $7, $8, $9, $10, $11::uuid)
        """
        await self._db.execute(
            q,
            rid,
            f"rollback-{revision_id[:8]}",
            json.dumps(clean),
            f"rollback to {revision_id}",
            actor,
            now,
            actor,
            now,
            actor,
            now,
            revision_id,
        )
        for k, v in clean.items():
            os.environ[k] = v
        row = await self.get_revision(rid)
        if row is None:
            raise RuntimeError("failed to create rollback revision")
        return row

    async def active_revision(self) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT * FROM cte.settings_revisions WHERE status='applied' ORDER BY applied_at DESC NULLS LAST LIMIT 1"
        )
        return self._to_dict(row) if row else None

    async def pending_schedules(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT * FROM cte.settings_revisions WHERE status='scheduled' AND scheduled_for IS NOT NULL ORDER BY scheduled_for ASC LIMIT 200"
        )
        return [self._to_dict(r) for r in rows]

    def _to_dict(self, row: Any) -> dict[str, Any]:
        d = dict(row)
        d["changes"] = _coerce_changes(d.get("changes"))
        for k in (
            "created_at",
            "approved_at",
            "scheduled_at",
            "scheduled_for",
            "applied_at",
        ):
            v = d.get(k)
            if isinstance(v, datetime):
                d[k] = v.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        d["revision_id"] = str(d.get("revision_id"))
        if d.get("supersedes_revision_id") is not None:
            d["supersedes_revision_id"] = str(d.get("supersedes_revision_id"))
        return d
