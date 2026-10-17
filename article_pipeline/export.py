"""Turn a draft from the database into Markdown files (for copy/paste, download, or a folder)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from .db import loads
from .transcripts import slugify


def article_markdown(row, include_source_credit: bool = False) -> str:
    parts = [f"# {row['article_title']}"]
    if row["article_subtitle"]:
        parts.append(f"*{row['article_subtitle']}*")
    parts.append((row["article_body"] or "").strip())
    if include_source_credit and row["url"]:
        parts.append(f"---\n\n*Inspired by [\"{row['title']}\"]({row['url']}) by {row['channel_title']}.*")
    return "\n\n".join(parts) + "\n"


def editor_notes_markdown(row) -> str:
    lines = ["# Editor notes", ""]
    if row["url"]:
        lines += [f"Source: [{row['title']}]({row['url']}) by {row['channel_title']}", ""]
    lines += ["## Title options", ""]
    options = loads(row["title_options_json"], [])
    for i, opt in enumerate(options, 1):
        label = " **(Recommended)**" if opt.get("recommended") else ""
        reason = f": {opt['reason']}" if opt.get("reason") else ""
        lines.append(f"{i}. {opt['title']}{label}{reason}")
    if not options:
        lines.append("(none)")
    lines += ["", "## What was removed and how it is structured", "", row["editor_notes"] or "(none)", ""]
    lines += ["## Claims to check before publishing", ""]
    claims = loads(row["flagged_claims_json"], [])
    lines += [f"- {c}" for c in claims] if claims else ["None flagged."]
    if row["article_model"]:
        lines += ["", f"_Written by {row['article_model']}._"]
    return "\n".join(lines) + "\n"


def image_prompts_markdown(row, aspect_ratio: str = "") -> str:
    lines = ["# Image prompts", "", f"For: **{row['article_title'] or row['title']}**  "]
    if aspect_ratio:
        lines.append(f"Aspect ratio: {aspect_ratio}")
    lines.append("")
    for i, p in enumerate(loads(row["image_prompts_json"], []), 1):
        lines += [f"## {i}. {p.get('placement', 'Image')}", "", "```text", p.get("prompt", ""), "```", "",
                  f"**Alt text:** {p.get('alt_text', '')}", ""]
    return "\n".join(lines)


def draft_files(row, include_source_credit: bool = False, aspect_ratio: str = "") -> dict:
    """filename -> text for everything this draft has so far."""
    files = {}
    if row["article_body"]:
        files["article.md"] = article_markdown(row, include_source_credit)
        files["editor_notes.md"] = editor_notes_markdown(row)
    if row["image_prompts_json"]:
        files["image_prompts.md"] = image_prompts_markdown(row, aspect_ratio)
    if row["transcript_text"]:
        files["transcript.txt"] = row["transcript_text"].strip() + "\n"
    metadata = loads(row["metadata_json"], {})
    metadata["transcript"] = loads(row["transcript_info_json"], {})
    files["metadata.json"] = json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"
    return files


def folder_name(row) -> str:
    return f"{slugify(row['article_title'] or row['title'] or 'draft')}-{row['video_id']}"


def zip_drafts(rows, include_source_credit: bool = False, aspect_ratio: str = "") -> bytes:
    """One zip with a folder per draft."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for row in rows:
            base = folder_name(row)
            for name, text in draft_files(row, include_source_credit, aspect_ratio).items():
                zf.writestr(f"{base}/{name}", text)
    return buffer.getvalue()


def export_to_folder(rows, target: Path, include_source_credit: bool = False, aspect_ratio: str = "") -> list:
    """Write a folder per draft under `target`. Returns the folders written."""
    written = []
    for row in rows:
        folder = target / folder_name(row)
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in draft_files(row, include_source_credit, aspect_ratio).items():
            (folder / name).write_text(text, encoding="utf-8")
        written.append(folder)
    return written
