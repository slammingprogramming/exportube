"""MusicBrainz enrichment via musicbrainzngs.

MusicBrainz is free, requires no API key, but mandates a descriptive
User-Agent and a 1 request/second rate limit for unauthenticated use --
both configured here from config.musicbrainz_user_agent() /
rate_limits.musicbrainz_requests_per_second. Every query and its raw
response is cached (namespace "musicbrainz") so re-running `identify`
after a crash, or retuning confidence weights, never re-queries the
network for a (artist, track, album, duration) combination already seen.

Normalized candidate dict shape returned by search_recordings /
lookup_by_isrc (one dict per MusicBrainz *release* the recording appears
on, since release/album context varies per release even for the same
recording):

  {
    "musicbrainz_recording_id": str,
    "artist": str,                 # joined artist-credit phrase
    "track": str,                  # recording title
    "recording_duration_seconds": float | None,
    "isrc": str | None,
    "musicbrainz_release_id": str | None,
    "album": str | None,           # release title
    "musicbrainz_release_group_id": str | None,
    "release_group": str | None,
    "release_type": str | None,    # Album | Single | EP | ...
    "release_country": str | None,
    "release_date": str | None,    # earliest known release date (YYYY[-MM[-DD]])
    "musicbrainz_artist_id": str | None,
    "score": int,                  # MusicBrainz's own search relevance score, 0-100
  }
"""
from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger(__name__)

from exportube.metadata_enrichment.base import MusicMetadataProvider

_client_configured = False


def _ensure_client(app: str, version: str, contact: str, rate_limit_per_sec: float) -> None:
    global _client_configured
    import musicbrainzngs

    if _client_configured:
        return
    musicbrainzngs.set_useragent(app, version, contact)
    interval = 1.0 / rate_limit_per_sec if rate_limit_per_sec > 0 else 1.0
    musicbrainzngs.set_rate_limit(limit_or_interval=interval, new_requests=1)
    _client_configured = True


class MusicBrainzProvider(MusicMetadataProvider):
    name = "musicbrainz"

    def __init__(self, app: str, version: str, contact: str, cache=None,
                 rate_limit_per_sec: float = 1.0):
        _ensure_client(app, version, contact, rate_limit_per_sec)
        self.cache = cache

    def _cached_call(self, key: str, fn):
        if self.cache is None:
            return fn()
        return self.cache.get_or_fetch("musicbrainz", key, fn)

    def search_recordings(self, artist: str | None, track: str, album: str | None = None,
                           duration_seconds: float | None = None, limit: int = 5) -> list[dict]:
        import musicbrainzngs

        if not track or not track.strip():
            return []

        query_parts = [f'recording:"{_escape(track)}"']
        if artist:
            query_parts.append(f'artist:"{_escape(artist)}"')
        if album:
            query_parts.append(f'release:"{_escape(album)}"')
        query = " AND ".join(query_parts)
        cache_key = hashlib.sha256(f"search|{query}|{limit}".encode()).hexdigest()

        def _fetch():
            try:
                return musicbrainzngs.search_recordings(query=query, limit=limit)
            except Exception as e:  # noqa: BLE001
                logger.warning("MusicBrainz search failed for %r: %s", query, e)
                return {"recording-list": []}

        response = self._cached_call(cache_key, _fetch)
        return self._flatten_recordings(response.get("recording-list", []))

    def lookup_by_isrc(self, isrc: str) -> list[dict]:
        import musicbrainzngs

        cache_key = hashlib.sha256(f"isrc|{isrc}".encode()).hexdigest()

        def _fetch():
            try:
                return musicbrainzngs.get_recordings_by_isrc(
                    isrc, includes=["releases", "artist-credits", "isrcs", "release-groups"]
                )
            except musicbrainzngs.ResponseError:
                return {"isrc": {"recording-list": []}}
            except Exception as e:  # noqa: BLE001
                logger.warning("MusicBrainz ISRC lookup failed for %s: %s", isrc, e)
                return {"isrc": {"recording-list": []}}

        response = self._cached_call(cache_key, _fetch)
        recordings = response.get("isrc", {}).get("recording-list", [])
        return self._flatten_recordings(recordings, forced_isrc=isrc)

    @staticmethod
    def _flatten_recordings(recording_list: list[dict], forced_isrc: str | None = None) -> list[dict]:
        out = []
        for rec in recording_list:
            artist_credit = rec.get("artist-credit-phrase") or _artist_credit_phrase(rec.get("artist-credit"))
            length_ms = rec.get("length")
            duration_s = float(length_ms) / 1000.0 if length_ms else None
            isrcs = rec.get("isrc-list") or ([forced_isrc] if forced_isrc else [])
            recording_id = rec.get("id")
            score = int(rec.get("ext:score", rec.get("score", 0)) or 0)

            releases = rec.get("release-list") or [{}]
            for release in releases:
                release_group = release.get("release-group", {}) or {}
                out.append({
                    "musicbrainz_recording_id": recording_id,
                    "artist": artist_credit,
                    "track": rec.get("title"),
                    "recording_duration_seconds": duration_s,
                    "isrc": isrcs[0] if isrcs else None,
                    "musicbrainz_release_id": release.get("id"),
                    "album": release.get("title"),
                    "musicbrainz_release_group_id": release_group.get("id"),
                    "release_group": release_group.get("title"),
                    "release_type": release_group.get("primary-type") or release_group.get("type"),
                    "release_country": release.get("country"),
                    "release_date": release.get("date") or release_group.get("first-release-date"),
                    "musicbrainz_artist_id": _first_artist_id(rec.get("artist-credit")),
                    "score": score,
                })
        return out


def _escape(s: str) -> str:
    return s.replace('"', '\\"')


def _artist_credit_phrase(artist_credit) -> str | None:
    if not artist_credit:
        return None
    parts = []
    for item in artist_credit:
        if isinstance(item, dict) and "artist" in item:
            parts.append(item["artist"].get("name", ""))
            parts.append(item.get("joinphrase", ""))
        elif isinstance(item, str):
            parts.append(item)
    return "".join(parts).strip() or None


def _first_artist_id(artist_credit) -> str | None:
    if not artist_credit:
        return None
    for item in artist_credit:
        if isinstance(item, dict) and "artist" in item:
            return item["artist"].get("id")
    return None
