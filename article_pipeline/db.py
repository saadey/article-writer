"""SQLite storage: projects, videos (with transcripts, articles and image prompts), and jobs.

Each video row belongs to one project. `stage` is where it is in the pipeline:

    found -> transcript -> article -> images -> published
      \-> dismissed

"found" videos were discovered by a search and wait for you to pick them;
"dismissed" ones you chose to skip (they are not found again). For the other
stages, `status` is the result of that step: pending, done, or failed.
A video whose images stage is done is ready to publish.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

STAGES = ("found", "transcript", "article", "images", "published", "dismissed")
REVIEW_STAGES = ("found", "dismissed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT NOT NULL UNIQUE,
    settings_json        TEXT NOT NULL,
    article_instructions TEXT NOT NULL DEFAULT '',
    image_instructions   TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id           INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    video_id             TEXT NOT NULL,
    title                TEXT,
    channel_title        TEXT,
    keyword              TEXT,
    url                  TEXT,
    published_at         TEXT,
    view_count           INTEGER,
    duration_sec         INTEGER,
    like_count           INTEGER,
    comment_count        INTEGER,
    channel_id           TEXT,
    subscriber_count     INTEGER,
    metadata_json        TEXT,
    stage                TEXT NOT NULL DEFAULT 'transcript',
    status               TEXT NOT NULL CHECK (status IN ('pending', 'done', 'failed')),
    error                TEXT,
    attempts             INTEGER NOT NULL DEFAULT 0,
    transcript_text      TEXT,
    transcript_json      TEXT,
    transcript_info_json TEXT,
    article_title        TEXT,
    article_subtitle     TEXT,
    article_body         TEXT,
    title_options_json   TEXT,
    editor_notes         TEXT,
    flagged_claims_json  TEXT,
    article_words        INTEGER,
    article_model        TEXT,
    article_at           TEXT,
    image_prompts_json   TEXT,
    images_model         TEXT,
    images_at            TEXT,
    posted_at            TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    UNIQUE (project_id, video_id)
);
CREATE INDEX IF NOT EXISTS idx_videos_stage ON videos (project_id, stage, status);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL,
    summary     TEXT,
    log         TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")  # lets the app read while a job writes
        self.conn.executescript(SCHEMA)
        self._add_missing_columns()
        self.conn.commit()

    def _add_missing_columns(self) -> None:
        """Upgrade databases created by older versions of the app."""
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(videos)")}
        for name, kind in (("like_count", "INTEGER"), ("comment_count", "INTEGER"),
                           ("channel_id", "TEXT"), ("subscriber_count", "INTEGER")):
            if name not in have:
                self.conn.execute(f"ALTER TABLE videos ADD COLUMN {name} {kind}")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _write(self, sql: str, params=()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    # ---- projects ------------------------------------------------------------

    def projects(self) -> list:
        return self.conn.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE").fetchall()

    def project(self, project_id: int):
        return self.conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()

    def project_by_name(self, name: str):
        return self.conn.execute(
            "SELECT * FROM projects WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()

    def create_project(self, name: str, settings: dict, article_prompt: str, image_prompt: str) -> int:
        ts = now()
        cur = self._write(
            "INSERT INTO projects (name, settings_json, article_instructions, image_instructions, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (name, json.dumps(settings), article_prompt, image_prompt, ts, ts),
        )
        return cur.lastrowid

    def update_project(self, project_id: int, **fields) -> None:
        allowed = {"name", "settings_json", "article_instructions", "image_instructions"}
        if "settings" in fields:
            fields["settings_json"] = json.dumps(fields.pop("settings"))
        cols = {k: v for k, v in fields.items() if k in allowed}
        if not cols:
            return
        sets = ", ".join(f"{k} = ?" for k in cols)
        self._write(f"UPDATE projects SET {sets}, updated_at = ? WHERE id = ?",
                    (*cols.values(), now(), project_id))

    def delete_project(self, project_id: int) -> None:
        self._write("DELETE FROM projects WHERE id = ?", (project_id,))

    # ---- videos: reads ----------------------------------------------------------

    def video(self, row_id: int):
        return self.conn.execute("SELECT * FROM videos WHERE id = ?", (row_id,)).fetchone()

    def video_by_youtube_id(self, project_id: int, video_id: str):
        return self.conn.execute(
            "SELECT * FROM videos WHERE project_id = ? AND video_id = ?", (project_id, video_id)
        ).fetchone()

    def tracked(self, project_id: int, video_ids: Iterable[str]) -> dict:
        """Map video_id -> (stage, status) for IDs this project already tracks."""
        ids = list(video_ids)
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT video_id, stage, status FROM videos WHERE project_id = ? AND video_id IN ({marks})",
            (project_id, *ids),
        ).fetchall()
        return {r["video_id"]: (r["stage"], r["status"]) for r in rows}

    def ready_for_next(self, project_id: int, finished_stage: str, limit: Optional[int] = None) -> list:
        """Videos whose `finished_stage` is done, i.e. waiting for the next step."""
        sql = ("SELECT * FROM videos WHERE project_id = ? AND stage = ? AND status = 'done' "
               "ORDER BY view_count DESC")
        params: list = [project_id, finished_stage]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def retryable(self, project_id: int, stage: str, limit: Optional[int] = None) -> list:
        """Failed videos, plus 'pending' ones left behind by an interrupted run."""
        sql = ("SELECT * FROM videos WHERE project_id = ? AND stage = ? "
               "AND status IN ('failed', 'pending') ORDER BY updated_at")
        params: list = [project_id, stage]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def videos(self, project_id: int, view: str = "all") -> list:
        """Videos for the drafts list. view: ready | progress | published | failed | all.
        Found and dismissed videos are not drafts yet; they're listed by found_videos()."""
        where = {
            "ready": "stage = 'images' AND status = 'done'",
            "published": "stage = 'published'",
            "failed": "status = 'failed'",
            "progress": "status != 'failed' AND stage NOT IN ('published', 'found', 'dismissed') "
                        "AND NOT (stage = 'images' AND status = 'done')",
        }.get(view, "stage NOT IN ('found', 'dismissed')")
        return self.conn.execute(
            f"SELECT * FROM videos WHERE project_id = ? AND {where} ORDER BY updated_at DESC",
            (project_id,),
        ).fetchall()

    def counts(self, project_id: int) -> dict:
        rows = self.conn.execute(
            "SELECT stage, status, COUNT(*) AS n FROM videos WHERE project_id = ? GROUP BY stage, status",
            (project_id,),
        ).fetchall()
        return {(r["stage"], r["status"]): r["n"] for r in rows}

    FOUND_SORTS = {
        "views": "view_count DESC",
        "likes": "like_count DESC",
        "ratio": "CAST(like_count AS REAL) / MAX(view_count, 1) DESC",
        "comments": "comment_count DESC",
        "subscribers": "subscriber_count DESC",
        "newest": "published_at DESC",
        "length": "duration_sec DESC",
        "found": "created_at DESC, view_count DESC",
    }

    def found_videos(self, project_id: int, dismissed: bool = False, sort: str = "views") -> list:
        order = self.FOUND_SORTS.get(sort, self.FOUND_SORTS["views"])
        return self.conn.execute(
            f"SELECT * FROM videos WHERE project_id = ? AND stage = ? ORDER BY {order}",
            (project_id, "dismissed" if dismissed else "found"),
        ).fetchall()

    def summary_counts(self, project_id: int) -> dict:
        c = self.counts(project_id)
        found = c.get(("found", "done"), 0)
        dismissed = c.get(("dismissed", "done"), 0)
        ready = c.get(("images", "done"), 0)
        published = c.get(("published", "done"), 0)
        failed = sum(n for (_, status), n in c.items() if status == "failed")
        drafts = sum(c.values()) - found - dismissed
        return {"found": found, "dismissed": dismissed, "ready": ready, "published": published,
                "failed": failed, "progress": drafts - ready - published - failed}

    def recent_failures(self, project_id: int, limit: int = 10) -> list:
        return self.conn.execute(
            "SELECT * FROM videos WHERE project_id = ? AND status = 'failed' "
            "ORDER BY updated_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()

    # ---- videos: writes ---------------------------------------------------------

    def mark_pending(self, project_id: int, video: dict) -> int:
        """Insert or refresh a video before its transcript is fetched. Returns the row id."""
        ts = now()
        self._write(
            """
            INSERT INTO videos (project_id, video_id, title, channel_title, keyword, url, published_at,
                                view_count, duration_sec, metadata_json, stage, status, attempts,
                                created_at, updated_at)
            VALUES (:project_id, :video_id, :title, :channel_title, :keyword, :url, :published_at,
                    :view_count, :duration_sec, :metadata_json, 'transcript', 'pending', 1, :ts, :ts)
            ON CONFLICT(project_id, video_id) DO UPDATE SET
                title = excluded.title,
                view_count = excluded.view_count,
                metadata_json = excluded.metadata_json,
                stage = 'transcript',
                status = 'pending',
                error = NULL,
                attempts = CASE WHEN videos.stage = 'transcript' THEN videos.attempts + 1 ELSE 1 END,
                updated_at = excluded.updated_at
            """,
            {
                "project_id": project_id,
                "video_id": video["video_id"],
                "title": video.get("title"),
                "channel_title": video.get("channel_title"),
                "keyword": video.get("keyword"),
                "url": video.get("url"),
                "published_at": video.get("published_at"),
                "view_count": video.get("view_count"),
                "duration_sec": video.get("duration_seconds"),
                "metadata_json": json.dumps(video, ensure_ascii=False),
                "ts": ts,
            },
        )
        return self.video_by_youtube_id(project_id, video["video_id"])["id"]

    def save_found(self, project_id: int, video: dict) -> None:
        """Store a search result for you to review. Nothing is fetched for it yet."""
        ts = now()
        self._write(
            """
            INSERT INTO videos (project_id, video_id, title, channel_title, keyword, url, published_at,
                                view_count, duration_sec, like_count, comment_count, channel_id,
                                subscriber_count, metadata_json, stage, status, attempts,
                                created_at, updated_at)
            VALUES (:project_id, :video_id, :title, :channel_title, :keyword, :url, :published_at,
                    :view_count, :duration_sec, :like_count, :comment_count, :channel_id,
                    :subscriber_count, :metadata_json, 'found', 'done', 0, :ts, :ts)
            ON CONFLICT(project_id, video_id) DO NOTHING
            """,
            {
                "project_id": project_id, "video_id": video["video_id"], "title": video.get("title"),
                "channel_title": video.get("channel_title"), "keyword": video.get("keyword"),
                "url": video.get("url"), "published_at": video.get("published_at"),
                "view_count": video.get("view_count"), "duration_sec": video.get("duration_seconds"),
                "like_count": video.get("like_count"), "comment_count": video.get("comment_count"),
                "channel_id": video.get("channel_id"), "subscriber_count": video.get("channel_subscribers"),
                "metadata_json": json.dumps(video, ensure_ascii=False), "ts": ts,
            },
        )

    def set_dismissed(self, project_id: int, row_ids: list, dismissed: bool) -> int:
        """Dismiss found videos (or restore dismissed ones). Returns how many changed."""
        if not row_ids:
            return 0
        marks = ",".join("?" * len(row_ids))
        cur = self._write(
            f"UPDATE videos SET stage = ?, updated_at = ? WHERE project_id = ? AND stage = ? AND id IN ({marks})",
            ("dismissed" if dismissed else "found", now(), project_id,
             "found" if dismissed else "dismissed", *row_ids),
        )
        return cur.rowcount

    def start_stage(self, row_id: int, stage: str) -> None:
        """Move a video to `stage` as pending. Attempts count per stage."""
        self._write(
            "UPDATE videos SET attempts = CASE WHEN stage = ? THEN attempts + 1 ELSE 1 END, "
            "stage = ?, status = 'pending', error = NULL, updated_at = ? WHERE id = ?",
            (stage, stage, now(), row_id),
        )

    def set_stage_done(self, row_id: int, stage: str) -> None:
        """Put a video back at `stage` = done, e.g. when a run stops for reasons unrelated
        to this video (quota, bad key) so it stays queued for the next run."""
        self._write(
            "UPDATE videos SET stage = ?, status = 'done', error = NULL, updated_at = ? WHERE id = ?",
            (stage, now(), row_id),
        )

    def mark_failed(self, row_id: int, error: str) -> None:
        self._write(
            "UPDATE videos SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
            (error[:1000], now(), row_id),
        )

    def save_transcript(self, row_id: int, text: str, segments: list, info: dict) -> None:
        self._write(
            "UPDATE videos SET transcript_text = ?, transcript_json = ?, transcript_info_json = ?, "
            "status = 'done', error = NULL, updated_at = ? WHERE id = ?",
            (text, json.dumps(segments, ensure_ascii=False), json.dumps(info), now(), row_id),
        )

    def save_article(self, row_id: int, article: dict) -> None:
        ts = now()
        self._write(
            """
            UPDATE videos SET article_title = ?, article_subtitle = ?, article_body = ?,
                title_options_json = ?, editor_notes = ?, flagged_claims_json = ?, article_words = ?,
                article_model = ?, article_at = ?, status = 'done', error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (
                article["title"], article["subtitle"], article["body"],
                json.dumps(article["title_options"], ensure_ascii=False), article["editor_notes"],
                json.dumps(article["flagged_claims"], ensure_ascii=False), article["word_count"],
                article["model"], ts, ts, row_id,
            ),
        )

    def save_image_prompts(self, row_id: int, prompts: list, model: str) -> None:
        ts = now()
        self._write(
            "UPDATE videos SET image_prompts_json = ?, images_model = ?, images_at = ?, "
            "status = 'done', error = NULL, updated_at = ? WHERE id = ?",
            (json.dumps(prompts, ensure_ascii=False), model, ts, ts, row_id),
        )

    def edit_article(self, row_id: int, title: str, subtitle: str, body: str) -> None:
        """Manual edits from the app."""
        self._write(
            "UPDATE videos SET article_title = ?, article_subtitle = ?, article_body = ?, "
            "article_words = ?, updated_at = ? WHERE id = ?",
            (title, subtitle, body, len(body.split()), now(), row_id),
        )

    def set_posted(self, row_id: int, posted: bool) -> None:
        """Mark a ready draft as published (or move it back to ready)."""
        self._write(
            "UPDATE videos SET stage = ?, status = 'done', posted_at = ?, updated_at = ? WHERE id = ?",
            ("published" if posted else "images", now() if posted else None, now(), row_id),
        )

    def delete_video(self, row_id: int) -> None:
        self._write("DELETE FROM videos WHERE id = ?", (row_id,))

    # ---- jobs --------------------------------------------------------------------

    def create_job(self, project_id: Optional[int], kind: str) -> int:
        return self._write(
            "INSERT INTO jobs (project_id, kind, status, started_at) VALUES (?, ?, 'running', ?)",
            (project_id, kind, now()),
        ).lastrowid

    def finish_job(self, job_id: int, status: str, summary: str, log: str) -> None:
        self._write(
            "UPDATE jobs SET status = ?, summary = ?, log = ?, finished_at = ? WHERE id = ?",
            (status, summary, log, now(), job_id),
        )

    def job(self, job_id: int):
        return self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def jobs(self, project_id: int, limit: int = 15) -> list:
        return self.conn.execute(
            "SELECT * FROM jobs WHERE project_id = ? ORDER BY id DESC LIMIT ?", (project_id, limit)
        ).fetchall()

    def mark_orphaned_jobs(self) -> None:
        """Jobs still 'running' at startup were cut off when the app last stopped."""
        self._write("UPDATE jobs SET status = 'interrupted', finished_at = ? WHERE status = 'running'",
                    (now(),))


def loads(value, default=None):
    """json.loads that tolerates NULL columns."""
    if not value:
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default
