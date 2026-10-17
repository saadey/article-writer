"""Step 4: write image-generator prompts for a finished article."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import ImagesConfig
from .gemini import GeminiClient, GenerationError

IMAGE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "image_prompts": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "placement": {
                        "type": "STRING",
                        "description": "'Header / thumbnail' for the first, otherwise the section heading it illustrates.",
                    },
                    "prompt": {"type": "STRING", "description": "The full image generator prompt."},
                    "alt_text": {"type": "STRING", "description": "Short description for screen readers."},
                },
                "required": ["placement", "prompt", "alt_text"],
                "propertyOrdering": ["placement", "prompt", "alt_text"],
            },
        },
    },
    "required": ["image_prompts"],
}


def build_system_prompt(cfg: ImagesConfig, niche_name: str) -> str:
    instructions = cfg.instructions.strip() or "Write prompts for an AI image generator to illustrate this article."
    return f"""{instructions}

CONTEXT
- Publication topic: {niche_name}
- Number of prompts: exactly {cfg.count} (the first is the header / thumbnail image)
- Aspect ratio: {cfg.aspect_ratio}

RESPONSE FORMAT
Your answer is read by a program, so return JSON with an `image_prompts` list. Each item has
`placement`, `prompt`, and `alt_text`."""


def write_image_prompts(client: GeminiClient, folder: Path, cfg: ImagesConfig, niche_name: str) -> Path:
    """Read article.md from the draft folder, write image_prompts.md. Returns its path."""
    article_path = folder / "article.md"
    if not article_path.exists():
        raise GenerationError(f"Missing article.md in {folder.name}; write the article first.")
    article = article_path.read_text(encoding="utf-8")

    result, model = client.generate_json(
        system=build_system_prompt(cfg, niche_name),
        prompt=f"Article:\n\n{article}",
        schema=IMAGE_SCHEMA,
        temperature=cfg.temperature,
    )
    prompts = [p for p in result.get("image_prompts") or [] if (p.get("prompt") or "").strip()]
    if not prompts:
        raise GenerationError("Gemini returned no image prompts.")

    title = article.splitlines()[0].lstrip("# ").strip() if article.strip() else folder.name
    lines = [
        "# Image prompts",
        "",
        f"For: **{title}**  ",
        f"Aspect ratio: {cfg.aspect_ratio}",
        "",
        "Paste each prompt into your image tool (ChatGPT, Gemini, Midjourney, ...). "
        "Use the alt text when you upload the image.",
        "",
    ]
    for i, p in enumerate(prompts, 1):
        lines += [
            f"## {i}. {(p.get('placement') or 'Image').strip()}",
            "",
            "```text",
            p["prompt"].strip(),
            "```",
            "",
            f"**Alt text:** {(p.get('alt_text') or '').strip()}",
            "",
        ]
    lines.append(f"_Written by {model}._")
    out = folder / "image_prompts.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    metadata_path = folder / "metadata.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["images"] = {
            "count": len(prompts),
            "aspect_ratio": cfg.aspect_ratio,
            "model": model,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
