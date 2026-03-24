"""SQLite database manager for the data collection pipeline.

Uses WAL mode for concurrent read/write and asyncio.to_thread for non-blocking
access from async code — zero new dependencies beyond stdlib.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS cycle_metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle       INTEGER NOT NULL,
    phase       TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    cost_usd    REAL NOT NULL DEFAULT 0.0,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    blockers_detected INTEGER NOT NULL DEFAULT 0,
    blocker_types TEXT NOT NULL DEFAULT '',
    timestamp   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS git_commits (
    sha           TEXT PRIMARY KEY,
    author        TEXT NOT NULL,
    message       TEXT NOT NULL,
    timestamp     TEXT NOT NULL,
    files_changed INTEGER NOT NULL DEFAULT 0,
    insertions    INTEGER NOT NULL DEFAULT 0,
    deletions     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS okr_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    objective_id    TEXT NOT NULL,
    objective_desc  TEXT NOT NULL DEFAULT '',
    progress        REAL NOT NULL DEFAULT 0.0,
    key_results_json TEXT NOT NULL DEFAULT '[]',
    cycle           INTEGER NOT NULL DEFAULT 0,
    timestamp       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_name TEXT NOT NULL,
    severity  TEXT NOT NULL,
    message   TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cycle_metrics_cycle ON cycle_metrics(cycle);
CREATE INDEX IF NOT EXISTS idx_okr_snapshots_obj ON okr_snapshots(objective_id, cycle);
CREATE INDEX IF NOT EXISTS idx_alerts_rule ON alerts(rule_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_git_commits_ts ON git_commits(timestamp);
"""


class Database:
    """Async-friendly SQLite wrapper. All writes go through a single connection
    protected by an asyncio.Lock to avoid contention."""

    def __init__(self, path: str | Path = "data/olympus.db") -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        """Create DB file, enable WAL, and apply schema."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await asyncio.to_thread(self._open)
        await asyncio.to_thread(self._apply_schema)
        logger.info("Database initialised at %s", self._path)

    async def close(self) -> None:
        if self._conn:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    # ── writes ────────────────────────────────────────────────

    async def insert_cycle_metric(self, **kw: Any) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._execute,
                "INSERT INTO cycle_metrics (cycle, phase, duration_ms, cost_usd, "
                "tokens_used, blockers_detected, blocker_types, timestamp) "
                "VALUES (:cycle, :phase, :duration_ms, :cost_usd, :tokens_used, "
                ":blockers_detected, :blocker_types, :timestamp)",
                kw,
            )

    async def upsert_git_commit(self, **kw: Any) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._execute,
                "INSERT OR IGNORE INTO git_commits "
                "(sha, author, message, timestamp, files_changed, insertions, deletions) "
                "VALUES (:sha, :author, :message, :timestamp, :files_changed, "
                ":insertions, :deletions)",
                kw,
            )

    async def insert_okr_snapshot(self, **kw: Any) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._execute,
                "INSERT INTO okr_snapshots (objective_id, objective_desc, progress, "
                "key_results_json, cycle, timestamp) "
                "VALUES (:objective_id, :objective_desc, :progress, "
                ":key_results_json, :cycle, :timestamp)",
                kw,
            )

    async def insert_alert(self, **kw: Any) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._execute,
                "INSERT INTO alerts (rule_name, severity, message, data_json, timestamp) "
                "VALUES (:rule_name, :severity, :message, :data_json, :timestamp)",
                kw,
            )

    # ── reads ─────────────────────────────────────────────────

    async def get_cycle_metrics(self, limit: int = 50) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._fetchall,
            "SELECT * FROM cycle_metrics ORDER BY cycle DESC LIMIT ?",
            (limit,),
        )

    async def get_recent_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._fetchall,
            "SELECT * FROM alerts ORDER BY id DESC LIMIT ?",
            (limit,),
        )

    async def get_git_commits(self, limit: int = 50) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._fetchall,
            "SELECT * FROM git_commits ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )

    async def get_okr_snapshots(
        self, objective_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if objective_id:
            return await asyncio.to_thread(
                self._fetchall,
                "SELECT * FROM okr_snapshots WHERE objective_id = ? "
                "ORDER BY cycle DESC LIMIT ?",
                (objective_id, limit),
            )
        return await asyncio.to_thread(
            self._fetchall,
            "SELECT * FROM okr_snapshots ORDER BY cycle DESC LIMIT ?",
            (limit,),
        )

    async def get_metric_value(self, metric: str, cycle: int) -> float | None:
        """Retrieve a single metric value for a given cycle."""
        rows = await asyncio.to_thread(
            self._fetchall,
            f"SELECT {metric} FROM cycle_metrics WHERE cycle = ?",
            (cycle,),
        )
        if rows:
            return rows[0].get(metric)
        return None

    async def get_last_n_metric_values(
        self, metric: str, n: int
    ) -> list[float]:
        """Return the last N values of a metric, newest first."""
        rows = await asyncio.to_thread(
            self._fetchall,
            f"SELECT {metric} FROM cycle_metrics ORDER BY cycle DESC LIMIT ?",
            (n,),
        )
        return [r[metric] for r in rows if r.get(metric) is not None]

    # ── internals ─────────────────────────────────────────────

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def _apply_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    def _execute(self, sql: str, params: Any = None) -> None:
        assert self._conn is not None
        self._conn.execute(sql, params or {})
        self._conn.commit()

    def _fetchall(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        assert self._conn is not None
        cur = self._conn.execute(sql, params or ())
        return [dict(row) for row in cur.fetchall()]
