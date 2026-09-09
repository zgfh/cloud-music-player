"""MusicBrainz recording text search with polite global rate limiting."""

from __future__ import annotations

import asyncio
import threading
import time
from difflib import SequenceMatcher

import requests


class MusicBrainzUnavailableError(RuntimeError):
    """MusicBrainz 限流或临时不可用。"""


class MusicBrainzService:
    SEARCH_URL = "https://musicbrainz.org/ws/2/recording/"
    USER_AGENT = (
        "NextCloudMusicPlayer/0.1.0 " "(https://github.com/zgfh/cloud-music-player)"
    )
    _rate_lock = threading.Lock()
    _last_request_at = 0.0
    MAX_ATTEMPTS = 3
    RETRYABLE_STATUS = {429, 500, 502, 503, 504}

    @staticmethod
    def _escape(value: str) -> str:
        return str(value or "").replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _similarity(left: str, right: str) -> int:
        def normalise(value: str) -> str:
            return " ".join(sorted(str(value or "").casefold().split()))

        return round(
            SequenceMatcher(None, normalise(left), normalise(right)).ratio() * 100
        )

    @classmethod
    def _wait_for_rate_limit(cls) -> None:
        with cls._rate_lock:
            wait = 1.05 - (time.monotonic() - cls._last_request_at)
            if wait > 0:
                time.sleep(wait)
            cls._last_request_at = time.monotonic()

    async def search(self, artist: str, title: str, limit: int = 5) -> list[dict]:
        return await asyncio.to_thread(self._search_sync, artist, title, limit)

    def _search_sync(self, artist: str, title: str, limit: int = 5) -> list[dict]:
        artist = str(artist or "").strip()
        title = str(title or "").strip()
        parts = []
        if title:
            parts.append(f'recording:"{self._escape(title)}"')
        if artist and artist != "未知艺术家":
            parts.append(f'artist:"{self._escape(artist)}"')
        query = " AND ".join(parts) or title or artist
        if not query:
            return []

        response = None
        for attempt in range(self.MAX_ATTEMPTS):
            self._wait_for_rate_limit()
            try:
                response = requests.get(
                    self.SEARCH_URL,
                    params={
                        "query": query,
                        "fmt": "json",
                        "limit": max(1, min(limit, 10)),
                    },
                    headers={
                        "User-Agent": self.USER_AGENT,
                        "Accept": "application/json",
                    },
                    timeout=12,
                )
                response.raise_for_status()
                break
            except requests.RequestException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                retryable = status in self.RETRYABLE_STATUS or status is None
                if not retryable:
                    raise
                if attempt + 1 >= self.MAX_ATTEMPTS:
                    raise MusicBrainzUnavailableError(
                        "MusicBrainz 服务暂不可用，请稍后再试"
                    ) from exc
                retry_after = getattr(
                    getattr(exc, "response", None), "headers", {}
                ).get("Retry-After")
                try:
                    delay = min(float(retry_after), 5.0)
                except (TypeError, ValueError):
                    delay = 0.75 * (2**attempt)
                time.sleep(delay)

        if response is None:
            return []
        results = []
        guess = f"{artist} {title}".strip()
        for recording in response.json().get("recordings", []):
            credits = recording.get("artist-credit") or []
            artist_name = "".join(
                f"{credit.get('name') or (credit.get('artist') or {}).get('name', '')}"
                f"{credit.get('joinphrase', '')}"
                for credit in credits
            ).strip()
            releases = recording.get("releases") or []
            release = releases[0] if releases else {}
            date = recording.get("first-release-date") or release.get("date", "")
            result_text = f"{artist_name} {recording.get('title', '')}".strip()
            mb_score = int(recording.get("score", 0) or 0)
            fuzzy_score = self._similarity(guess, result_text)
            results.append(
                {
                    "artist": artist_name,
                    "title": recording.get("title", ""),
                    "album": release.get("title", ""),
                    "year": str(date)[:4] if date else "",
                    "mbid": recording.get("id", ""),
                    "mb_score": mb_score,
                    "fuzzy_score": fuzzy_score,
                    "confidence": round((mb_score + fuzzy_score) / 2),
                }
            )
        return sorted(results, key=lambda item: item["confidence"], reverse=True)
