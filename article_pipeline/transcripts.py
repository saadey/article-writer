"""Step 2: fetch a video's transcript and clean it into readable text."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional

from youtube_transcript_api import (
    AgeRestricted,
    CouldNotRetrieveTranscript,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)
from youtube_transcript_api.proxies import GenericProxyConfig

from .config import TranscriptConfig

# Caption noise like [Music], [Applause], (laughs)
_NOISE_RE = re.compile(r"[\[(](?:music|applause|laughter|laughs|inaudible|silence)[\])]", re.I)
PARAGRAPH_SECONDS = 60


class TranscriptError(Exception):
    """This video has no usable transcript. Mark it failed and continue."""


class TranscriptBlockedError(TranscriptError):
    """YouTube is blocking this IP, so every other caption request would fail too."""


def make_transcript_api(proxy_url: Optional[str]) -> YouTubeTranscriptApi:
    if proxy_url:
        return YouTubeTranscriptApi(proxy_config=GenericProxyConfig(http_url=proxy_url, https_url=proxy_url))
    return YouTubeTranscriptApi()


def fetch_transcript(api: YouTubeTranscriptApi, video_id: str, cfg: TranscriptConfig) -> dict:
    """Return {'segments': [...], 'language_code', 'language', 'is_generated', 'translated'}."""
    try:
        transcript_list = api.list(video_id)
        translated = False
        try:
            if cfg.allow_auto_generated:
                transcript = transcript_list.find_transcript(cfg.languages)
            else:
                transcript = transcript_list.find_manually_created_transcript(cfg.languages)
        except NoTranscriptFound:
            if not cfg.translate_if_missing:
                raise
            source = next(
                (t for t in transcript_list
                 if t.is_translatable and (cfg.allow_auto_generated or not t.is_generated)),
                None,
            )
            if source is None:
                raise
            transcript = source.translate(cfg.languages[0])
            translated = True

        fetched = transcript.fetch()
    except TranscriptsDisabled:
        raise TranscriptError("Captions are disabled for this video.")
    except NoTranscriptFound:
        kind = "" if cfg.allow_auto_generated else "manually created "
        raise TranscriptError(f"No {kind}transcript in languages {cfg.languages}.")
    except VideoUnavailable:
        raise TranscriptError("Video is unavailable (deleted or private).")
    except AgeRestricted:
        raise TranscriptError("Video is age-restricted; transcripts need a signed-in session.")
    except RequestBlocked:
        raise TranscriptBlockedError(
            "YouTube is blocking transcript requests from your IP (too many requests, or a "
            "cloud/VPN IP). Wait a few hours, switch network, or add a transcript proxy on the API keys page."
        )
    except CouldNotRetrieveTranscript as exc:
        cause = (exc.cause or "").strip().splitlines()
        raise TranscriptError(cause[0] if cause else f"Could not retrieve transcript: {type(exc).__name__}")

    segments = [
        {"start": round(s.start, 2), "duration": round(s.duration, 2), "text": s.text}
        for s in fetched.snippets
    ]
    return {
        "segments": segments,
        "language_code": fetched.language_code,
        "language": fetched.language,
        "is_generated": fetched.is_generated,
        "translated": translated,
    }


def segments_to_text(segments: list) -> str:
    """Join caption lines into readable paragraphs of roughly one minute each."""
    paragraphs, current, paragraph_start = [], [], 0.0
    for seg in segments:
        text = _NOISE_RE.sub(" ", seg["text"].replace("\n", " "))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if current and seg["start"] - paragraph_start >= PARAGRAPH_SECONDS:
            paragraphs.append(" ".join(current))
            current, paragraph_start = [], seg["start"]
        if not current:
            paragraph_start = seg["start"]
        current.append(text)
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)


def slugify(text: str, max_length: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_length].rstrip("-") or "video"


def get_transcript(api: YouTubeTranscriptApi, video_id: str, cfg: TranscriptConfig) -> tuple:
    """Fetch and clean a transcript. Returns (text, segments, info)."""
    result = fetch_transcript(api, video_id, cfg)
    text = segments_to_text(result["segments"])
    word_count = len(text.split())
    if word_count < cfg.min_words:
        raise TranscriptError(
            f"Transcript too short ({word_count} words; the minimum is {cfg.min_words})."
        )
    info = {
        "language_code": result["language_code"],
        "language": result["language"],
        "is_generated": result["is_generated"],
        "translated": result["translated"],
        "source": "youtube",
        "word_count": word_count,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return text, result["segments"], info


GEMINI_TRANSCRIBE_PROMPT = (
    "Transcribe everything that is said in this video, word for word, in the language it is spoken. "
    "Write it as plain text paragraphs, starting a new paragraph roughly every minute or when the topic "
    "changes. No timestamps, speaker labels, headings, descriptions of visuals or commentary. "
    "Return only the transcript."
)


def gemini_transcript(client, video_id: str, cfg: TranscriptConfig) -> tuple:
    """Have Gemini watch the public YouTube video and transcribe it. Returns (text, segments, info).

    Google fetches the video, so this works even when YouTube blocks your own IP.
    Low resolution and few frames keep it to roughly 30-40 tokens per second of video.
    """
    parts = [
        {"fileData": {"fileUri": f"https://www.youtube.com/watch?v={video_id}"}, "videoMetadata": {"fps": 0.1}},
        {"text": GEMINI_TRANSCRIBE_PROMPT},
    ]
    text, model = client.generate_text(parts, {"mediaResolution": "MEDIA_RESOLUTION_LOW", "temperature": 0})
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    word_count = len(text.split())
    if word_count < cfg.min_words:
        raise TranscriptError(
            f"Transcript too short ({word_count} words; the minimum is {cfg.min_words})."
        )
    info = {
        "language_code": "",
        "language": f"Transcribed by {model}",
        "source": "gemini",
        "is_generated": True,
        "translated": False,
        "word_count": word_count,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return text, [], info
