"""
db/database.py
--------------
SQLite persistence layer.

Tables
  seen_listings  – every listing the watcher has ever matched, keyed by id.
                   Used to avoid duplicate alerts.
  watch_log      – one row per watcher run, for audit / debugging.
"""

import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str = "komatsu_bot.db"):
        self.db_path = db_path
        self._init_schema()

    # ---- Schema ----------------------------------------------------------

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS seen_listings (
                    id          TEXT PRIMARY KEY,
                    data        TEXT    NOT NULL,
                    first_seen  TEXT    DEFAULT (datetime('now')),
                    last_seen   TEXT    DEFAULT (datetime('now')),
                    notified    INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS watch_log (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp    TEXT    DEFAULT (datetime('now')),
                    models       TEXT,
                    total_found  INTEGER DEFAULT 0,
                    new_found    INTEGER DEFAULT 0
                );
            """)

    # ---- Connection helper -----------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- Listing helpers -------------------------------------------------

    def is_seen(self, listing_id: str) -> bool:
        """Return True if this listing ID has already been recorded."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM seen_listings WHERE id = ?", (listing_id,)
            ).fetchone()
        return row is not None

    def mark_seen(self, listing_id: str, data: dict, notified: bool = True):
        """Insert or update a listing record."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO seen_listings (id, data, last_seen, notified)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    notified  = excluded.notified
                """,
                (
                    listing_id,
                    json.dumps(data, ensure_ascii=False),
                    datetime.now().isoformat(timespec="seconds"),
                    int(notified),
                ),
            )

    def get_all_seen(self) -> list[dict]:
        """Return all seen listings, newest first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, data, first_seen, last_seen, notified "
                "FROM seen_listings ORDER BY first_seen DESC"
            ).fetchall()
        return [
            {
                "id": r["id"],
                "data": json.loads(r["data"]),
                "first_seen": r["first_seen"],
                "last_seen": r["last_seen"],
                "notified": bool(r["notified"]),
            }
            for r in rows
        ]

    def get_listing(self, listing_id: str) -> Optional[dict]:
        """Fetch a single seen listing by ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, data, first_seen, last_seen FROM seen_listings WHERE id = ?",
                (listing_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "data": json.loads(row["data"]),
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
        }

    def delete_by_model(self, model_name: str) -> int:
        """
        Delete all seen listings whose title contains the model name.
        Returns the number of rows deleted.
        Uses SQLite json_extract so it matches the 'title' field precisely.
        """
        with self._conn() as conn:
            result = conn.execute(
                "DELETE FROM seen_listings "
                "WHERE UPPER(json_extract(data, '$.title')) LIKE UPPER(?)",
                (f"%{model_name}%",),
            )
            return result.rowcount

    # ---- Watch log -------------------------------------------------------

    def log_run(self, models: list[str], total_found: int, new_found: int):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO watch_log (models, total_found, new_found) VALUES (?, ?, ?)",
                (", ".join(models), total_found, new_found),
            )

    def get_recent_runs(self, limit: int = 10) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM watch_log ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
