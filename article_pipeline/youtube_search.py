"""Step 1: discover videos with the YouTube Data API v3 and filter them."""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from .config import Settings
from .db import Database
from .logging_setup import get_logger

API_BASE = "https://www.googleapis.com/youtube/v3"
log = get_logger()


class YouTubeAPIError(Exception):
    """A YouTube API problem that should stop the run (bad key, quota, network)."""


@dataclass
class VideoInfo:
    video_id: str
    title: str
    channel_title: str
    channel_id: str
    published_at: str
    duration_seconds: int
    view_count: int
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    description: str = ""
    tags: list = field(default_factory=list)
    keyword: str = ""

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["url"] = self.url
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "VideoInfo":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in allowed})


class YouTubeClient:
    def __init__(self, api_key: str, timeout: int = 20, max_retries: int = 3):
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.quota_used = 0

    def search_video_ids(
        self,
        keyword: str,
        max_results: int,
        published_after: datetime,
        order: str,
        relevance_language: Optional[str],
        region_code: Optional[str],
        safe_search: str,
        video_duration: str,
    ) -> list:
        ids: list = []
        page_token = None
        while len(ids) < max_results:
            params = {
                "part": "id",
                "type": "video",
                "q": keyword,
                "maxResults": min(50, max_results - len(ids)),
                "order": order,
                "publishedAfter": published_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "safeSearch": safe_search,
                "videoDuration": video_duration,
            }
            if relevance_language:
                params["relevanceLanguage"] = relevance_language
            if region_code:
                params["regionCode"] = region_code
            if page_token:
                params["pageToken"] = page_token

            data = self._get("search", params, cost=100)
            ids.extend(
                item["id"]["videoId"]
                for item in data.get("items", [])
                if item.get("id", {}).get("videoId")
            )
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return ids

    def video_details(self, video_ids: list) -> list:
        """Fetch title, stats and duration for up to any number of IDs (50 per call)."""
        results = []
        for start in range(0, len(video_ids), 50):
            chunk = video_ids[start:start + 50]
            data = self._get(
                "videos",
                {"part": "snippet,statistics,contentDetails", "id": ",".join(chunk)},
                cost=1,
            )
            results.extend(data.get("items", []))
        return results

    def _get(self, endpoint: str, params: dict, cost: int) -> dict:
        url = f"{API_BASE}/{endpoint}"
        params = {**params, "key": self.api_key}
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt == self.max_retries:
                    raise YouTubeAPIError(f"Network error talking to YouTube: {exc}") from exc
                log.warning("Network error (%s), retrying in %ds...", exc, 2 ** attempt)
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 200:
                self.quota_used += cost
                return resp.json()

            reason, message = _parse_error(resp)
            transient = resp.status_code >= 500 or reason in ("rateLimitExceeded", "backendError")
            if transient and attempt < self.max_retries:
                log.warning("YouTube API %s (%s), retrying in %ds...", resp.status_code, reason, 2 ** attempt)
                time.sleep(2 ** attempt)
                continue
            raise YouTubeAPIError(_friendly_error(resp.status_code, reason, message))
        raise YouTubeAPIError("YouTube API request failed after retries.")


def _parse_error(resp: requests.Response) -> tuple:
    try:
        err = resp.json().get("error", {})
        reason = (err.get("errors") or [{}])[0].get("reason", "")
        return reason, err.get("message", resp.text[:200])
    except ValueError:
        return "", resp.text[:200]


def _friendly_error(status: int, reason: str, message: str) -> str:
    if reason in ("quotaExceeded", "dailyLimitExceeded"):
        return (
            "YouTube API daily quota is used up. It resets at midnight Pacific time. "
            "Lower search.keywords or max_results_per_keyword to use less quota."
        )
    if reason in ("keyInvalid", "badRequest") and "API key" in message:
        return "Your YouTube API key is not valid. Paste it again on the API keys page."
    if reason == "accessNotConfigured" or "has not been used in project" in message:
        return (
            "YouTube Data API v3 is not enabled for your Google Cloud project. "
            "Enable it in the Google Cloud Console (see README), wait a minute, then retry."
        )
    if reason in ("forbidden", "ipRefererBlocked") or "API_KEY_HTTP_REFERRER_BLOCKED" in message:
        return (
            "Your API key is restricted in a way that blocks this script. In Google Cloud Console, "
            "set the key's application restriction to 'None' and the API restriction to YouTube Data API v3."
        )
    return f"YouTube API error {status} ({reason or 'unknown'}): {message}"


_DURATION_RE = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$"
)


def parse_iso_duration(value: str) -> int:
    """'PT1H2M3S' -> 3723 seconds. Unknown formats return 0."""
    match = _DURATION_RE.match(value or "")
    if not match:
        return 0
    parts = {k: int(v) if v else 0 for k, v in match.groupdict().items()}
    return parts["d"] * 86400 + parts["h"] * 3600 + parts["m"] * 60 + parts["s"]


def _api_duration_bucket(min_minutes: float, max_minutes: float) -> str:
    """Let YouTube pre-filter by length when the configured range fits one of its buckets.

    YouTube buckets: short < 4 min, medium 4-20 min, long > 20 min.
    """
    if min_minutes >= 20:
        return "long"
    if max_minutes <= 4:
        return "short"
    if min_minutes >= 4 and max_minutes <= 20:
        return "medium"
    return "any"


def _to_video(item: dict, keyword: str) -> VideoInfo:
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})

    def as_int(value):
        return int(value) if value is not None else None

    return VideoInfo(
        video_id=item["id"],
        title=snippet.get("title", ""),
        channel_title=snippet.get("channelTitle", ""),
        channel_id=snippet.get("channelId", ""),
        published_at=snippet.get("publishedAt", ""),
        duration_seconds=parse_iso_duration(item.get("contentDetails", {}).get("duration", "")),
        view_count=as_int(stats.get("viewCount")) or 0,
        like_count=as_int(stats.get("likeCount")),
        comment_count=as_int(stats.get("commentCount")),
        description=snippet.get("description", ""),
        tags=snippet.get("tags", []),
        keyword=keyword,
    )


def _rejection_reason(item: dict, video: VideoInfo, settings: Settings, cutoff: datetime) -> Optional[str]:
    f = settings.filters
    if item.get("snippet", {}).get("liveBroadcastContent", "none") != "none":
        return "live or upcoming"
    if video.view_count < f.min_views:
        return "too few views"
    if video.duration_seconds < f.min_duration_minutes * 60:
        return "too short"
    if video.duration_seconds > f.max_duration_minutes * 60:
        return "too long"
    try:
        published = datetime.strptime(video.published_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if published < cutoff:
            return "too old"
    except ValueError:
        pass
    title = video.title.lower()
    if any(word.lower() in title for word in f.exclude_title_words):
        return "excluded title word"
    excluded_channels = {c.lower() for c in f.exclude_channels}
    if video.channel_title.lower() in excluded_channels or video.channel_id.lower() in excluded_channels:
        return "excluded channel"
    return None


def discover_videos(settings: Settings, client: YouTubeClient, db: Database) -> list:
    """Search every keyword, drop already-tracked and filtered-out videos, return new VideoInfo list."""
    s, f = settings.search, settings.filters
    cutoff = datetime.now(timezone.utc) - timedelta(days=f.max_age_days)
    duration_bucket = _api_duration_bucket(f.min_duration_minutes, f.max_duration_minutes)

    keyword_for: dict = {}
    for keyword in s.keywords:
        ids = client.search_video_ids(
            keyword=keyword,
            max_results=s.max_results_per_keyword,
            published_after=cutoff,
            order=s.order,
            relevance_language=s.relevance_language,
            region_code=s.region_code,
            safe_search=s.safe_search,
            video_duration=duration_bucket,
        )
        new = [i for i in ids if i not in keyword_for]
        for vid in new:
            keyword_for[vid] = keyword
        log.info("Search '%s': %d results (%d new to this run)", keyword, len(ids), len(new))

    if not keyword_for:
        log.info("No videos found for any keyword.")
        return []

    tracked = db.tracked(settings.project_id, keyword_for)
    # A transcript left 'pending' means a previous run was interrupted mid-video: process it again.
    candidates = [vid for vid in keyword_for if tracked.get(vid, ("transcript", "pending")) == ("transcript", "pending")]
    skipped = Counter("failed" if status == "failed" else "done" for vid, (_, status) in tracked.items()
                      if vid not in candidates)
    if skipped:
        log.info(
            "Skipping %d already processed and %d previously failed (use 'retry' for failed ones).",
            skipped.get("done", 0), skipped.get("failed", 0),
        )

    kept, rejections = [], Counter()
    for item in client.video_details(candidates):
        video = _to_video(item, keyword_for.get(item["id"], ""))
        reason = _rejection_reason(item, video, settings, cutoff)
        if reason:
            rejections[reason] += 1
            log.debug("Filtered out %s (%s): %s", video.video_id, reason, video.title)
        else:
            kept.append(video)

    if rejections:
        log.info("Filtered out %d: %s", sum(rejections.values()),
                 ", ".join(f"{n} {r}" for r, n in rejections.most_common()))

    kept.sort(key=lambda v: v.view_count, reverse=True)
    if len(kept) > f.max_videos_per_run:
        log.info("Keeping the top %d of %d by views (filters.max_videos_per_run).",
                 f.max_videos_per_run, len(kept))
        kept = kept[: f.max_videos_per_run]

    log.info("%d new video(s) passed the filters. Approx. quota used: %d units.",
             len(kept), client.quota_used)
    return kept
