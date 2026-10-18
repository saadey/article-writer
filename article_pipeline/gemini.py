"""Shared Gemini API client: rate-limit waits, model fallback, friendly errors."""

from __future__ import annotations

import json
import re
import time
from typing import Optional

import requests

from .logging_setup import get_logger

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
log = get_logger()

MAX_RATE_LIMIT_WAITS = 5
MAX_RATE_LIMIT_SLEEP = 90


class GenerationError(Exception):
    """This one item could not be generated. Mark the video failed and continue."""


class GeminiError(Exception):
    """A Gemini problem that should stop the run (bad key, API disabled, network)."""


class GeminiQuotaError(GeminiError):
    """Every configured model has used up its free daily quota."""


class _ModelUnavailable(Exception):
    """This model can't be used right now (daily quota hit or model not found); try the next one."""


class GeminiClient:
    def __init__(self, api_key: str, models: list, timeout: int = 180, max_retries: int = 3):
        self.api_key = api_key
        self.models = list(models)
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.sleep = time.sleep  # the app swaps this for a cancellable wait

    def generate_json(self, system: str, prompt: str, schema: dict, temperature: float) -> tuple:
        """Return (parsed_json, model_used), falling back through models on quota/availability."""
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        data, model = self._with_fallback(body)
        text = _response_text(data)
        try:
            return json.loads(text), model
        except ValueError as exc:
            raise GenerationError(f"Gemini returned invalid JSON: {exc}") from exc

    def generate_text(self, parts: list, generation_config: dict) -> tuple:
        """Send parts (text, YouTube links...) and return (text, model_used)."""
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": generation_config}
        data, model = self._with_fallback(body)
        return _response_text(data), model

    def _with_fallback(self, body: dict) -> tuple:
        while self.models:
            model = self.models[0]
            try:
                return self._generate(model, body), model
            except _ModelUnavailable as exc:
                log.warning("Model %s unavailable: %s", model, exc)
                self.models.pop(0)
                if self.models:
                    log.warning("Switching to fallback model %s.", self.models[0])
        raise GeminiQuotaError(
            "All configured Gemini models have hit their free daily limit (or are unavailable). "
            "Limits reset at midnight Pacific time; then click Continue writing on the dashboard."
        )

    def check_model(self, model: str) -> None:
        """Cheap request that confirms the key works and the model exists."""
        resp = self._request("GET", f"{API_BASE}/models/{model}")
        if resp.status_code == 404:
            raise GeminiError(f"Model '{model}' was not found. Pick a current model in Settings.")
        if resp.status_code != 200:
            self._raise_fatal(resp)

    def _generate(self, model: str, body: dict) -> dict:
        url = f"{API_BASE}/models/{model}:generateContent"
        rate_limit_waits = server_errors = 0
        while True:
            resp = self._request("POST", url, json=body)
            if resp.status_code == 200:
                return resp.json()

            status, message, details = _error_parts(resp)
            if resp.status_code == 429:
                if _is_daily_quota(details, message):
                    raise _ModelUnavailable("free daily quota used up")
                rate_limit_waits += 1
                if rate_limit_waits > MAX_RATE_LIMIT_WAITS:
                    raise _ModelUnavailable("still rate limited after several waits")
                wait = min(_retry_delay(details) or 30, MAX_RATE_LIMIT_SLEEP)
                log.info("  Gemini per-minute limit reached; waiting %ds...", wait)
                self.sleep(wait)
                continue
            if resp.status_code == 404:
                raise _ModelUnavailable(f"model not found ({message})")
            if resp.status_code >= 500:
                server_errors += 1
                if server_errors >= self.max_retries:
                    raise GenerationError(f"Gemini server error {resp.status_code}: {message}")
                wait = 10 * server_errors
                log.warning("  Gemini %s (%s); retrying in %ds...", resp.status_code, status, wait)
                self.sleep(wait)
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
                self.sleep(5 * attempt)
        raise GeminiError("Gemini request failed after retries.")

    @staticmethod
    def _raise_fatal(resp: requests.Response) -> None:
        status, message, details = _error_parts(resp)
        reasons = {d.get("reason", "") for d in details}
        if "API_KEY_INVALID" in reasons or "API key not valid" in message:
            raise GeminiError("Your Gemini API key is not valid. Paste it again on the API keys page.")
        if "SERVICE_DISABLED" in reasons or "has not been used in project" in message:
            raise GeminiError(
                "The Gemini API is not enabled for this key's project. Create the key at "
                "https://aistudio.google.com/apikey, which enables it automatically."
            )
        if resp.status_code == 403:
            raise GeminiError(f"Gemini refused the request (403 {status}): {message}")
        if resp.status_code == 400 and "location is not supported" in message.lower():
            raise GeminiError("The Gemini API free tier is not available in your country/region.")
        raise GenerationError(f"Gemini error {resp.status_code} ({status}): {message}")


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


def _response_text(data: dict) -> str:
    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise GenerationError(f"Gemini blocked the request ({block}).")
    candidates = data.get("candidates") or []
    if not candidates:
        raise GenerationError("Gemini returned no answer.")
    candidate = candidates[0]
    finish = candidate.get("finishReason", "STOP")
    text = "".join(
        p.get("text", "") for p in (candidate.get("content") or {}).get("parts", []) if not p.get("thought")
    ).strip()
    if finish == "MAX_TOKENS":
        raise GenerationError("Gemini's answer was cut off (too long). Try a lower writing.target_word_count.")
    if finish not in ("STOP", "FINISH_REASON_UNSPECIFIED") and not text:
        raise GenerationError(f"Gemini stopped without an answer (reason: {finish}).")
    return text
