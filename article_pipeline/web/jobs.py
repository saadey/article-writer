"""Runs one pipeline task at a time in a background thread, with a live log the pages can poll."""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime
from typing import Callable, Optional

from ..config import DB_PATH, ConfigError
from ..db import Database
from ..gemini import GeminiError
from ..logging_setup import get_logger
from ..pipeline import Cancelled, Pipeline
from ..projects import settings_for
from ..youtube_search import YouTubeAPIError

log = get_logger()

JOB_LABELS = {
    "find": "Find videos",
    "process": "Write selected videos",
    "run": "Run everything",
    "transcripts": "Find videos + save transcripts",
    "preview": "Preview search",
    "write": "Write articles + image prompts",
    "images": "Write image prompts",
    "retry": "Retry failed",
    "regenerate": "Rewrite article + images",
    "regenerate_images": "Rewrite image prompts",
    "check": "Check API keys",
}


class _JobLogHandler(logging.Handler):
    def __init__(self, lines: deque):
        super().__init__(logging.INFO)
        self.lines = lines
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append({"level": record.levelname.lower(), "text": self.format(record)})


class JobRunner:
    def __init__(self):
        self._lock = threading.Lock()
        self.current: Optional[dict] = None

    def busy(self) -> bool:
        return bool(self.current and self.current["status"] == "running")

    def start(self, project_id: Optional[int], kind: str, task: Callable[[Pipeline], None],
              video_ids: Optional[list] = None) -> int:
        """Start `task(pipeline)` in the background. Raises RuntimeError if a job is already running."""
        with self._lock:
            if self.busy():
                raise RuntimeError("Another task is still running. Wait for it to finish or press Stop.")
            with Database(DB_PATH) as db:
                job_id = db.create_job(project_id, kind)
            self.current = {
                "id": job_id, "project_id": project_id, "kind": kind,
                "label": JOB_LABELS.get(kind, kind), "status": "running", "summary": "",
                "lines": deque(maxlen=3000), "cancel": threading.Event(),
                "started": datetime.now().strftime("%H:%M:%S"),
                "video_ids": list(video_ids or []),
            }
            job = self.current
        threading.Thread(target=self._run, args=(job, task), daemon=True).start()
        return job_id

    def cancel(self) -> None:
        if self.busy():
            self.current["cancel"].set()
            log.warning("Stopping after the current step...")

    def snapshot(self, since: int = 0) -> Optional[dict]:
        job = self.current
        if not job:
            return None
        lines = list(job["lines"])
        return {k: job[k] for k in ("id", "project_id", "kind", "label", "status", "summary", "started",
                                    "video_ids")} | {
            "lines": lines[since:], "total_lines": len(lines),
            "stopping": job["cancel"].is_set() and job["status"] == "running",
        }

    def _run(self, job: dict, task) -> None:
        handler = _JobLogHandler(job["lines"])
        log.addHandler(handler)
        status, summary = "done", ""
        db = Database(DB_PATH)
        try:
            project = db.project(job["project_id"]) if job["project_id"] else None
            if job["project_id"] and project is None:
                raise ConfigError("This project no longer exists.")
            pipeline = Pipeline(db, settings_for(project), cancel=job["cancel"])
            task(pipeline)
            summary = pipeline.summary()
        except Cancelled:
            status, summary = "cancelled", "Stopped. Unfinished videos stay queued."
            log.warning(summary)
        except (ConfigError, YouTubeAPIError, GeminiError) as exc:
            status, summary = "failed", str(exc)
            log.error("%s", exc)
        except Exception as exc:  # show unexpected errors instead of dying silently
            status, summary = "failed", f"Unexpected error: {type(exc).__name__}: {exc}"
            log.exception("Unexpected error")
        finally:
            log.removeHandler(handler)
            text = "\n".join(line["text"] for line in job["lines"])
            try:
                db.finish_job(job["id"], status, summary, text)
            finally:
                db.close()
            job["status"], job["summary"] = status, summary
            log.info("Task finished: %s (%s)", status, summary)


runner = JobRunner()
