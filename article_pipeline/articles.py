"""Step 3: rewrite a saved transcript into an article with the Gemini API."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from .config import WritingConfig
from .logging_setup import get_logger

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
log = get_logger()

MAX_RATE_LIMIT_WAITS = 5
MAX_RATE_LIMIT_SLEEP = 90


class ArticleError(Exception):
    """This article could not be written. Mark the video failed and continue."""


class GeminiError(Exception):
    """A Gemini problem that should stop the run (bad key, API disabled, network)."""


class GeminiQuotaError(GeminiError):
    """Every configured model has used up its free daily quota."""


class _ModelUnavailable(Exception):
    """This model can't be used right now (daily quota hit or model not found); try the next one."""


ARTICLE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING", "description": "Compelling article headline, under 80 characters."},
        "subtitle": {"type": "STRING", "description": "One-sentence subtitle that expands on the headline."},
        "body_markdown": {
            "type": "STRING",
            "description": "Full article body in Markdown, using ## subheadings. Do not repeat the title.",
        },
    },
    "required": ["title", "subtitle", "body_markdown"],
}


class GeminiClient:
    def __init__(self, api_key: str, models: list, timeout: int = 180, max_retries: int = 3):
        self.api_key = api_key
        self.models = list(models)
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    def generate_json(self, system: str, prompt: str, schema: dict, temperature: float) -> tuple:
        """Return (parsed_json, model_used), falling back through models on quota/availability."""
        while self.models:
            model = self.models[0]
            try:
                return self._generate(model, system, prompt, schema, temperature), model
            except _ModelUnavailable as exc:
                log.warning("Model %s unavailable: %s", model, exc)
                self.models.pop(0)
                if self.models:
                    log.warning("Switching to fallback model %s.", self.models[0])
        raise GeminiQuotaError(
            "All configured Gemini models have hit their free daily limit (or are unavailable). "
            "Limits reset at midnight Pacific time; then run 'python main.py write'."
        )

    def check_model(self, model: str) -> None:
        """Cheap request that confirms the key works and the model exists."""
        resp = self._request("GET", f"{API_BASE}/models/{model}")
        if resp.status_code == 404:
            raise GeminiError(f"Model '{model}' was not found. Check writing.model in config.yaml.")
        if resp.status_code != 200:
            self._raise_fatal(resp)

    def _generate(self, model: str, system: str, prompt: str, schema: dict, temperature: float) -> dict:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        url = f"{API_BASE}/models/{model}:generateContent"
        rate_limit_waits = server_errors = 0
        while True:
            resp = self._request("POST", url, json=body)
            if resp.status_code == 200:
                return _parse_response(resp.json())

            status, message, details = _error_parts(resp)
            if resp.status_code == 429:
                if _is_daily_quota(details, message):
                    raise _ModelUnavailable("free daily quota used up")
                rate_limit_waits += 1
                if rate_limit_waits > MAX_RATE_LIMIT_WAITS:
                    raise _ModelUnavailable("still rate limited after several waits")
                wait = min(_retry_delay(details) or 30, MAX_RATE_LIMIT_SLEEP)
                log.info("  Gemini per-minute limit reached; waiting %ds...", wait)
                time.sleep(wait)
                continue
            if resp.status_code == 404:
                raise _ModelUnavailable(f"model not found ({message})")
            if resp.status_code >= 500:
                server_errors += 1
                if server_errors >= self.max_retries:
                    raise ArticleError(f"Gemini server error {resp.status_code}: {message}")
                wait = 10 * server_errors
                log.warning("  Gemini %s (%s); retrying in %ds...", resp.status_code, status, wait)
                time.sleep(wait)
                continue
            self._raise_fatal(resp)

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        headers = {"x-goog-api-key": self.api_key}
        for attempt in range(1, self.max_retries + 1):
            try:
                return self.session.request(method, url, headers=headers, timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                if attempt == self.max_retries:
                    raise GeminiError(f"Network error talking to Gemini: {exc}") from exc
                log.warning("  Network error (%s), retrying in %ds...", exc, 5 * attempt)
                time.sleep(5 * attempt)
        raise GeminiError("Gemini request failed after retries.")

    @staticmethod
    def _raise_fatal(resp: requests.Response) -> None:
        status, message, details = _error_parts(resp)
        reasons = {d.get("reason", "") for d in details}
        if "API_KEY_INVALID" in reasons or "API key not valid" in message:
            raise GeminiError("Your GEMINI_API_KEY is not valid. Check the value in .env (no quotes or spaces).")
        if "SERVICE_DISABLED" in reasons or "has not been used in project" in message:
            raise GeminiError(
                "The Gemini API is not enabled for this key's project. Create the key at "
                "https://aistudio.google.com/apikey, which enables it automatically."
            )
        if resp.status_code == 403:
            raise GeminiError(f"Gemini refused the request (403 {status}): {message}")
        if resp.status_code == 400 and "location is not supported" in message.lower():
            raise GeminiError("The Gemini API free tier is not available in your country/region.")
        raise ArticleError(f"Gemini error {resp.status_code} ({status}): {message}")


def _error_parts(resp: requests.Response) -> tuple:
    try:
        err = resp.json().get("error", {})
        return err.get("status", ""), err.get("message", ""), err.get("details", []) or []
    except ValueError:
        return "", resp.text[:300], []


def _is_daily_quota(details: list, message: str) -> bool:
    for d in details:
        for v in d.get("violations", []) or []:
            if "PerDay" in v.get("quotaId", ""):
                return True
    return "per day" in message.lower()


def _retry_delay(details: list) -> Optional[int]:
    for d in details:
        delay = d.get("retryDelay")
        if delay:
            match = re.match(r"(\d+(?:\.\d+)?)s", delay)
            if match:
                return int(float(match.group(1))) + 1
    return None


def _parse_response(data: dict) -> dict:
    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise ArticleError(f"Gemini blocked the request ({block}).")
    candidates = data.get("candidates") or []
    if not candidates:
        raise ArticleError("Gemini returned no answer.")
    candidate = candidates[0]
    finish = candidate.get("finishReason", "STOP")
    text = "".join(
        p.get("text", "") for p in (candidate.get("content") or {}).get("parts", []) if not p.get("thought")
    ).strip()
    if finish == "MAX_TOKENS":
        raise ArticleError("Gemini's answer was cut off (too long). Try a lower writing.target_word_count.")
    if finish not in ("STOP", "FINISH_REASON_UNSPECIFIED") and not text:
        raise ArticleError(f"Gemini stopped without an answer (reason: {finish}).")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ArticleError(f"Gemini returned invalid JSON: {exc}") from exc


def build_system_prompt(cfg: WritingConfig, niche_name: str) -> str:
    return f"""You are a skilled newsletter writer for a Substack publication about {niche_name}.

Audience: {cfg.audience}
Tone: {cfg.tone}
Target length: about {cfg.target_word_count} words for the article body.

Instructions:
{cfg.instructions.strip()}

General rules:
- Write an original article based on the ideas in the source material. Do not copy its sentences.
- Fix transcription errors and ignore filler, sponsor reads, and calls to like/subscribe.
- Do not invent statistics, quotes, or facts that are not supported by the source.
- Return JSON with the fields title, subtitle, and body_markdown."""


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
        raise ArticleError(f"Missing {transcript_path.name} in {folder.name}; retry the transcript step.")
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
        raise ArticleError("Gemini's answer was missing a title or article body.")

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

    if metadata:
        metadata["article"] = {
            "title": title,
            "subtitle": subtitle,
            "word_count": word_count,
            "model": model,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return article_path
