"""Step 5: drafts/INDEX.md, an overview of every draft and what to do next."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from .db import Database

WAITING_FOR = {
    "transcript": "waiting for article",
    "article": "waiting for image prompts",
}


def _metadata(folder: Path) -> dict:
    try:
        return json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _local_date(iso_utc) -> str:
    try:
        return datetime.fromisoformat(iso_utc).astimezone().strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def _link(text: str, target: Path, drafts_dir: Path) -> str:
    rel = os.path.relpath(target, drafts_dir).replace(os.sep, "/").replace(" ", "%20")
    return f"[{text.replace('|', '/')}]({rel})"


def write_index(db: Database, drafts_dir: Path) -> Path:
    ready, published, in_progress, failed = [], [], [], []
    for row in db.all_rows():
        stage, status = row["stage"], row["status"]
        if status == "failed":
            failed.append(row)
        elif stage == "published":
            published.append(row)
        elif stage == "images" and status == "done":
            ready.append(row)
        else:
            in_progress.append(row)

    lines = [
        "# Drafts",
        "",
        f"_Updated {datetime.now().strftime('%Y-%m-%d %H:%M')}. The pipeline rewrites this file after every run._",
        "",
        f"## Ready to publish ({len(ready)})",
        "",
    ]
    if ready:
        lines += [
            "Each folder has `article.md` (the post), `image_prompts.md`, and `editor_notes.md` "
            "(title options and claims to check). After you publish one, run "
            "`python main.py published <video id>`.",
            "",
            "| Article | Words | Claims to check | Video ID |",
            "|---|---|---|---|",
        ]
        for row in ready:
            folder = Path(row["draft_dir"])
            article = _metadata(folder).get("article", {})
            title = article.get("title") or row["title"]
            lines.append(
                f"| {_link(title, folder / 'article.md', drafts_dir)} | {article.get('word_count', '')} "
                f"| {article.get('flagged_claims', '')} | `{row['video_id']}` |"
            )
    else:
        lines.append("Nothing ready yet.")

    lines += ["", f"## Published ({len(published)})", ""]
    for row in published:
        folder = Path(row["draft_dir"])
        meta = _metadata(folder)
        title = meta.get("article", {}).get("title") or row["title"]
        when = _local_date(meta.get("published_at") or row["updated_at"])
        lines.append(f"- {when}: {_link(title, folder / 'article.md', drafts_dir)} (`{row['video_id']}`)")
    if not published:
        lines.append("None yet.")

    lines += ["", f"## In progress ({len(in_progress)})", ""]
    for row in in_progress:
        what = WAITING_FOR.get(row["stage"], "done") if row["status"] == "done" else f"{row['stage']} interrupted"
        lines.append(f"- {row['title']} (`{row['video_id']}`): {what}")
    if in_progress:
        lines += ["", "Continue with `python main.py write`."]
    else:
        lines.append("Nothing in progress.")

    lines += ["", f"## Failed ({len(failed)})", ""]
    for row in failed:
        lines.append(f"- {row['title']} (`{row['video_id']}`), {row['stage']} step: {row['error']}")
    if failed:
        lines += ["", "Retry with `python main.py retry`."]
    else:
        lines.append("No failures.")

    drafts_dir.mkdir(parents=True, exist_ok=True)
    path = drafts_dir / "INDEX.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
