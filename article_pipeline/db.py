"""SQLite tracking of processed videos.

Each video has one row. `stage` is the last pipeline step attempted
(only "transcript" for now; later steps add "article", "images", ...)
and `status` is the result of that step: pending, done, or failed.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id      TEXT PRIMARY KEY,
    title         TEXT,
    channel_title TEXT,
    keyword       TEXT,
    url           TEXT,
    published_at  TEXT,
    view_count    INTEGER,
    duration_sec  INTEGER,
    metadata_json TEXT,
    stage         TEXT NOT NULL DEFAULT 'transcript',
    status        TEXT NOT NULL CHECK (status IN ('pending', 'done', 'failed')),
    error         TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    draft_dir     TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos (stage, status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- reads -------------------------------------------------------

    def statuses(self, video_ids: Iterable[str]) -> dict:
        """Map video_id -> status for the IDs that are already tracked."""
        ids = list(video_ids)
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT video_id, status FROM videos WHERE video_id IN ({placeholders})", ids
        ).fetchall()
        return {r["video_id"]: r["status"] for r in rows}

    def retryable(self, stage: str = "transcript", limit: Optional[int] = None) -> list:
        """Failed videos, plus 'pending' ones left behind by an interrupted run."""
        sql = (
            "SELECT * FROM videos WHERE stage = ? AND status IN ('failed', 'pending') "
            "ORDER BY updated_at"
        )
        params: list = [stage]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def counts(self) -> dict:
        rows = self.conn.execute(
            "SELECT stage, status, COUNT(*) AS n FROM videos GROUP BY stage, status"
        ).fetchall()
        return {(r["stage"], r["status"]): r["n"] for r in rows}

    def recent_failures(self, limit: int = 10) -> list:
        return self.conn.execute(
            "SELECT video_id, title, stage, error, attempts, updated_at FROM videos "
            "WHERE status = 'failed' ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    # ---- writes ------------------------------------------------------

    def mark_pending(self, video: dict, stage: str = "transcript") -> None:
        """Insert or refresh a video before work starts and count the attempt."""
        now = _now()
        self.conn.execute(
            """
            INSERT INTO videos (video_id, title, channel_title, keyword, url, published_at,
                                view_count, duration_sec, metadata_json, stage, status,
                                attempts, created_at, updated_at)
            VALUES (:video_id, :title, :channel_title, :keyword, :url, :published_at,
                    :view_count, :duration_sec, :metadata_json, :stage, 'pending', 1, :now, :now)
            ON CONFLICT(video_id) DO UPDATE SET
                title = excluded.title,
                view_count = excluded.view_count,
                metadata_json = excluded.metadata_json,
                stage = excluded.stage,
                status = 'pending',
                error = NULL,
                attempts = videos.attempts + 1,
                updated_at = excluded.updated_at
            """,
            {
                "video_id": video["video_id"],
                "title": video.get("title"),
                "channel_title": video.get("channel_title"),
                "keyword": video.get("keyword"),
                "url": video.get("url"),
                "published_at": video.get("published_at"),
                "view_count": video.get("view_count"),
                "duration_sec": video.get("duration_seconds"),
                "metadata_json": json.dumps(video, ensure_ascii=False),
                "stage": stage,
                "now": now,
            },
        )
        self.conn.commit()

    def mark_done(self, video_id: str, draft_dir: Path) -> None:
        self.conn.execute(
            "UPDATE videos SET status = 'done', error = NULL, draft_dir = ?, updated_at = ? "
            "WHERE video_id = ?",
            (str(draft_dir), _now(), video_id),
        )
        self.conn.commit()

    def mark_failed(self, video_id: str, error: str) -> None:
        self.conn.execute(
            "UPDATE videos SET status = 'failed', error = ?, updated_at = ? WHERE video_id = ?",
            (error[:1000], _now(), video_id),
        )
        self.conn.commit()
