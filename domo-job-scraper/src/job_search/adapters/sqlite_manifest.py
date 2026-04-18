"""JobManifest adapter backed by SQLite."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..interfaces import JobManifest


class SQLiteJobManifest(JobManifest):
    """Thread-safe SQLite manifest for deduplicating job postings.

    Hides:
    - Path creation and sqlite3 connection setup
    - Schema creation (CREATE TABLE IF NOT EXISTS)
    - Migration: ALTER TABLE to add slack_ts column on older databases
    - threading.Lock wrapping every query
    - INSERT OR REPLACE upsert semantics
    - UTC timestamp generation

    Args:
        db_path: Path to the SQLite database file. Parent dirs are created
                 automatically if absent.
    """

    def __init__(self, db_path: str | Path = "data/EXPORTS/job_search.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS seen_jobs (
                    url             TEXT PRIMARY KEY,
                    title           TEXT NOT NULL DEFAULT '',
                    company         TEXT NOT NULL DEFAULT '',
                    slack_permalink TEXT NOT NULL DEFAULT '',
                    slack_ts        TEXT NOT NULL DEFAULT '',
                    found_at        TEXT NOT NULL DEFAULT (datetime('now'))
                );
                """
            )
            # Add slack_ts column to pre-existing databases that lack it.
            try:
                self._conn.execute("SELECT slack_ts FROM seen_jobs LIMIT 1")
            except Exception:
                self._conn.execute("ALTER TABLE seen_jobs ADD COLUMN slack_ts TEXT NOT NULL DEFAULT ''")
            self._conn.commit()

    # ------------------------------------------------------------------
    # JobManifest interface
    # ------------------------------------------------------------------

    def is_seen(self, url: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM seen_jobs WHERE url=?", (url,)).fetchone()
        return row is not None

    def get_slack_ts(self, url: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT slack_ts FROM seen_jobs WHERE url=?", (url,)).fetchone()
        return row["slack_ts"] if row else None

    def mark_seen(
        self,
        *,
        url: str,
        title: str = "",
        company: str = "",
        slack_permalink: str = "",
        slack_ts: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO seen_jobs
                    (url, title, company, slack_permalink, slack_ts, found_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (url, title, company, slack_permalink, slack_ts, now),
            )
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Diagnostics (not on the ABC — adapter-specific)
    # ------------------------------------------------------------------

    def count(self) -> int:
        """Total number of seen jobs. Useful for diagnostics."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM seen_jobs").fetchone()
        return row["n"] if row else 0
