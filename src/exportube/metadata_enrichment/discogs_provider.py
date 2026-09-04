"""Discogs enrichment -- a second, independent metadata_enrichment
provider (spec section 7: "design the application so additional metadata
providers can be plugged in later... Do not make the entire application
dependent upon one provider").

Metadata-only, same as MusicBrainz: this queries Discogs' release search
API with an artist/track (and optional album) string and never downloads
audio. Requires a free Discogs personal access token (see
docs/PRIVACY.md and .env.example) -- entirely optional. Without a token
configured, this provider is simply not instantiated (see cli.py identify
command) and the pipeline runs on MusicBrainz alone, exactly as before;
nothing else changes.

Scope note: Discogs' search endpoint returns release-level metadata
(title, year, label, format, country, genre/style, a `master_id`) but not
per-track duration -- getting that would require a second API call per
candidate release (`GET /releases/{id}`), which roughly doubles Discogs
API traffic for a second-tier corroborating source. Left as a future
enhancement (see docs/ARCHITECTURE.md); v1 candidates from this provider carry no
recording_duration_seconds and so never contribute duration_match
evidence, only text/identity/cross-source-agreement evidence via
confidence/engine.py.
"""
from __future__ import annotations

import hashlib
import logging
import time

logger = logging.getLogger(__name__)

from exportube.metadata_enrichment.base import MusicMetadataProvider

SEARCH_URL = "https://api.discogs.com/database/search"


class DiscogsProvider(MusicMetadataProvider):
    name = "discogs"

    def __init__(self, token: str, user_agent: str, cache=None, requests_per_minute: float = 55.0):
        self.token = token
        self.user_agent = user_agent
        self.cache = cache
        self._min_interval = 60.0 / requests_per_minute if requests_per_minute > 0 else 0.0
        self._last_request_at = 0.0

    def _cached_call(self, key: str, fn):
        if self.cache is None:
            return fn()
        return self.cache.get_or_fetch("discogs", key, fn)

    def _rate_limit(self) -> None:
        if self._min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_at = time.monotonic()

    def search_recordings(self, artist: str | None, track: str, album: str | None = None,
                           duration_seconds: float | None = None, limit: int = 5) -> list[dict]:
        import requests

        if not track or not track.strip():
            return []

        params = {"track": track, "type": "release", "per_page": str(limit), "token": self.token}
        if artist:
            params["artist"] = artist
        if album:
            params["release_title"] = album

        cache_key = hashlib.sha256(repr(sorted(params.items())).encode()).hexdigest()

        def _fetch():
            self._rate_limit()
            try:
                resp = requests.get(
                    SEARCH_URL, params=params,
                    headers={"User-Agent": self.user_agent},
                    timeout=10,
                )
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                logger.warning("Discogs search failed for %r: %s", params, e)
                return {"results": []}

        response = self._cached_call(cache_key, _fetch)
        return self._normalize(response.get("results", []), artist)

    @staticmethod
    def _normalize(results: list[dict], fallback_artist: str | None) -> list[dict]:
        out = []
        for r in results:
            title = r.get("title") or ""
            # Discogs search results title-case as "Artist - Release Title";
            # split it back out when possible, else fall back to the query artist.
            if " - " in title:
                artist, release_title = title.split(" - ", 1)
            else:
                artist, release_title = fallback_artist, title

            year = r.get("year")
            out.append({
                "musicbrainz_recording_id": None,
                "artist": artist,
                "track": release_title,
                "album": release_title,
                "release_group": None,
                "release_type": (r.get("format") or [None])[0] if isinstance(r.get("format"), list) else None,
                "release_country": r.get("country"),
                "release_date": str(year) if year else None,
                "isrc": None,
                "recording_duration_seconds": None,  # see module docstring
                "musicbrainz_release_id": None,
                "musicbrainz_release_group_id": None,
                "musicbrainz_artist_id": None,
                "score": 0,
                "discogs_release_id": r.get("id"),
            })
        return out
