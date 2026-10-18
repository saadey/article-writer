"""The pipeline steps as one service, shared by the web app and the command line."""

from __future__ import annotations

import json
import threading
from collections import Counter
from typing import Optional

from .articles import generate_article
from .config import Settings
from .db import Database, loads
from .gemini import GeminiClient, GeminiError, GenerationError
from .images import generate_image_prompts
from .logging_setup import get_logger
from .transcripts import (
    TranscriptBlockedError,
    TranscriptError,
    gemini_transcript,
    get_transcript,
    make_transcript_api,
)
from .youtube_search import VideoInfo, YouTubeClient, discover_videos

log = get_logger()

# Each Gemini step: the stage that must be finished first, and a label for logs.
GEN_STEPS = {
    "article": ("transcript", "Writing articles"),
    "images": ("article", "Writing image prompts"),
}


class Cancelled(Exception):
    """The user pressed Stop."""


class Pipeline:
    def __init__(self, db: Database, settings: Settings, cancel: Optional[threading.Event] = None):
        self.db = db
        self.settings = settings
        self.project_id = settings.project_id
        self.cancel = cancel or threading.Event()
        self.tally: Counter = Counter()
        self._gemini: Optional[GeminiClient] = None
        self._transcriber: Optional[GeminiClient] = None
        self._youtube_blocked = False  # once YouTube blocks us, stop asking it during this task

    # ---- helpers --------------------------------------------------------------

    def sleep(self, seconds: float) -> None:
        """Wait, but wake up immediately if the user presses Stop."""
        if self.cancel.wait(seconds):
            raise Cancelled()

    def check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

    @property
    def gemini(self) -> GeminiClient:
        if self._gemini is None:
            self._gemini = GeminiClient(self.settings.require_gemini_key(), self.settings.writing.models)
            self._gemini.sleep = self.sleep
        return self._gemini

    @property
    def transcriber(self) -> GeminiClient:
        """Gemini client for transcribing videos: the transcript model first, then the writing models."""
        if self._transcriber is None:
            models = [self.settings.transcripts.gemini_model] + self.settings.writing.models
            self._transcriber = GeminiClient(self.settings.require_gemini_key(), list(dict.fromkeys(models)))
            self._transcriber.sleep = self.sleep
        return self._transcriber

    def summary(self) -> str:
        labels = [
            ("found", "new videos found"), ("transcripts", "transcripts saved"),
            ("article", "articles written"), ("images", "image prompt sets written"),
            ("failed", "failed"),
        ]
        parts = [f"{self.tally[k]} {label}" for k, label in labels if self.tally[k]]
        if self.tally["stopped"]:
            parts.append("stopped early (see log)")
        return ", ".join(parts) or "Nothing to do"

    # ---- steps 1-2 -----------------------------------------------------------

    def discover(self, limit: Optional[int] = None) -> list:
        log.info("Project: %s | %d keyword(s)", self.settings.niche_name, len(self.settings.search.keywords))
        client = YouTubeClient(self.settings.require_youtube_key())
        videos = discover_videos(self.settings, client, self.db, limit)
        self.tally["found"] += len(videos)
        return videos

    def find(self, limit: Optional[int] = None) -> list:
        """Search only: save new videos with their stats for you to review. No transcripts, no Gemini."""
        videos = self.discover(limit)
        for video in videos:
            self.db.save_found(self.project_id, video.to_dict())
        if videos:
            log.info("Saved %d video(s) to the Found list. Pick the ones to write about there.", len(videos))
        return videos

    def process(self, row_ids: list) -> None:
        """Take each chosen video all the way: transcript, article, image prompts. One video at a
        time, so each finished one shows up as ready while the rest are still being written."""
        rows = [r for r in (self.db.video(i) for i in row_ids) if r and r["project_id"] == self.project_id]
        if not rows:
            log.info("No videos selected.")
            return
        api = None
        for index, row in enumerate(rows, 1):
            self.check_cancel()
            if index > 1 and self.settings.transcripts.delay_seconds:
                self.sleep(self.settings.transcripts.delay_seconds)
            log.info("=== Video %d of %d: %s", index, len(rows), row["title"])
            if row["stage"] == "published" or (row["stage"], row["status"]) == ("images", "done"):
                log.info("  already finished, skipping")
                continue
            if not row["transcript_text"]:
                api = api or make_transcript_api(self.settings.transcript_proxy_url)
                video = VideoInfo.from_dict(loads(row["metadata_json"], {}))
                outcome = self._fetch_one(api, video)
                if outcome == "stop":
                    return
                if outcome != "ok":
                    continue
            for stage in ("article", "images"):
                row = self.db.video(row["id"])
                if stage == "article" and row["article_body"] and row["stage"] != "transcript":
                    continue  # article already written
                if not self.generate(stage, [row]):
                    return
                if self.db.video(row["id"])["status"] == "failed":
                    break  # don't build image prompts on top of a failed article

    def fetch_transcripts(self, videos: list) -> None:
        if not videos:
            log.info("No new videos to process.")
            return
        log.info("Saving transcripts for %d video(s).", len(videos))
        api = make_transcript_api(self.settings.transcript_proxy_url)
        for index, video in enumerate(videos, 1):
            self.check_cancel()
            if index > 1 and self.settings.transcripts.delay_seconds:
                self.sleep(self.settings.transcripts.delay_seconds)
            log.info("[%d/%d] %s", index, len(videos), video.title)
            if self._fetch_one(api, video) == "stop":
                return

    def _fetch_one(self, api, video: VideoInfo) -> str:
        """Fetch and save one transcript. Returns 'ok', 'failed', or 'stop' (end the task)."""
        row_id = self.db.mark_pending(self.project_id, video.to_dict())
        try:
            text, segments, info = self._transcribe(api, video)
        except (TranscriptBlockedError, GeminiError) as exc:
            # Every other video would fail the same way: record it and stop.
            self.db.mark_failed(row_id, str(exc))
            self.tally["failed"] += 1
            self.tally["stopped"] += 1
            log.error("%s", exc)
            log.error("Stopping early. Remaining videos were not attempted.")
            return "stop"
        except (TranscriptError, GenerationError) as exc:
            self.db.mark_failed(row_id, str(exc))
            self.tally["failed"] += 1
            log.warning("  transcript failed: %s", exc)
            return "failed"
        except Cancelled:
            raise
        except Exception as exc:  # unexpected bug or network error: record it and keep going
            self.db.mark_failed(row_id, f"Unexpected error: {type(exc).__name__}: {exc}")
            self.tally["failed"] += 1
            log.exception("  unexpected error")
            return "failed"
        self.db.save_transcript(row_id, text, segments, info)
        self.tally["transcripts"] += 1
        log.info("  saved transcript (%d words, %s)", info["word_count"],
                 "from Gemini" if info.get("source") == "gemini" else "from YouTube captions")
        return "ok"

    def _transcribe(self, api, video: VideoInfo) -> tuple:
        """YouTube captions first (free, instant); Gemini watches the video when that isn't possible."""
        cfg = self.settings.transcripts
        if cfg.source != "gemini" and not self._youtube_blocked:
            try:
                return get_transcript(api, video.video_id, cfg)
            except TranscriptBlockedError:
                if cfg.source == "youtube" or not self.settings.gemini_api_key:
                    raise
                self._youtube_blocked = True
                log.warning("  YouTube is blocking caption requests from your connection. Using Gemini to "
                            "transcribe instead (for the rest of this task too).")
            except TranscriptError as exc:
                if cfg.source == "youtube" or not self.settings.gemini_api_key or "too short" in str(exc):
                    raise
                log.info("  no usable YouTube captions (%s) Transcribing with Gemini instead.", exc)
        log.info("  transcribing with Gemini from the video link...")
        return gemini_transcript(self.transcriber, video.video_id, cfg)

    # ---- steps 3-4 -------------------------------------------------------------

    def generate(self, stage: str, rows: list) -> bool:
        """Run one Gemini step over rows. Returns False if the run had to stop (quota, bad key)."""
        previous, label = GEN_STEPS[stage]
        log.info("%s for %d video(s).", label, len(rows))
        for index, row in enumerate(rows, 1):
            self.check_cancel()
            if index > 1 and self.settings.writing.delay_seconds:
                self.sleep(self.settings.writing.delay_seconds)
            log.info("[%d/%d] %s", index, len(rows), row["article_title"] or row["title"])
            self.db.start_stage(row["id"], stage)
            try:
                if stage == "article":
                    article = generate_article(
                        self.gemini, self.settings.writing, self.settings.niche_name,
                        loads(row["metadata_json"], {}), row["transcript_text"],
                    )
                    self.db.save_article(row["id"], article)
                    log.info("  wrote \"%s\" (%d words)", article["title"], article["word_count"])
                else:
                    prompts, model = generate_image_prompts(
                        self.gemini, self.settings.images, self.settings.niche_name,
                        row["article_title"], row["article_subtitle"], row["article_body"],
                    )
                    self.db.save_image_prompts(row["id"], prompts, model)
                    log.info("  wrote %d image prompts", len(prompts))
                self.tally[stage] += 1
            except Cancelled:
                self.db.set_stage_done(row["id"], previous)  # leave it queued
                raise
            except GeminiError as exc:
                # Not this video's fault (quota, bad key): leave it queued for the next run.
                self.db.set_stage_done(row["id"], previous)
                self.tally["stopped"] += 1
                log.error("%s", exc)
                log.error("Stopping early. %d video(s) left for next time.", len(rows) - index + 1)
                return False
            except GenerationError as exc:
                self.db.mark_failed(row["id"], str(exc))
                self.tally["failed"] += 1
                log.warning("  failed: %s", exc)
            except Exception as exc:
                self.db.mark_failed(row["id"], f"Unexpected error: {type(exc).__name__}: {exc}")
                self.tally["failed"] += 1
                log.exception("  unexpected error")
        return True

    def write_pending(self, stages=("article", "images"), limit: Optional[int] = None) -> None:
        """Run the Gemini steps for every video waiting for them."""
        limit = limit or self.settings.writing.max_articles_per_run
        for stage in stages:
            rows = self.db.ready_for_next(self.project_id, GEN_STEPS[stage][0], limit=limit)
            if not rows:
                log.info("%s: nothing waiting.", GEN_STEPS[stage][1])
                continue
            if not self.generate(stage, rows):
                return

    def regenerate(self, row_id: int, stages=("article", "images")) -> None:
        """(Re)write the article and/or image prompts for one video."""
        for stage in stages:
            row = self.db.video(row_id)
            if not self.generate(stage, [row]):
                return
            if self.db.video(row_id)["status"] == "failed":
                return  # don't build image prompts on top of a failed article

    # ---- whole runs -----------------------------------------------------------------

    def run_all(self, write: bool = True) -> None:
        if write:
            self.settings.require_gemini_key()  # fail now, not after downloading transcripts
        self.fetch_transcripts(self.discover())
        if write:
            self.write_pending()

    def retry(self, limit: Optional[int] = None) -> None:
        rows = {s: self.db.retryable(self.project_id, s, limit) for s in ("transcript", "article", "images")}
        if not any(rows.values()):
            log.info("Nothing to retry: no failed or interrupted videos.")
            return
        if rows["transcript"]:
            log.info("Retrying %d transcript(s).", len(rows["transcript"]))
            videos = [VideoInfo.from_dict(json.loads(r["metadata_json"])) for r in rows["transcript"]]
            self.fetch_transcripts(videos)
        for stage in ("article", "images"):
            if rows[stage]:
                log.info("Retrying %d %s step(s).", len(rows[stage]), stage)
                if not self.generate(stage, rows[stage]):
                    return

    def check_keys(self) -> None:
        """Verify both API keys with cheap requests."""
        YouTubeClient(self.settings.require_youtube_key()).video_details(["dQw4w9WgXcQ"])
        log.info("YouTube API key OK.")
        for model in self.settings.writing.models:
            self.gemini.check_model(model)
        log.info("Gemini API key OK (models: %s).", ", ".join(self.settings.writing.models))

