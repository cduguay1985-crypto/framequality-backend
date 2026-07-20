"""Job state. SQLite so a restart doesn't lose in-flight work."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any

_LOCK = threading.Lock()

DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    status        TEXT NOT NULL,
    progress      INTEGER NOT NULL DEFAULT 0,
    stage         TEXT DEFAULT '',
    title         TEXT,
    profile       TEXT,
    source_url    TEXT,
    filename      TEXT,
    callback_url  TEXT,
    owner         TEXT,
    error         TEXT,
    report_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner, created_at DESC);
"""


class Store:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db_path = db_path
        with self._conn() as c:
            c.executescript(DDL)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        # WAL is faster but unsupported on some network/FUSE volumes (and on
        # container bind mounts). Fall back rather than refusing to start.
        try:
            c.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            c.execute("PRAGMA journal_mode=DELETE")
        return c

    def create(self, **fields: Any) -> str:
        job_id = uuid.uuid4().hex[:16]
        now = time.time()
        with _LOCK, self._conn() as c:
            c.execute(
                "INSERT INTO jobs (id, created_at, updated_at, status, progress, "
                "stage, title, profile, source_url, filename, callback_url, owner) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, now, now, "queued", 0, "queued",
                 fields.get("title"), fields.get("profile"),
                 fields.get("source_url"), fields.get("filename"),
                 fields.get("callback_url"), fields.get("owner")),
            )
        return job_id

    def update(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        with _LOCK, self._conn() as c:
            c.execute(f"UPDATE jobs SET {cols} WHERE id=?",
                      (*fields.values(), job_id))

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("report_json"):
            d["report"] = json.loads(d["report_json"])
        d.pop("report_json", None)
        return d

    def list(self, owner: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        q = ("SELECT id, created_at, updated_at, status, progress, stage, title, "
             "profile, filename, error FROM jobs")
        args: tuple = ()
        if owner:
            q += " WHERE owner=?"
            args = (owner,)
        q += " ORDER BY created_at DESC LIMIT ?"
        with self._conn() as c:
            rows = c.execute(q, (*args, limit)).fetchall()
        return [dict(r) for r in rows]

    def save_report(self, job_id: str, report: dict[str, Any]) -> None:
        self.update(job_id, report_json=json.dumps(report), status="complete",
                    progress=100, stage="complete")
