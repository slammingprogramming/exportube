"""Primary metadata provider: yt-dlp.

This is the workhorse of the pipeline. yt-dlp needs no API key/quota, works
for any public/unlisted video, and -- critically for music identification --
surfaces YouTube's own "Music in this video" panel data when YouTube has
attached it: `track`, `artist`, `album`, `release_date`, `release_year`.
Those fields are extracted from YouTube's watch-page music metadata panel,
not guessed from the title, so they are strong (not proof-positive)
evidence for music_detection and music_identification.

Unavailable videos (private/deleted/removed) are not errors from the
pipeline's point of view: we classify the failure reason and return a
record with availability set accordingly, never raise out of fetch_one.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

from tune_history.youtube_metadata.base import VideoMetadataProvider

_PRIVATE_MARKERS = ("private video",)
_DELETED_MARKERS = (
    "this video is no longer available",
    "account associated with this video has been terminated",
    "video has been removed",
    "no longer available because the uploader has closed their youtube account",
)
_UNAVAILABLE_MARKERS = (
    "video unavailable",
    "this video is not available",
    "content isn't available",
    # Age-gated content requires a signed-in, age-verified session that
    # this pipeline (anonymous or non-age-verified cookies) doesn't have --
    # confirmed as a real, recurring case in a 50-video sample from a real
    # watch history. Treated as "unavailable" (inaccessible to this run)
    # rather than a distinct category, since the CSV's `availability`
    # column is about "can we get metadata," not the video's true state.
    "sign in to confirm your age",
    "inappropriate for some users",
)


class YtDlpProvider(VideoMetadataProvider):
    name = "yt-dlp"

    def __init__(self, cookies_from_browser: str | None = None, cookies_file: str | None = None,
                 max_workers: int = 4, max_retries: int = 3):
        self.cookies_from_browser = cookies_from_browser
        self.cookies_file = cookies_file
        self.max_workers = max_workers
        self.max_retries = max_retries

    def _ydl_opts(self) -> dict:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "extract_flat": False,
        }
        if self.cookies_from_browser:
            opts["cookiesfrombrowser"] = (self.cookies_from_browser,)
        if self.cookies_file:
            opts["cookiefile"] = self.cookies_file
        return opts

    def fetch_one(self, video_id: str) -> dict:
        import yt_dlp
        from yt_dlp.utils import DownloadError

        url = f"https://www.youtube.com/watch?v={video_id}"
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                with yt_dlp.YoutubeDL(self._ydl_opts()) as ydl:
                    info = ydl.extract_info(url, download=False)
                return self._normalize(video_id, info)
            except DownloadError as e:
                msg = str(e).lower()
                if any(m in msg for m in _PRIVATE_MARKERS):
                    return self._unavailable_record(video_id, url, "private", str(e))
                if any(m in msg for m in _DELETED_MARKERS):
                    return self._unavailable_record(video_id, url, "deleted", str(e))
                if any(m in msg for m in _UNAVAILABLE_MARKERS):
                    return self._unavailable_record(video_id, url, "unavailable", str(e))
                last_error = e
                # Transient/network-ish error: retry with backoff.
                time.sleep(min(2 ** attempt, 8))
            except Exception as e:  # noqa: BLE001 - genuinely want to keep going
                last_error = e
                time.sleep(min(2 ** attempt, 8))

        logger.warning("yt-dlp failed for %s after %d attempts: %s", video_id, self.max_retries, last_error)
        return self._unavailable_record(video_id, url, "unknown", str(last_error) if last_error else "unknown error")

    def fetch_many(self, video_ids: list[str], progress_cb=None) -> dict[str, dict]:
        results: dict[str, dict] = {}
        completed = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self.fetch_one, vid): vid for vid in video_ids}
            for future in as_completed(futures):
                vid = futures[future]
                try:
                    results[vid] = future.result()
                except Exception as e:  # noqa: BLE001
                    logger.error("Unexpected failure fetching %s: %s", vid, e)
                    results[vid] = self._unavailable_record(
                        vid, f"https://www.youtube.com/watch?v={vid}", "unknown", str(e)
                    )
                completed += 1
                if progress_cb:
                    progress_cb(completed, len(video_ids))
        return results

    @staticmethod
    def _unavailable_record(video_id: str, url: str, reason: str, error_message: str) -> dict:
        return {
            "video_id": video_id,
            "url": url,
            "availability": reason,
            "metadata_source": "yt-dlp",
            "raw": {"error": error_message},
        }

    @staticmethod
    def _normalize(video_id: str, info: dict) -> dict:
        upload_date_raw = info.get("upload_date")  # YYYYMMDD
        upload_date_iso = None
        if upload_date_raw and len(upload_date_raw) == 8:
            upload_date_iso = f"{upload_date_raw[0:4]}-{upload_date_raw[4:6]}-{upload_date_raw[6:8]}"

        release_date_raw = info.get("release_date")
        release_date_iso = None
        if release_date_raw and len(release_date_raw) == 8:
            release_date_iso = f"{release_date_raw[0:4]}-{release_date_raw[4:6]}-{release_date_raw[6:8]}"

        availability_map = {
            "public": "available", "unlisted": "available", "premium_only": "available",
            "subscriber_only": "available", "needs_auth": "available",
            "private": "private",
        }
        availability = availability_map.get(info.get("availability"), "available")

        return {
            "video_id": video_id,
            "url": info.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
            "title": info.get("title"),
            "uploader": info.get("uploader"),
            "channel_id": info.get("channel_id"),
            "channel_url": info.get("channel_url") or info.get("uploader_url"),
            "duration_seconds": info.get("duration"),
            "upload_date": upload_date_iso,
            "description": info.get("description"),
            "tags": info.get("tags") or [],
            "categories": info.get("categories") or [],
            "availability": availability,
            "metadata_source": "yt-dlp",
            "yt_track": info.get("track"),
            "yt_artist": info.get("artist"),
            "yt_album": info.get("album"),
            "yt_release_date": release_date_iso,
            "yt_release_year": str(info.get("release_year")) if info.get("release_year") else None,
            "like_count": info.get("like_count"),
            "view_count": info.get("view_count"),
            "raw": _slim_raw(info),
        }


def _slim_raw(info: dict) -> dict:
    """Keep a reasonably-sized provenance snapshot instead of yt-dlp's full
    (often huge, with format lists) info dict."""
    keys = (
        "id", "title", "uploader", "uploader_id", "channel", "channel_id", "channel_url",
        "duration", "upload_date", "release_date", "release_year", "track", "artist", "album",
        "creators", "categories", "tags", "availability", "description", "view_count",
        "like_count", "webpage_url", "live_status", "was_live",
    )
    return {k: info.get(k) for k in keys if k in info}
