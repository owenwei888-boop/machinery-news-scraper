# src/machinery_news_scraper/pusher/queue.py
"""SQLite-backed queue for items that failed to push.
Survives process restarts so we don't lose items on a crash."""
from __future__ import annotations
import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..models import Item


SCHEMA = """
CREATE TABLE IF NOT EXISTS failed_pushes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT NOT NULL UNIQUE,
    item_json TEXT NOT NULL,
    reason TEXT,
    attempts INTEGER NOT NULL DEFAULT 1,
    first_queued_at REAL NOT NULL,
    last_queued_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_failed_pushes_last_queued
    ON failed_pushes(last_queued_at);
CREATE TABLE IF NOT EXISTS attempt_counters (
    external_id TEXT PRIMARY KEY,
    total_attempts INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass(frozen=True)
class QueuedItem:
    """One row in the failed-push queue."""
    id: int
    item: Item
    reason: Optional[str]
    attempts: int


class FailedQueue:
    def __init__(self, db_path: Path | str):
        self._path = str(db_path)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)

    def enqueue(self, item: Item, reason: Optional[str] = None) -> int:
        """Add or re-queue an item. Returns the row id.
        If external_id already exists, increments attempts and updates reason.
        Attempts counter persists across mark_pushed/delete cycles so repeated
        failures of the same item accumulate."""
        now = time.time()
        item_json = json.dumps(asdict(item), default=str)
        with self._conn() as c:
            # Bump the cumulative attempt counter first (persists across deletes).
            c.execute(
                "INSERT INTO attempt_counters (external_id, total_attempts) "
                "VALUES (?, 1) "
                "ON CONFLICT(external_id) DO UPDATE SET "
                "    total_attempts = total_attempts + 1",
                (item.external_id,),
            )
            total_row = c.execute(
                "SELECT total_attempts FROM attempt_counters WHERE external_id = ?",
                (item.external_id,),
            ).fetchone()
            total_attempts = total_row["total_attempts"]

            cur = c.execute(
                "UPDATE failed_pushes SET attempts = ?, "
                "reason = ?, last_queued_at = ?, item_json = ? "
                "WHERE external_id = ?",
                (total_attempts, reason, now, item_json, item.external_id),
            )
            if cur.rowcount == 0:
                cur = c.execute(
                    "INSERT INTO failed_pushes "
                    "(external_id, item_json, reason, attempts, first_queued_at, last_queued_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (item.external_id, item_json, reason, total_attempts, now, now),
                )
                return cur.lastrowid  # type: ignore[return-value]
            row = c.execute(
                "SELECT id FROM failed_pushes WHERE external_id = ?",
                (item.external_id,),
            ).fetchone()
            return row["id"]

    def peek(self, limit: int = 50) -> list[QueuedItem]:
        """Returns up to `limit` oldest queued items."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, item_json, reason, attempts FROM failed_pushes "
                "ORDER BY last_queued_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        out: list[QueuedItem] = []
        for row in rows:
            d = json.loads(row["item_json"])
            d["published_at"] = datetime.fromisoformat(d["published_at"])
            d["fetched_at"] = datetime.fromisoformat(d["fetched_at"])
            out.append(QueuedItem(
                id=row["id"], item=Item(**d),
                reason=row["reason"], attempts=row["attempts"],
            ))
        return out

    def mark_pushed(self, row_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM failed_pushes WHERE id = ?", (row_id,))
