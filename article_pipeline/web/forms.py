"""The project settings form: field definitions, and turning submitted form data into settings."""

from __future__ import annotations

from ..config import ConfigError

# (key, label, type, help). type: lines | int | float | text | text_opt | bool | (choices...)
SETTINGS_FORM = [
    ("Search", "What to look for on YouTube.", [
        ("search.keywords", "Keywords", "lines",
         "One search per line. Each keyword uses 100 of your 10,000 daily YouTube quota units per 50 results."),
        ("search.max_results_per_keyword", "Results per keyword", "int", "1 to 200. Above 50 uses extra quota."),
        ("search.order", "Sort results by", ("relevance", "date", "viewCount", "rating"), ""),
        ("search.relevance_language", "Prefer language", "text_opt", "Language code like en. Empty means any."),
        ("search.region_code", "Prefer country", "text_opt", "Two letter code like US. Empty means any."),
        ("search.safe_search", "Safe search", ("none", "moderate", "strict"), ""),
    ]),
    ("Filters", "Which search results are worth turning into articles.", [
        ("filters.min_views", "Minimum views", "int", ""),
        ("filters.max_age_days", "Maximum age (days)", "int", "Only videos published in this many days."),
        ("filters.min_duration_minutes", "Minimum length (minutes)", "float", "Skips Shorts and short clips."),
        ("filters.max_duration_minutes", "Maximum length (minutes)", "float", "Skips long streams and podcasts."),
        ("filters.exclude_title_words", "Skip titles containing", "lines", "One word or phrase per line."),
        ("filters.exclude_channels", "Skip channels", "lines", "Channel names or IDs, one per line."),
        ("filters.max_videos_per_run", "New videos per run", "int", "The highest viewed are kept."),
    ]),
    ("Transcripts", "", [
        ("transcripts.languages", "Languages", "lines", "Preferred transcript languages in order, like en."),
        ("transcripts.allow_auto_generated", "Allow auto generated captions", "bool",
         "Most videos only have these."),
        ("transcripts.translate_if_missing", "Translate if not available", "bool",
         "Translate another language into the first one above."),
        ("transcripts.min_words", "Minimum words", "int", "Shorter transcripts are marked failed."),
        ("transcripts.delay_seconds", "Pause between videos (seconds)", "float", "Avoids YouTube blocking you."),
    ]),
    ("Writing", "How Gemini writes the articles. The full instructions are on the Prompts page.", [
        ("writing.model", "Gemini model", "text", "Free models: ai.google.dev/gemini-api/docs/pricing"),
        ("writing.fallback_models", "Backup models", "lines",
         "Used in order when a model hits its free daily limit."),
        ("writing.audience", "Audience", "text", "Who you write for. Empty to leave it to the prompt."),
        ("writing.tone", "Tone", "text", "Leave empty if your prompt describes the tone."),
        ("writing.target_word_count", "Target length (words)", "int", ""),
        ("writing.temperature", "Creativity", "float", "0 is predictable, 1 or more is more creative."),
        ("writing.include_source_credit", "Add source credit to exports", "bool",
         "Adds 'Inspired by <video> by <channel>' to exported and copied articles."),
        ("writing.delay_seconds", "Pause between articles (seconds)", "float", "Stays under per minute limits."),
        ("writing.max_articles_per_run", "Articles per run", "int", "The free tier has a small daily limit."),
    ]),
    ("Image prompts", "", [
        ("images.count", "Prompts per article", "int", "The first is always the header / thumbnail."),
        ("images.aspect_ratio", "Aspect ratio", "text", "Width:height, like 16:9."),
        ("images.temperature", "Creativity", "float", ""),
    ]),
]


def field_value(settings: dict, key: str):
    section, name = key.split(".")
    return (settings.get(section) or {}).get(name)


def display_value(value, kind) -> str:
    if kind == "lines":
        return "\n".join(value or [])
    if value is None:
        return ""
    if kind == "float" and isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def parse_settings_form(form) -> dict:
    """Form data -> nested settings dict. Raises ConfigError with a readable message."""
    data: dict = {}
    for _, _, fields in SETTINGS_FORM:
        for key, label, kind, _ in fields:
            section, name = key.split(".")
            raw = form.get(key, "")
            if kind == "lines":
                value = [line.strip() for line in raw.splitlines() if line.strip()]
            elif kind == "bool":
                value = form.get(key) == "on"
            elif kind == "int":
                try:
                    value = int(raw.strip())
                except ValueError:
                    raise ConfigError(f"{label} must be a whole number.")
            elif kind == "float":
                try:
                    value = float(raw.strip())
                except ValueError:
                    raise ConfigError(f"{label} must be a number.")
                if value.is_integer():
                    value = int(value)
            elif kind == "text_opt":
                value = raw.strip() or None
            else:
                value = raw.strip()
            data.setdefault(section, {})[name] = value
    return data
