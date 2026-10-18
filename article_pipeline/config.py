"""Project settings (stored in the database), validation, and API keys (stored in .env)."""

from __future__ import annotations

import copy
import os
import re
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import dotenv_values

# FROZEN: running as the packaged app (PyInstaller), not from the source folder.
FROZEN = bool(getattr(sys, "frozen", False))
# Files that ship with the app: default prompts and the starter config.
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def _data_home() -> Path:
    """Where your data lives. From source: the project folder (as before).
    Packaged app: your user folder, so updating the app never touches your drafts."""
    override = os.environ.get("ARTICLE_WRITER_HOME")
    if override:
        return Path(override).expanduser()
    if not FROZEN:
        return RESOURCE_ROOT
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Article Writer"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Article Writer"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "article-writer"


DATA_HOME = _data_home()
DATA_DIR = DATA_HOME / "data"
DB_PATH = Path(os.environ.get("ARTICLE_WRITER_DB") or DATA_DIR / "app.db")  # override for testing
LOG_DIR = DATA_HOME / "logs"
EXPORTS_DIR = DATA_HOME / "exports"
ENV_PATH = DATA_HOME / ".env"
DEFAULT_PROMPTS_DIR = RESOURCE_ROOT / "prompts"
APP_PORT_FILE = DATA_DIR / "app-port.json"  # lets a second launch find the running app

VALID_ORDERS = {"relevance", "date", "viewCount", "rating"}
VALID_SAFE_SEARCH = {"none", "moderate", "strict"}
TRANSCRIPT_SOURCES = {"auto", "youtube", "gemini"}
KEY_NAMES = ("YOUTUBE_API_KEY", "GEMINI_API_KEY", "TRANSCRIPT_PROXY_URL")


class ConfigError(Exception):
    """Raised when project settings or API keys are missing or invalid."""


@dataclass
class SearchConfig:
    keywords: list = field(default_factory=list)
    max_results_per_keyword: int = 25
    order: str = "relevance"
    relevance_language: Optional[str] = "en"
    region_code: Optional[str] = None
    safe_search: str = "moderate"

    def validate(self) -> None:
        if not isinstance(self.keywords, list) or not self.keywords:
            raise ConfigError("Add at least one search keyword.")
        if not all(isinstance(k, str) and k.strip() for k in self.keywords):
            raise ConfigError("Search keywords must be non-empty text.")
        _require_int("Results per keyword", self.max_results_per_keyword, 1, 200)
        if self.order not in VALID_ORDERS:
            raise ConfigError(f"Search order must be one of {sorted(VALID_ORDERS)}.")
        if self.safe_search not in VALID_SAFE_SEARCH:
            raise ConfigError(f"Safe search must be one of {sorted(VALID_SAFE_SEARCH)}.")


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
        _require_int("Minimum views", self.min_views, 0)
        _require_int("Maximum age (days)", self.max_age_days, 1)
        _require_number("Minimum length (minutes)", self.min_duration_minutes, 0)
        _require_number("Maximum length (minutes)", self.max_duration_minutes, 0)
        if self.min_duration_minutes > self.max_duration_minutes:
            raise ConfigError("Minimum length cannot be larger than maximum length.")
        _require_int("Videos per run", self.max_videos_per_run, 1)
        for name in ("exclude_title_words", "exclude_channels"):
            if not isinstance(getattr(self, name), list):
                raise ConfigError(f"{name} must be a list.")


@dataclass
class TranscriptConfig:
    # gemini: Gemini transcribes the public video from its link (official Google API; default).
    # auto: YouTube captions first, Gemini as fallback. youtube: captions only.
    # Reading captions directly is an unofficial method that YouTube's terms don't permit.
    source: str = "gemini"
    gemini_model: str = "gemini-3.5-flash-lite"
    languages: list = field(default_factory=lambda: ["en"])
    allow_auto_generated: bool = True
    translate_if_missing: bool = False
    min_words: int = 300
    delay_seconds: float = 2

    def validate(self) -> None:
        if not isinstance(self.languages, list) or not self.languages:
            raise ConfigError('Add at least one transcript language, e.g. "en".')
        if self.source not in TRANSCRIPT_SOURCES:
            raise ConfigError(f"Transcript source must be one of {sorted(TRANSCRIPT_SOURCES)}.")
        if not isinstance(self.gemini_model, str) or not self.gemini_model.strip():
            raise ConfigError("Choose a Gemini model for transcribing, e.g. gemini-3.5-flash-lite.")
        _require_int("Minimum transcript words", self.min_words, 0)
        _require_number("Transcript delay", self.delay_seconds, 0)


@dataclass
class WritingConfig:
    model: str = "gemini-3.8-flash"
    fallback_models: list = field(default_factory=list)
    audience: str = ""
    tone: str = ""
    target_word_count: int = 1200
    include_source_credit: bool = False
    temperature: float = 0.8
    delay_seconds: float = 5
    max_articles_per_run: int = 10
    instructions: str = ""  # comes from the project's article prompt, not the settings form

    def validate(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise ConfigError("Choose a Gemini model, e.g. gemini-3.8-flash.")
        if not isinstance(self.fallback_models, list):
            raise ConfigError("Fallback models must be a list.")
        _require_int("Target word count", self.target_word_count, 200, 6000)
        _require_number("Writing temperature", self.temperature, 0, 2)
        _require_number("Delay between articles", self.delay_seconds, 0)
        _require_int("Articles per run", self.max_articles_per_run, 1)

    @property
    def models(self) -> list:
        """Primary model first, then fallbacks, without duplicates."""
        seen: list = []
        for m in [self.model, *self.fallback_models]:
            if m and m not in seen:
                seen.append(m)
        return seen


@dataclass
class ImagesConfig:
    count: int = 3
    aspect_ratio: str = "16:9"
    temperature: float = 1.0
    instructions: str = ""  # comes from the project's image prompt

    def validate(self) -> None:
        _require_int("Image prompts per article", self.count, 1, 8)
        _require_number("Image temperature", self.temperature, 0, 2)
        if not isinstance(self.aspect_ratio, str) or not re.match(r"^\d+:\d+$", self.aspect_ratio):
            raise ConfigError('Aspect ratio must look like 16:9.')


SECTIONS = {
    "search": SearchConfig,
    "filters": FilterConfig,
    "transcripts": TranscriptConfig,
    "writing": WritingConfig,
    "images": ImagesConfig,
}
PROMPT_FIELDS = {"instructions"}  # stored separately from the settings JSON


@dataclass
class Settings:
    project_id: Optional[int]
    niche_name: str
    search: SearchConfig
    filters: FilterConfig
    transcripts: TranscriptConfig
    writing: WritingConfig
    images: ImagesConfig
    youtube_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    transcript_proxy_url: Optional[str] = None

    def require_gemini_key(self) -> str:
        if not self.gemini_api_key:
            raise ConfigError(
                "The Gemini API key is not set. Add it on the API keys page "
                "(free key: https://aistudio.google.com/apikey)."
            )
        return self.gemini_api_key

    def require_youtube_key(self) -> str:
        if not self.youtube_api_key:
            raise ConfigError("The YouTube API key is not set. Add it on the API keys page (see README).")
        return self.youtube_api_key


# ---- defaults for new projects -------------------------------------------------

DEFAULT_SETTINGS = {
    "search": {
        "keywords": [],
        "max_results_per_keyword": 25,
        "order": "relevance",
        "relevance_language": "en",
        "region_code": "US",
        "safe_search": "moderate",
    },
    "filters": {
        "min_views": 10000,
        "max_age_days": 365,
        "min_duration_minutes": 8,
        "max_duration_minutes": 60,
        "exclude_title_words": ["#shorts", "live stream"],
        "exclude_channels": [],
        "max_videos_per_run": 10,
    },
    "transcripts": {
        "source": "gemini",
        "gemini_model": "gemini-3.5-flash-lite",
        "languages": ["en"],
        "allow_auto_generated": True,
        "translate_if_missing": False,
        "min_words": 400,
        "delay_seconds": 2,
    },
    "writing": {
        "model": "gemini-3.8-flash",
        "fallback_models": ["gemini-3.6-flash", "gemini-3.5-flash-lite"],
        "audience": "",
        "tone": "",
        "target_word_count": 1200,
        "include_source_credit": False,
        "temperature": 0.8,
        "delay_seconds": 5,
        "max_articles_per_run": 10,
    },
    "images": {"count": 3, "aspect_ratio": "16:9", "temperature": 1.0},
}


def default_settings() -> dict:
    return copy.deepcopy(DEFAULT_SETTINGS)


def default_prompt(kind: str) -> str:
    """Starter prompt text for new projects: prompts/article_instructions.md or image_instructions.md."""
    path = DEFAULT_PROMPTS_DIR / f"{kind}_instructions.md"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


# ---- building settings ------------------------------------------------------------

def build_settings(
    data: dict,
    niche_name: str,
    article_instructions: str = "",
    image_instructions: str = "",
    project_id: Optional[int] = None,
    with_keys: bool = True,
) -> Settings:
    """Validate a project's settings dict and return Settings. Missing values use defaults."""
    if not isinstance(data, dict):
        raise ConfigError("Settings must be a mapping of sections.")
    unknown = set(data) - set(SECTIONS)
    if unknown:
        raise ConfigError(f"Unknown settings section(s): {', '.join(sorted(unknown))}.")

    sections = {}
    for name, cls in SECTIONS.items():
        merged = {**DEFAULT_SETTINGS.get(name, {}), **(data.get(name) or {})}
        sections[name] = _build(cls, merged, name)
        sections[name].validate()
    sections["writing"].instructions = (article_instructions or "").strip()
    sections["images"].instructions = (image_instructions or "").strip()

    keys = load_keys() if with_keys else {}
    return Settings(
        project_id=project_id,
        niche_name=niche_name or "Untitled project",
        youtube_api_key=keys.get("YOUTUBE_API_KEY"),
        gemini_api_key=keys.get("GEMINI_API_KEY"),
        transcript_proxy_url=keys.get("TRANSCRIPT_PROXY_URL"),
        **sections,
    )


def settings_to_dict(settings: Settings) -> dict:
    """The JSON-storable part of Settings (prompts are stored separately)."""
    out = {}
    for name in SECTIONS:
        section = asdict(getattr(settings, name))
        out[name] = {k: v for k, v in section.items() if k not in PROMPT_FIELDS}
    return out


def read_yaml_project(path: Path) -> tuple:
    """Read an old-style config.yaml. Returns (name, settings_dict, article_prompt, image_prompt)."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} is not a valid config file.")

    name = str((raw.get("niche") or {}).get("name") or Path(path).stem)
    prompts = {}
    data = {}
    for section in SECTIONS:
        values = dict(raw.get(section) or {})
        inline = values.pop("instructions", "") or ""
        prompt_file = values.pop("instructions_file", None)
        if section in ("writing", "images"):
            kind = "article" if section == "writing" else "image"
            if prompt_file:
                prompt_path = _resolve(prompt_file)
                inline = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else inline
            prompts[kind] = (inline or default_prompt(kind)).strip()
        valid = {f.name for f in fields(SECTIONS[section])}
        data[section] = {k: v for k, v in values.items() if k in valid}
    build_settings(data, name, with_keys=False)  # validate now so bad files fail early
    return name, data, prompts.get("article", ""), prompts.get("image", "")


# ---- API keys (.env) -----------------------------------------------------------------

def load_keys() -> dict:
    """Read keys fresh from .env each time (so changes from the app apply immediately),
    falling back to real environment variables."""
    file_values = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    keys = {}
    for name in KEY_NAMES:
        value = (file_values.get(name) or os.getenv(name) or "").strip()
        keys[name] = value or None
    return keys


def save_keys(updates: dict) -> None:
    """Write keys to .env, keeping any other lines and comments in the file."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    remaining = {k: (v or "").strip() for k, v in updates.items() if k in KEY_NAMES}
    for i, line in enumerate(lines):
        name = line.split("=", 1)[0].strip()
        if name in remaining:
            lines[i] = f"{name}={remaining.pop(name)}"
    lines += [f"{name}={value}" for name, value in remaining.items()]
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(ENV_PATH, 0o600)  # keys readable only by you
    except OSError:
        pass


# ---- helpers -----------------------------------------------------------------------------

def _build(cls, data: Any, section: str):
    valid = {f.name for f in fields(cls)} - PROMPT_FIELDS
    unknown = set(data) - valid
    if unknown:
        raise ConfigError(f"Unknown setting(s) in '{section}': {', '.join(sorted(unknown))}.")
    try:
        return cls(**data)
    except TypeError as exc:
        raise ConfigError(f"Invalid settings in '{section}': {exc}") from exc


def _resolve(value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else RESOURCE_ROOT / p


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
