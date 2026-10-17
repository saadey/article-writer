"""Step 3: rewrite a saved transcript into an article with the Gemini API."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import WritingConfig
from .gemini import GeminiClient, GenerationError
from .logging_setup import get_logger

log = get_logger()


ARTICLE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title_options": {
            "type": "ARRAY",
            "description": "Exactly 3 suggested titles, the recommended one first.",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "title": {"type": "STRING"},
                    "recommended": {"type": "BOOLEAN"},
                    "reason": {"type": "STRING", "description": "One line on why (required for the recommended title)."},
                },
                "required": ["title", "recommended"],
            },
        },
        "title": {"type": "STRING", "description": "The title used for the article (the recommended one)."},
        "subtitle": {"type": "STRING", "description": "One sentence subtitle for the newsletter post."},
        "body_markdown": {
            "type": "STRING",
            "description": "The full article body in Markdown, with ## headings. Do not repeat the title.",
        },
        "editor_notes": {
            "type": "STRING",
            "description": "Two or three sentences on what was removed and the structure used.",
        },
        "flagged_claims": {
            "type": "ARRAY",
            "description": "Claims stated more confidently than the source supports, each with a short caveat. Empty if none.",
            "items": {"type": "STRING"},
        },
    },
    "required": ["title_options", "title", "subtitle", "body_markdown", "editor_notes", "flagged_claims"],
    "propertyOrdering": ["title_options", "title", "subtitle", "body_markdown", "editor_notes", "flagged_claims"],
}

def build_system_prompt(cfg: WritingConfig, niche_name: str) -> str:
    """Your instructions from config, plus context and the fields the pipeline needs back."""
    context = [f"- Publication topic: {niche_name}"]
    if cfg.audience.strip():
        context.append(f"- Target audience: {cfg.audience.strip()}")
    if cfg.tone.strip():
        context.append(f"- Tone: {cfg.tone.strip()}")
    context.append(f"- Target length: about {cfg.target_word_count} words for the article body.")
    instructions = cfg.instructions.strip() or "Turn the transcript into an original, publication ready article."

    return f"""{instructions}

CONTEXT
{chr(10).join(context)}

RESPONSE FORMAT
Your answer is read by a program, so return it as JSON with these fields (this replaces any
other layout described above, but keep all the content it asks for):
- title_options: 3 suggested titles, recommended first, with recommended=true and a one line reason.
- title: the title used for the article.
- subtitle: one sentence subtitle.
- body_markdown: the complete article in Markdown, without the title.
- editor_notes: what was removed and the structure used.
- flagged_claims: claims stated more confidently than the source supports, each with a short caveat (empty list if none)."""


def build_user_prompt(metadata: dict, transcript: str) -> str:
    return (
        f"Source video title: {metadata.get('title', '')}\n"
        f"Channel: {metadata.get('channel_title', '')}\n\n"
        f"Transcript:\n{transcript}"
    )


def write_article(
    client: GeminiClient, folder: Path, cfg: WritingConfig, niche_name: str
) -> Path:
    """Read transcript.txt from the draft folder, write article.md. Returns the article path."""
    transcript_path = folder / "transcript.txt"
    metadata_path = folder / "metadata.json"
    if not transcript_path.exists():
        raise GenerationError(f"Missing {transcript_path.name} in {folder.name}; retry the transcript step.")
    transcript = transcript_path.read_text(encoding="utf-8")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}

    result, model = client.generate_json(
        system=build_system_prompt(cfg, niche_name),
        prompt=build_user_prompt(metadata, transcript),
        schema=ARTICLE_SCHEMA,
        temperature=cfg.temperature,
    )
    title = (result.get("title") or "").strip()
    subtitle = (result.get("subtitle") or "").strip()
    body = (result.get("body_markdown") or "").strip()
    if not title or not body:
        raise GenerationError("Gemini's answer was missing a title or article body.")
    title_options = [t for t in result.get("title_options") or [] if (t.get("title") or "").strip()]
    flagged = [c.strip() for c in result.get("flagged_claims") or [] if c and c.strip()]

    word_count = len(body.split())
    if word_count < cfg.target_word_count * 0.5:
        log.warning("  article is short: %d words (target %d)", word_count, cfg.target_word_count)

    parts = [f"# {title}", f"*{subtitle}*" if subtitle else "", body]
    if cfg.include_source_credit and metadata.get("url"):
        parts.append(
            f"---\n\n*Inspired by [\"{metadata.get('title', 'this video')}\"]({metadata['url']}) "
            f"by {metadata.get('channel_title', 'the original creator')}.*"
        )
    article_path = folder / "article.md"
    article_path.write_text("\n\n".join(p for p in parts if p) + "\n", encoding="utf-8")
    (folder / "editor_notes.md").write_text(
        _editor_notes(metadata, title_options, (result.get("editor_notes") or "").strip(), flagged, model),
        encoding="utf-8",
    )

    if metadata:
        metadata["article"] = {
            "title": title,
            "subtitle": subtitle,
            "title_options": title_options,
            "word_count": word_count,
            "flagged_claims": len(flagged),
            "model": model,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return article_path


def _editor_notes(metadata: dict, title_options: list, notes: str, flagged: list, model: str) -> str:
    """Everything the editor should see before publishing, kept out of article.md."""
    lines = ["# Editor notes", ""]
    if metadata.get("url"):
        lines += [f"Source: [{metadata.get('title', '')}]({metadata['url']}) by {metadata.get('channel_title', '')}", ""]
    lines += ["## Title options", ""]
    for i, opt in enumerate(title_options, 1):
        label = " **(Recommended)**" if opt.get("recommended") else ""
        reason = f": {opt['reason'].strip()}" if (opt.get("reason") or "").strip() else ""
        lines.append(f"{i}. {opt['title'].strip()}{label}{reason}")
    if not title_options:
        lines.append("(none returned)")
    lines += ["", "## What was removed and how it is structured", "", notes or "(none returned)", ""]
    lines += ["## Claims to check before publishing", ""]
    lines += [f"- {c}" for c in flagged] if flagged else ["None flagged."]
    lines += ["", f"_Written by {model}._", ""]
    return "\n".join(lines)
