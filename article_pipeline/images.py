"""Step 4: write image-generator prompts for a finished article."""

from __future__ import annotations

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


def generate_image_prompts(client: GeminiClient, cfg: ImagesConfig, niche_name: str,
                           title: str, subtitle: str, body: str) -> tuple:
    """Ask Gemini for image prompts for an article. Returns (prompts, model)."""
    if not (body or "").strip():
        raise GenerationError("This video has no article yet; write the article first.")
    article = f"# {title}\n\n{subtitle}\n\n{body}"
    result, model = client.generate_json(
        system=build_system_prompt(cfg, niche_name),
        prompt=f"Article:\n\n{article}",
        schema=IMAGE_SCHEMA,
        temperature=cfg.temperature,
    )
    prompts = [
        {"placement": (p.get("placement") or "Image").strip(), "prompt": p["prompt"].strip(),
         "alt_text": (p.get("alt_text") or "").strip()}
        for p in result.get("image_prompts") or [] if (p.get("prompt") or "").strip()
    ]
    if not prompts:
        raise GenerationError("Gemini returned no image prompts.")
    return prompts, model
