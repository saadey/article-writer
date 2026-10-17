"""Command-line entry point: python main.py <command>."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import ConfigError, Settings, load_settings
from .db import Database
from .logging_setup import get_logger, setup_logging
from .transcripts import (
    TranscriptBlockedError,
    TranscriptError,
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

    run = sub.add_parser("run", help="Search YouTube for new videos and save their transcripts")
    run.add_argument("--dry-run", action="store_true",
                     help="Only list the videos that would be processed; save nothing")

    retry = sub.add_parser("retry", help="Retry videos that failed (or were interrupted) earlier")
    retry.add_argument("--limit", type=int, default=None, help="Retry at most this many videos")

    sub.add_parser("status", help="Show how many videos are done/failed, and recent errors")
    sub.add_parser("check", help="Check config, .env and the YouTube API key")
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
                return cmd_run(settings, db, args.dry_run)
            if args.command == "retry":
                return cmd_retry(settings, db, args.limit)
            if args.command == "status":
                return cmd_status(db)
            if args.command == "check":
                return cmd_check(settings)
    except ConfigError as exc:
        log.error("Config problem: %s", exc)
        return 2
    except YouTubeAPIError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.warning("Interrupted. Unfinished videos stay 'pending'; run 'retry' to pick them up.")
        return 130
    return 0


def cmd_run(settings: Settings, db: Database, dry_run: bool) -> int:
    log.info("Niche: %s | %d keyword(s)", settings.niche_name, len(settings.search.keywords))
    client = YouTubeClient(settings.require_youtube_key())
    videos = discover_videos(settings, client, db)

    if dry_run:
        for v in videos:
            log.info("  would process %s | %s views | %d min | %s",
                     v.video_id, f"{v.view_count:,}", v.duration_seconds // 60, v.title)
        log.info("Dry run: nothing was saved.")
        return 0
    return process_videos(settings, db, videos)


def cmd_retry(settings: Settings, db: Database, limit) -> int:
    rows = db.retryable(stage="transcript", limit=limit)
    if not rows:
        log.info("Nothing to retry: no failed or pending videos.")
        return 0
    log.info("Retrying %d video(s).", len(rows))
    videos = [VideoInfo.from_dict(json.loads(row["metadata_json"])) for row in rows]
    return process_videos(settings, db, videos)


def process_videos(settings: Settings, db: Database, videos: list) -> int:
    if not videos:
        log.info("No new videos to process.")
        return 0

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

    log.info("Finished: %d saved, %d failed. Drafts are in %s", done, failed, _relative(settings.drafts_dir))
    if failed:
        log.info("See failures with 'python main.py status'; retry them with 'python main.py retry'.")
    return 0


def cmd_status(db: Database) -> int:
    counts = db.counts()
    if not counts:
        print("No videos tracked yet. Run: python main.py run")
        return 0
    print("Videos by step and status:")
    for (stage, status), n in sorted(counts.items()):
        print(f"  {stage:<12} {status:<8} {n}")
    failures = db.recent_failures()
    if failures:
        print("\nRecent failures:")
        for row in failures:
            print(f"  {row['video_id']}  (attempts: {row['attempts']})  {row['title']}")
            print(f"      {row['error']}")
    return 0


def cmd_check(settings: Settings) -> int:
    log.info("Config OK: %s (niche: %s)", _relative(settings.config_path), settings.niche_name)
    client = YouTubeClient(settings.require_youtube_key())
    client.video_details(["dQw4w9WgXcQ"])  # 1 quota unit; raises a friendly error if the key is bad
    log.info("YouTube API key OK.")
    if settings.transcript_proxy_url:
        log.info("Transcript proxy is configured.")
    return 0


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)
