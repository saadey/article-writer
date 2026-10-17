"""Command-line entry point: python main.py <command>."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .articles import write_article
from .config import ConfigError, Settings, load_settings
from .db import Database
from .gemini import GeminiClient, GeminiError, GenerationError
from .images import write_image_prompts
from .index import write_index
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

# Each Gemini step: the stage it needs finished first, and a label for logs.
GEN_STEPS = {
    "article": ("transcript", "Step 3: writing articles"),
    "images": ("article", "Step 4: writing image prompts"),
}
UPDATES_INDEX = {"run", "write", "images", "retry", "published", "ready"}


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

    run = sub.add_parser("run", help="Do everything: find videos, transcripts, articles, image prompts")
    run.add_argument("--dry-run", action="store_true",
                     help="Only list the videos that would be processed; save nothing")
    run.add_argument("--no-write", action="store_true",
                     help="Stop after saving transcripts (no Gemini calls)")

    write = sub.add_parser("write", help="Write articles + image prompts for saved transcripts")
    write.add_argument("--limit", type=int, default=None,
                       help="At most this many per step (default: writing.max_articles_per_run)")
    write.add_argument("--video", metavar="VIDEO_ID", default=None,
                       help="(Re)write the article and image prompts for this one video")

    images = sub.add_parser("images", help="Only write image prompts for finished articles")
    images.add_argument("--limit", type=int, default=None, help="At most this many")
    images.add_argument("--video", metavar="VIDEO_ID", default=None,
                        help="(Re)write the image prompts for this one video")

    retry = sub.add_parser("retry", help="Retry videos that failed (or were interrupted) earlier")
    retry.add_argument("--limit", type=int, default=None, help="Retry at most this many videos per step")

    sub.add_parser("ready", help="List drafts that are ready to publish")
    published = sub.add_parser("published", help="Mark drafts as published after you post them")
    published.add_argument("video_ids", nargs="+", metavar="VIDEO_ID")
    published.add_argument("--undo", action="store_true", help="Move them back to 'ready to publish'")

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
            try:
                return dispatch(args, settings, db)
            finally:
                if args.command in UPDATES_INDEX and not getattr(args, "dry_run", False):
                    write_index(db, settings.drafts_dir)
    except ConfigError as exc:
        log.error("Config problem: %s", exc)
        return 2
    except (YouTubeAPIError, GeminiError) as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.warning("Interrupted. Unfinished videos stay 'pending'; run 'retry' to pick them up.")
        return 130


def dispatch(args, settings: Settings, db: Database) -> int:
    if args.command == "run":
        return cmd_run(settings, db, args.dry_run, args.no_write)
    if args.command == "write":
        return cmd_generate(settings, db, ["article", "images"], args.limit, args.video)
    if args.command == "images":
        return cmd_generate(settings, db, ["images"], args.limit, args.video)
    if args.command == "retry":
        return cmd_retry(settings, db, args.limit)
    if args.command == "ready":
        return cmd_ready(settings, db)
    if args.command == "published":
        return cmd_published(db, args.video_ids, args.undo)
    if args.command == "status":
        return cmd_status(db)
    if args.command == "check":
        return cmd_check(settings)
    return 0


# ---- steps 1-2 ---------------------------------------------------------------

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
    return cmd_generate(settings, db, ["article", "images"], None, None)


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


# ---- steps 3-4 (Gemini) --------------------------------------------------------

def cmd_generate(settings: Settings, db: Database, stages: list, limit, video_id) -> int:
    """Run the given Gemini steps in order, either for one video or for everything waiting."""
    client = GeminiClient(settings.require_gemini_key(), settings.writing.models)

    if video_id:
        row = db.get(video_id)
        if row is None:
            log.error("Video %s is not in the database. Check the ID with 'python main.py status'.", video_id)
            return 1
        if row["stage"] == "published":
            log.error("Video %s is marked as published. Run 'python main.py published --undo %s' first.",
                      video_id, video_id)
            return 1
        needs = GEN_STEPS[stages[0]][0]
        order = ["transcript", "article", "images", "published"]
        reached = order.index(row["stage"]) - (0 if row["status"] == "done" else 1)
        if reached < order.index(needs):
            log.error("Video %s isn't far enough along yet (it needs a finished %s).", video_id, needs)
            return 1
        for stage in stages:
            if not process_generation(settings, db, client, stage, [db.get(video_id)]):
                break
            if db.get(video_id)["status"] == "failed":
                break  # don't build image prompts on top of a failed article
        return 0

    limit = limit or settings.writing.max_articles_per_run
    for stage in stages:
        rows = db.ready_for_next(GEN_STEPS[stage][0], limit=limit)
        if not rows:
            log.info("%s: nothing waiting.", GEN_STEPS[stage][1])
            continue
        if not process_generation(settings, db, client, stage, rows):
            break
    return 0


def process_generation(settings: Settings, db: Database, client: GeminiClient, stage: str, rows: list) -> bool:
    """Run one Gemini step over rows. Returns False if the run had to stop (quota, bad key)."""
    previous, label = GEN_STEPS[stage]
    delay = settings.writing.delay_seconds
    log.info("%s for %d video(s).", label, len(rows))
    done = failed = 0
    for index, row in enumerate(rows, 1):
        if index > 1 and delay:
            time.sleep(delay)
        video_id = row["video_id"]
        folder = Path(row["draft_dir"]) if row["draft_dir"] else draft_folder(
            settings.drafts_dir, VideoInfo.from_dict(json.loads(row["metadata_json"]))
        )
        log.info("[%d/%d] %s | %s", index, len(rows), video_id, row["title"])
        db.start_stage(video_id, stage)
        try:
            if stage == "article":
                out = write_article(client, folder, settings.writing, settings.niche_name)
            else:
                out = write_image_prompts(client, folder, settings.images, settings.niche_name)
        except GeminiError as exc:
            # Not this video's fault (quota, bad key): leave it queued for the next run.
            db.set_stage_done(video_id, previous)
            log.error("%s", exc)
            log.error("Stopping early. %d video(s) left for next time.", len(rows) - index + 1)
            _summary(label, done, failed)
            return False
        except GenerationError as exc:
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
            log.info("  wrote %s", _relative(out))
    _summary(label, done, failed)
    return True


def _summary(label: str, done: int, failed: int) -> None:
    log.info("%s: %d done, %d failed.", label.split(": ", 1)[-1].capitalize(), done, failed)
    if failed:
        log.info("See failures with 'python main.py status'; retry them with 'python main.py retry'.")


def cmd_retry(settings: Settings, db: Database, limit) -> int:
    rows = {stage: db.retryable(stage=stage, limit=limit) for stage in ("transcript", "article", "images")}
    if not any(rows.values()):
        log.info("Nothing to retry: no failed or pending videos.")
        return 0
    if rows["transcript"]:
        log.info("Retrying %d transcript(s).", len(rows["transcript"]))
        videos = [VideoInfo.from_dict(json.loads(r["metadata_json"])) for r in rows["transcript"]]
        process_transcripts(settings, db, videos)
    if rows["article"] or rows["images"]:
        client = GeminiClient(settings.require_gemini_key(), settings.writing.models)
        for stage in ("article", "images"):
            if rows[stage]:
                log.info("Retrying %d %s step(s).", len(rows[stage]), stage)
                if not process_generation(settings, db, client, stage, rows[stage]):
                    break
    return 0


# ---- step 5: publishing ----------------------------------------------------------

def cmd_ready(settings: Settings, db: Database) -> int:
    rows = db.ready_for_next("images")
    if not rows:
        print("Nothing is ready to publish yet. Run: python main.py write")
        return 0
    print(f"{len(rows)} draft(s) ready to publish:\n")
    for row in rows:
        folder = Path(row["draft_dir"])
        title = _article_title(folder) or row["title"]
        print(f"  {row['video_id']}  {title}")
        print(f"      {_relative(folder)}/")
    print(f"\nOverview with links: {_relative(settings.drafts_dir / 'INDEX.md')}")
    print("After posting one, run: python main.py published <video id>")
    return 0


def cmd_published(db: Database, video_ids: list, undo: bool) -> int:
    errors = 0
    for video_id in video_ids:
        row = db.get(video_id)
        if row is None:
            log.error("%s: not found. Run 'python main.py ready' to see draft IDs.", video_id)
            errors += 1
            continue
        folder = Path(row["draft_dir"]) if row["draft_dir"] else None
        if undo:
            if row["stage"] != "published":
                log.error("%s: isn't marked as published.", video_id)
                errors += 1
                continue
            db.set_stage_done(video_id, "images")
            _set_published_at(folder, None)
            log.info("%s: moved back to ready to publish.", video_id)
            continue
        if row["stage"] == "published":
            log.info("%s: already marked as published.", video_id)
            continue
        if (row["stage"], row["status"]) != ("images", "done"):
            log.error("%s: not ready yet (it needs an article and image prompts first).", video_id)
            errors += 1
            continue
        db.set_stage_done(video_id, "published")
        _set_published_at(folder, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        log.info("%s: marked as published.", video_id)
    return 1 if errors else 0


def _set_published_at(folder, value) -> None:
    path = folder / "metadata.json" if folder else None
    if not path or not path.exists():
        return
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if value:
        metadata["published_at"] = value
    else:
        metadata.pop("published_at", None)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def _article_title(folder: Path) -> str:
    try:
        return json.loads((folder / "metadata.json").read_text(encoding="utf-8"))["article"]["title"]
    except (OSError, ValueError, KeyError):
        return ""


# ---- info ---------------------------------------------------------------------------

STAGE_LABELS = {
    ("transcript", "done"): "transcript saved, waiting for article",
    ("transcript", "failed"): "transcript failed",
    ("transcript", "pending"): "transcript interrupted",
    ("article", "done"): "article written, waiting for image prompts",
    ("article", "failed"): "article failed",
    ("article", "pending"): "article interrupted",
    ("images", "done"): "ready to publish",
    ("images", "failed"): "image prompts failed",
    ("images", "pending"): "image prompts interrupted",
    ("published", "done"): "published",
}
STAGE_ORDER = ["transcript", "article", "images", "published"]


def cmd_status(db: Database) -> int:
    counts = db.counts()
    if not counts:
        print("No videos tracked yet. Run: python main.py run")
        return 0
    print("Videos:")
    for (stage, status), n in sorted(counts.items(), key=lambda kv: (STAGE_ORDER.index(kv[0][0])
                                                                     if kv[0][0] in STAGE_ORDER else 99, kv[0][1])):
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
        log.warning("GEMINI_API_KEY is not set yet; needed for articles and image prompts.")
    if settings.transcript_proxy_url:
        log.info("Transcript proxy is configured.")
    return 0


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)
