"""Step 3: rewrite a saved transcript into an article with the Gemini API."""

from __future__ import annotations

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
        "subtitle": {"type": "STRING", "description": "One sentence subtitle shown under the title."},
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


def generate_article(client: GeminiClient, cfg: WritingConfig, niche_name: str,
                     metadata: dict, transcript: str) -> dict:
    """Ask Gemini for an article. Returns the fields stored in the database."""
    if not (transcript or "").strip():
        raise GenerationError("There is no saved transcript for this video; retry the transcript step.")

    result, model = client.generate_json(
        system=build_system_prompt(cfg, niche_name),
        prompt=build_user_prompt(metadata, transcript),
        schema=ARTICLE_SCHEMA,
        temperature=cfg.temperature,
    )
    title = (result.get("title") or "").strip()
    body = (result.get("body_markdown") or "").strip()
    if not title or not body:
        raise GenerationError("Gemini's answer was missing a title or article body.")

    word_count = len(body.split())
    if word_count < cfg.target_word_count * 0.5:
        log.warning("  article is short: %d words (target %d)", word_count, cfg.target_word_count)

    return {
        "title": title,
        "subtitle": (result.get("subtitle") or "").strip(),
        "body": body,
        "title_options": [
            {"title": t["title"].strip(), "recommended": bool(t.get("recommended")),
             "reason": (t.get("reason") or "").strip()}
            for t in result.get("title_options") or [] if (t.get("title") or "").strip()
        ],
        "editor_notes": (result.get("editor_notes") or "").strip(),
        "flagged_claims": [c.strip() for c in result.get("flagged_claims") or [] if c and c.strip()],
        "word_count": word_count,
        "model": model,
    }
