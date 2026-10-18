"""Command-line interface: python main.py <command>. The web app (python app.py) does the same things."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import DB_PATH, EXPORTS_DIR, LOG_DIR, ConfigError
from .db import Database
from .export import export_to_folder
from .gemini import GeminiError
from .logging_setup import get_logger, setup_logging
from .pipeline import Cancelled, Pipeline
from .projects import ensure_a_project, import_yaml, resolve_project, settings_for
from .youtube_search import YouTubeAPIError

log = get_logger()

STAGE_LABELS = {
    ("found", "done"): "found, waiting for you to pick",
    ("dismissed", "done"): "dismissed",
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="Turn YouTube videos into article drafts. Tip: `python app.py` opens the same "
                    "features in your browser.",
    )
    parser.add_argument("--project", "-p", default=None,
                        help="Project name (optional when you only have one)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show debug detail")
    sub = parser.add_subparsers(dest="command", metavar="command")

    find = sub.add_parser("find", help="Search and save new videos for review (no transcripts or AI)")
    find.add_argument("--count", "-n", type=int, default=None, help="How many videos (default: from settings)")
    sub.add_parser("found", help="List found videos waiting for review, with their stats")
    process = sub.add_parser("process", help="Write the chosen found videos: transcript, article, images")
    process.add_argument("video_ids", nargs="+", metavar="VIDEO_ID")

    run = sub.add_parser("run", help="Fully automatic, no review: find new videos and write them all")
    run.add_argument("--dry-run", action="store_true", help="Only list the videos that would be processed")
    run.add_argument("--no-write", action="store_true", help="Stop after saving transcripts")

    write = sub.add_parser("write", help="Write articles + image prompts for saved transcripts")
    write.add_argument("--limit", type=int, default=None, help="At most this many per step")
    write.add_argument("--video", metavar="VIDEO_ID", help="(Re)write one video's article and image prompts")

    images = sub.add_parser("images", help="Only write image prompts for finished articles")
    images.add_argument("--limit", type=int, default=None, help="At most this many")
    images.add_argument("--video", metavar="VIDEO_ID", help="(Re)write one video's image prompts")

    retry = sub.add_parser("retry", help="Retry videos that failed (or were interrupted)")
    retry.add_argument("--limit", type=int, default=None, help="At most this many per step")

    sub.add_parser("ready", help="List drafts that are ready to publish")
    published = sub.add_parser("published", help="Mark drafts as published after you post them")
    published.add_argument("video_ids", nargs="+", metavar="VIDEO_ID")
    published.add_argument("--undo", action="store_true", help="Move them back to ready")

    export = sub.add_parser("export", help="Save drafts as Markdown folders")
    export.add_argument("--all", action="store_true", help="Export every draft, not only ready ones")
    export.add_argument("--to", type=Path, default=EXPORTS_DIR, help="Folder to write to (default: exports/)")

    sub.add_parser("status", help="How many videos are at each step, and recent errors")
    sub.add_parser("check", help="Check your API keys")
    sub.add_parser("projects", help="List projects")
    imp = sub.add_parser("import-config", help="Create a project from a config.yaml file")
    imp.add_argument("path", type=Path)
    imp.add_argument("--name", default=None, help="Project name (default: niche name in the file)")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    setup_logging(LOG_DIR, verbose=args.verbose)

    try:
        with Database(DB_PATH) as db:
            ensure_a_project(db)
            if args.command == "projects":
                return cmd_projects(db)
            if args.command == "import-config":
                pid = import_yaml(db, args.path, args.name)
                log.info("Created project '%s'.", db.project(pid)["name"])
                return 0
            project = resolve_project(db, args.project)
            pipeline = Pipeline(db, settings_for(project))
            return dispatch(args, db, pipeline)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    except (YouTubeAPIError, GeminiError) as exc:
        log.error("%s", exc)
        return 1
    except (KeyboardInterrupt, Cancelled):
        log.warning("Stopped. Unfinished videos stay queued; run 'retry' or 'write' to continue.")
        return 130


def dispatch(args, db: Database, pipeline: Pipeline) -> int:
    pid = pipeline.project_id
    cmd = args.command
    if cmd == "find":
        pipeline.find(args.count)
        log.info("Review them with: python main.py found")
        return 0
    if cmd == "found":
        return cmd_found(db, pid)
    if cmd == "process":
        rows = [_row(db, pid, v) for v in args.video_ids]
        if not all(rows):
            return 1
        pipeline.process([r["id"] for r in rows])
    elif cmd == "run":
        if args.dry_run:
            for v in pipeline.discover():
                log.info("  would process %s | %s views | %d min | %s",
                         v.video_id, f"{v.view_count:,}", v.duration_seconds // 60, v.title)
            log.info("Dry run: nothing was saved.")
            return 0
        pipeline.run_all(write=not args.no_write)
    elif cmd in ("write", "images"):
        stages = ("article", "images") if cmd == "write" else ("images",)
        if args.video:
            row = _row(db, pid, args.video)
            if row is None:
                return 1
            pipeline.regenerate(row["id"], stages)
        else:
            pipeline.write_pending(stages, args.limit)
    elif cmd == "retry":
        pipeline.retry(args.limit)
    elif cmd == "ready":
        return cmd_ready(db, pid)
    elif cmd == "published":
        return cmd_published(db, pid, args.video_ids, args.undo)
    elif cmd == "export":
        s = pipeline.settings
        rows = db.videos(pid, "all" if args.all else "ready")
        rows = [r for r in rows if r["transcript_text"]]
        folders = export_to_folder(rows, args.to, s.writing.include_source_credit, s.images.aspect_ratio)
        log.info("Exported %d draft(s) to %s", len(folders), args.to)
        return 0
    elif cmd == "status":
        return cmd_status(db, pid)
    elif cmd == "check":
        pipeline.check_keys()
        return 0
    if cmd in ("run", "write", "images", "retry", "process"):
        log.info("Done: %s.", pipeline.summary())
    return 0


def _row(db: Database, pid: int, video_id: str):
    row = db.video_by_youtube_id(pid, video_id)
    if row is None:
        log.error("Video %s is not in this project. See IDs with 'python main.py ready' or 'status'.", video_id)
    return row


def cmd_projects(db: Database) -> int:
    for p in db.projects():
        c = db.summary_counts(p["id"])
        print(f"  {p['name']}: {c['ready']} ready, {c['progress']} in progress, "
              f"{c['published']} published, {c['failed']} failed")
    return 0


def cmd_found(db: Database, pid: int) -> int:
    rows = db.found_videos(pid)
    if not rows:
        print("No found videos to review. Run: python main.py find")
        return 0
    print(f"{len(rows)} video(s) to review (most viewed first):\n")
    for r in rows:
        subs = f"{r['subscriber_count']:,}" if r["subscriber_count"] is not None else "hidden"
        likes = f"{r['like_count']:,}" if r["like_count"] is not None else "hidden"
        print(f"  {r['video_id']}  {r['title']}")
        print(f"      {r['channel_title']} ({subs} subscribers) | {r['view_count']:,} views | {likes} likes | "
              f"{(r['duration_sec'] or 0) // 60} min")
    print("\nWrite the ones you want: python main.py process VIDEO_ID [VIDEO_ID ...]")
    return 0


def cmd_ready(db: Database, pid: int) -> int:
    rows = db.videos(pid, "ready")
    if not rows:
        print("Nothing is ready to publish yet. Run: python main.py write")
        return 0
    print(f"{len(rows)} draft(s) ready to publish:\n")
    for row in rows:
        print(f"  {row['video_id']}  {row['article_title']}")
    print("\nOpen them in the app (python app.py) or export them: python main.py export")
    return 0


def cmd_published(db: Database, pid: int, video_ids: list, undo: bool) -> int:
    errors = 0
    for video_id in video_ids:
        row = _row(db, pid, video_id)
        if row is None:
            errors += 1
        elif undo and row["stage"] != "published":
            log.error("%s isn't marked as published.", video_id)
            errors += 1
        elif not undo and row["stage"] == "published":
            log.info("%s is already marked as published.", video_id)
        elif not undo and (row["stage"], row["status"]) != ("images", "done"):
            log.error("%s isn't ready yet (it needs an article and image prompts first).", video_id)
            errors += 1
        else:
            db.set_posted(row["id"], not undo)
            log.info("%s: %s.", video_id, "moved back to ready" if undo else "marked as published")
    return 1 if errors else 0


def cmd_status(db: Database, pid: int) -> int:
    counts = db.counts(pid)
    if not counts:
        print("No videos yet. Run: python main.py run")
        return 0
    order = ["found", "transcript", "article", "images", "published", "dismissed"]
    print("Videos:")
    rank = lambda stage: order.index(stage) if stage in order else len(order)  # noqa: E731
    for (stage, status), n in sorted(counts.items(), key=lambda kv: (rank(kv[0][0]), kv[0][1])):
        print(f"  {n:>4}  {STAGE_LABELS.get((stage, status), f'{stage} {status}')}")
    failures = db.recent_failures(pid)
    if failures:
        print("\nRecent failures:")
        for row in failures:
            print(f"  {row['video_id']}  [{row['stage']}, attempts: {row['attempts']}]  {row['title']}")
            print(f"      {row['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
