"""Load and validate config.yaml plus secrets from .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"

VALID_ORDERS = {"relevance", "date", "viewCount", "rating"}
VALID_SAFE_SEARCH = {"none", "moderate", "strict"}


class ConfigError(Exception):
    """Raised when config.yaml or .env is missing or invalid."""


@dataclass
class SearchConfig:
    keywords: list
    max_results_per_keyword: int = 25
    order: str = "relevance"
    relevance_language: Optional[str] = "en"
    region_code: Optional[str] = None
    safe_search: str = "moderate"

    def validate(self) -> None:
        if not isinstance(self.keywords, list) or not self.keywords:
            raise ConfigError("search.keywords must be a non-empty list of search phrases.")
        if not all(isinstance(k, str) and k.strip() for k in self.keywords):
            raise ConfigError("search.keywords must only contain non-empty text.")
        _require_int("search.max_results_per_keyword", self.max_results_per_keyword, 1, 200)
        if self.order not in VALID_ORDERS:
            raise ConfigError(f"search.order must be one of {sorted(VALID_ORDERS)}, got '{self.order}'.")
        if self.safe_search not in VALID_SAFE_SEARCH:
            raise ConfigError(f"search.safe_search must be one of {sorted(VALID_SAFE_SEARCH)}.")


@dataclass
class FilterConfig:
    min_views: int = 0
    max_age_days: int = 365
    min_duration_minutes: float = 0
    max_duration_minutes: float = 600
    exclude_title_words: list = field(default_factory=list)
    exclude_channels: list = field(default_factory=list)
    max_videos_per_run: int = 10

    def validate(self) -> None:
        _require_int("filters.min_views", self.min_views, 0)
        _require_int("filters.max_age_days", self.max_age_days, 1)
        _require_number("filters.min_duration_minutes", self.min_duration_minutes, 0)
        _require_number("filters.max_duration_minutes", self.max_duration_minutes, 0)
        if self.min_duration_minutes > self.max_duration_minutes:
            raise ConfigError("filters.min_duration_minutes cannot be larger than max_duration_minutes.")
        _require_int("filters.max_videos_per_run", self.max_videos_per_run, 1)
        for name in ("exclude_title_words", "exclude_channels"):
            if not isinstance(getattr(self, name), list):
                raise ConfigError(f"filters.{name} must be a list (use [] for none).")


@dataclass
class TranscriptConfig:
    languages: list = field(default_factory=lambda: ["en"])
    allow_auto_generated: bool = True
    translate_if_missing: bool = False
    min_words: int = 300
    delay_seconds: float = 2

    def validate(self) -> None:
        if not isinstance(self.languages, list) or not self.languages:
            raise ConfigError('transcripts.languages must be a non-empty list, e.g. ["en"].')
        _require_int("transcripts.min_words", self.min_words, 0)
        _require_number("transcripts.delay_seconds", self.delay_seconds, 0)


@dataclass
class WritingConfig:
    model: str = "gemini-3.8-flash"
    fallback_models: list = field(default_factory=list)
    audience: str = ""
    tone: str = ""
    target_word_count: int = 1200
    instructions_file: Optional[str] = None
    instructions: str = ""
    include_source_credit: bool = True
    temperature: float = 0.8
    delay_seconds: float = 5
    max_articles_per_run: int = 10

    def validate(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise ConfigError("writing.model must be a Gemini model name, e.g. gemini-3.8-flash.")
        if not isinstance(self.fallback_models, list):
            raise ConfigError("writing.fallback_models must be a list (use [] for none).")
        _require_int("writing.target_word_count", self.target_word_count, 200, 6000)
        _require_number("writing.temperature", self.temperature, 0, 2)
        _require_number("writing.delay_seconds", self.delay_seconds, 0)
        _require_int("writing.max_articles_per_run", self.max_articles_per_run, 1)
        if self.instructions_file and self.instructions.strip():
            raise ConfigError("Set either writing.instructions_file or writing.instructions, not both.")

    def load_instructions(self) -> None:
        """Read instructions_file (relative to the project folder) into `instructions`."""
        if not self.instructions_file:
            return
        path = _resolve(self.instructions_file)
        if not path.exists():
            raise ConfigError(f"writing.instructions_file not found: {path}")
        self.instructions = path.read_text(encoding="utf-8").strip()
        if not self.instructions:
            raise ConfigError(f"writing.instructions_file is empty: {path}")

    @property
    def models(self) -> list:
        """Primary model first, then fallbacks, without duplicates."""
        seen: list = []
        for m in [self.model, *self.fallback_models]:
            if m and m not in seen:
                seen.append(m)
        return seen


@dataclass
class OutputConfig:
    drafts_dir: str = "drafts"
    database_path: str = "data/pipeline.db"
    log_dir: str = "logs"


@dataclass
class Settings:
    config_path: Path
    niche_name: str
    search: SearchConfig
    filters: FilterConfig
    transcripts: TranscriptConfig
    writing: WritingConfig
    drafts_dir: Path
    database_path: Path
    log_dir: Path
    youtube_api_key: Optional[str]
    transcript_proxy_url: Optional[str]
    gemini_api_key: Optional[str]

    def require_gemini_key(self) -> str:
        if not self.gemini_api_key:
            raise ConfigError(
                "GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey "
                "and paste it after GEMINI_API_KEY= in .env (see README)."
            )
        return self.gemini_api_key

    def require_youtube_key(self) -> str:
        if not self.youtube_api_key:
            raise ConfigError(
                "YOUTUBE_API_KEY is not set. Copy .env.example to .env and paste your key "
                "after YOUTUBE_API_KEY= (see README, 'Get a YouTube API key')."
            )
        return self.youtube_api_key


def load_settings(config_path: Optional[Path] = None) -> Settings:
    """Read the YAML config and .env, validate them, and return Settings."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse {path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name} must contain sections like 'search:' and 'filters:'.")

    known_sections = {"niche", "search", "filters", "transcripts", "writing", "output"}
    unknown = set(raw) - known_sections
    if unknown:
        raise ConfigError(
            f"Unknown section(s) in {path.name}: {', '.join(sorted(unknown))}. "
            f"Valid sections: {', '.join(sorted(known_sections))}."
        )
    if "search" not in raw:
        raise ConfigError(f"{path.name} is missing the 'search:' section with your keywords.")

    search = _build(SearchConfig, raw.get("search"), "search")
    filters = _build(FilterConfig, raw.get("filters"), "filters")
    transcripts = _build(TranscriptConfig, raw.get("transcripts"), "transcripts")
    writing = _build(WritingConfig, raw.get("writing"), "writing")
    output = _build(OutputConfig, raw.get("output"), "output")
    for section in (search, filters, transcripts, writing):
        section.validate()
    writing.load_instructions()

    niche = raw.get("niche") or {}

    load_dotenv(PROJECT_ROOT / ".env")

    return Settings(
        config_path=path,
        niche_name=str(niche.get("name") or "Untitled niche"),
        search=search,
        filters=filters,
        transcripts=transcripts,
        writing=writing,
        drafts_dir=_resolve(output.drafts_dir),
        database_path=_resolve(output.database_path),
        log_dir=_resolve(output.log_dir),
        youtube_api_key=_env("YOUTUBE_API_KEY"),
        transcript_proxy_url=_env("TRANSCRIPT_PROXY_URL"),
        gemini_api_key=_env("GEMINI_API_KEY"),
    )


def _build(cls, data: Any, section: str):
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"'{section}' must be a section of settings, not a single value.")
    valid = {f.name for f in fields(cls)}
    unknown = set(data) - valid
    if unknown:
        raise ConfigError(
            f"Unknown setting(s) in '{section}': {', '.join(sorted(unknown))}. "
            f"Valid settings: {', '.join(sorted(valid))}."
        )
    try:
        return cls(**data)
    except TypeError as exc:
        raise ConfigError(f"Missing required setting in '{section}': {exc}") from exc


def _resolve(value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p


def _env(name: str) -> Optional[str]:
    value = os.getenv(name, "").strip()
    return value or None


def _require_number(name: str, value: Any, minimum: float, maximum: Optional[float] = None) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} must be a number, got '{value}'.")
    if value < minimum or (maximum is not None and value > maximum):
        bounds = f"between {minimum} and {maximum}" if maximum is not None else f"at least {minimum}"
        raise ConfigError(f"{name} must be {bounds}, got {value}.")


def _require_int(name: str, value: Any, minimum: int, maximum: Optional[int] = None) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be a whole number, got '{value}'.")
    _require_number(name, value, minimum, maximum)
