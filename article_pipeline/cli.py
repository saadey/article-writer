"""Command-line entry point: python main.py <command>."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .articles import ArticleError, GeminiClient, GeminiError, write_article
from .config import ConfigError, Settings, load_settings
from .db import Database
from .logging_setup import get_logger, setup_logging
from .transcripts import (
    TranscriptBlockedError,
    TranscriptError,
    draft_folder,
    make_transcript_api,
    save_transcript_draft,
)
from .youtube_search import VideoInfo, YouTubeAPIError, YouTubeClient, discover_videos

log = get_logger()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="Turn YouTube videos into article drafts.",
    )
    parser.add_argument("--config", type=Path, default=None,
                        help="Path to a config file (default: config.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show debug detail (e.g. why each video was filtered out)")
    sub = parser.add_subparsers(dest="command", metavar="command")

    run = sub.add_parser("run", help="Find new videos, save transcripts, and write articles")
    run.add_argument("--dry-run", action="store_true",
                     help="Only list the videos that would be processed; save nothing")
    run.add_argument("--no-write", action="store_true",
                     help="Stop after saving transcripts (skip Gemini article writing)")

    write = sub.add_parser("write", help="Write articles for saved transcripts that don't have one yet")
    write.add_argument("--limit", type=int, default=None,
                       help="Write at most this many (default: writing.max_articles_per_run)")
    write.add_argument("--video", metavar="VIDEO_ID", default=None,
                       help="(Re)write the article for this one video, e.g. to test new instructions")

    retry = sub.add_parser("retry", help="Retry videos that failed (or were interrupted) earlier")
    retry.add_argument("--limit", type=int, default=None, help="Retry at most this many videos per step")

    sub.add_parser("status", help="Show how many videos are at each step, and recent errors")
    sub.add_parser("check", help="Check config, .env and your API keys")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"Config problem: {exc}", file=sys.stderr)
        return 2

    setup_logging(settings.log_dir, verbose=args.verbose)
    log.debug("Using config %s", settings.config_path)

    try:
        with Database(settings.database_path) as db:
            if args.command == "run":
                return cmd_run(settings, db, args.dry_run, args.no_write)
            if args.command == "write":
                return cmd_write(settings, db, args.limit, args.video)
            if args.command == "retry":
                return cmd_retry(settings, db, args.limit)
            if args.command == "status":
                return cmd_status(db)
            if args.command == "check":
                return cmd_check(settings)
    except ConfigError as exc:
        log.error("Config problem: %s", exc)
        return 2
    except (YouTubeAPIError, GeminiError) as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.warning("Interrupted. Unfinished videos stay 'pending'; run 'retry' to pick them up.")
        return 130
    return 0


def cmd_run(settings: Settings, db: Database, dry_run: bool, no_write: bool) -> int:
    log.info("Niche: %s | %d keyword(s)", settings.niche_name, len(settings.search.keywords))
    if not (dry_run or no_write):
        settings.require_gemini_key()  # fail now, not after downloading transcripts
    client = YouTubeClient(settings.require_youtube_key())
    videos = discover_videos(settings, client, db)

    if dry_run:
        for v in videos:
            log.info("  would process %s | %s views | %d min | %s",
                     v.video_id, f"{v.view_count:,}", v.duration_seconds // 60, v.title)
        log.info("Dry run: nothing was saved.")
        return 0
    process_transcripts(settings, db, videos)
    if no_write:
        return 0
    return cmd_write(settings, db, None)


def cmd_write(settings: Settings, db: Database, limit, video_id=None) -> int:
    if video_id:
        row = db.get(video_id)
        if row is None:
            log.error("Video %s is not in the database. Check the ID with 'python main.py status'.", video_id)
            return 1
        if (row["stage"], row["status"]) in (("transcript", "failed"), ("transcript", "pending")):
            log.error("Video %s has no saved transcript yet; run 'python main.py retry' first.", video_id)
            return 1
        return process_articles(settings, db, [row])
    limit = limit or settings.writing.max_articles_per_run
    rows = db.ready_for_next("transcript", limit=limit)
    if not rows:
        log.info("No transcripts waiting for an article.")
        return 0
    return process_articles(settings, db, rows)


def cmd_retry(settings: Settings, db: Database, limit) -> int:
    transcript_rows = db.retryable(stage="transcript", limit=limit)
    article_rows = db.retryable(stage="article", limit=limit)
    if not transcript_rows and not article_rows:
        log.info("Nothing to retry: no failed or pending videos.")
        return 0
    if transcript_rows:
        log.info("Retrying %d transcript(s).", len(transcript_rows))
        videos = [VideoInfo.from_dict(json.loads(row["metadata_json"])) for row in transcript_rows]
        process_transcripts(settings, db, videos)
    if article_rows:
        log.info("Retrying %d article(s).", len(article_rows))
        process_articles(settings, db, article_rows)
    return 0


def process_transcripts(settings: Settings, db: Database, videos: list) -> int:
    if not videos:
        log.info("No new videos to process.")
        return 0

    log.info("Step 2: saving transcripts for %d video(s).", len(videos))
    api = make_transcript_api(settings.transcript_proxy_url)
    done = failed = 0
    for index, video in enumerate(videos, 1):
        if index > 1 and settings.transcripts.delay_seconds:
            time.sleep(settings.transcripts.delay_seconds)
        log.info("[%d/%d] %s | %s", index, len(videos), video.video_id, video.title)
        db.mark_pending(video.to_dict())
        try:
            folder = save_transcript_draft(api, video, settings.transcripts, settings.drafts_dir)
        except TranscriptBlockedError as exc:
            db.mark_failed(video.video_id, str(exc))
            failed += 1
            log.error("%s", exc)
            log.error("Stopping this run early. Remaining videos were not attempted.")
            break
        except TranscriptError as exc:
            db.mark_failed(video.video_id, str(exc))
            failed += 1
            log.warning("  failed: %s", exc)
        except Exception as exc:  # unexpected bug or network error: record it and keep going
            db.mark_failed(video.video_id, f"Unexpected error: {type(exc).__name__}: {exc}")
            failed += 1
            log.exception("  unexpected error")
        else:
            db.mark_done(video.video_id, folder)
            done += 1
            log.info("  saved %s", _relative(folder))

    log.info("Transcripts: %d saved, %d failed.", done, failed)
    if failed:
        log.info("See failures with 'python main.py status'; retry them with 'python main.py retry'.")
    return 0


def process_articles(settings: Settings, db: Database, rows: list) -> int:
    cfg = settings.writing
    client = GeminiClient(settings.require_gemini_key(), cfg.models)
    log.info("Step 3: writing %d article(s) with %s.", len(rows), cfg.model)
    done = failed = 0
    for index, row in enumerate(rows, 1):
        if index > 1 and cfg.delay_seconds:
            time.sleep(cfg.delay_seconds)
        video_id = row["video_id"]
        folder = Path(row["draft_dir"]) if row["draft_dir"] else draft_folder(
            settings.drafts_dir, VideoInfo.from_dict(json.loads(row["metadata_json"]))
        )
        log.info("[%d/%d] %s | %s", index, len(rows), video_id, row["title"])
        db.start_stage(video_id, "article")
        try:
            article = write_article(client, folder, cfg, settings.niche_name)
        except GeminiError as exc:
            # Not this video's fault (quota, bad key): leave it queued for the next run.
            db.revert_to(video_id, "transcript")
            log.error("%s", exc)
            log.error("Stopping this run early. %d article(s) left for next time.", len(rows) - index + 1)
            break
        except ArticleError as exc:
            db.mark_failed(video_id, str(exc))
            failed += 1
            log.warning("  failed: %s", exc)
        except Exception as exc:
            db.mark_failed(video_id, f"Unexpected error: {type(exc).__name__}: {exc}")
            failed += 1
            log.exception("  unexpected error")
        else:
            db.mark_done(video_id, folder)
            done += 1
            log.info("  wrote %s", _relative(article))

    log.info("Articles: %d written, %d failed. Drafts are in %s", done, failed, _relative(settings.drafts_dir))
    if failed:
        log.info("See failures with 'python main.py status'; retry them with 'python main.py retry'.")
    return 0


STAGE_LABELS = {
    ("transcript", "done"): "transcript saved, waiting for article",
    ("transcript", "failed"): "transcript failed",
    ("transcript", "pending"): "transcript interrupted",
    ("article", "done"): "article written",
    ("article", "failed"): "article failed",
    ("article", "pending"): "article interrupted",
}


def cmd_status(db: Database) -> int:
    counts = db.counts()
    if not counts:
        print("No videos tracked yet. Run: python main.py run")
        return 0
    print("Videos:")
    for (stage, status), n in sorted(counts.items()):
        print(f"  {n:>4}  {STAGE_LABELS.get((stage, status), f'{stage} {status}')}")
    failures = db.recent_failures()
    if failures:
        print("\nRecent failures:")
        for row in failures:
            print(f"  {row['video_id']}  [{row['stage']}, attempts: {row['attempts']}]  {row['title']}")
            print(f"      {row['error']}")
    return 0


def cmd_check(settings: Settings) -> int:
    log.info("Config OK: %s (niche: %s)", _relative(settings.config_path), settings.niche_name)
    client = YouTubeClient(settings.require_youtube_key())
    client.video_details(["dQw4w9WgXcQ"])  # 1 quota unit; raises a friendly error if the key is bad
    log.info("YouTube API key OK.")
    if settings.gemini_api_key:
        gemini = GeminiClient(settings.gemini_api_key, settings.writing.models)
        for model in settings.writing.models:
            gemini.check_model(model)  # free metadata request, uses no quota
        log.info("Gemini API key OK (models: %s).", ", ".join(settings.writing.models))
    else:
        log.warning("GEMINI_API_KEY is not set yet; needed for article writing (step 3).")
    if settings.transcript_proxy_url:
        log.info("Transcript proxy is configured.")
    return 0


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)
