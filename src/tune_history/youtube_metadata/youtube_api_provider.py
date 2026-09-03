"""Supplementary metadata provider: official YouTube Data API v3.

Optional -- only used when OAuth credentials (see history_import.youtube_provider
.YouTubeOAuthClient) are configured. Adds a few things yt-dlp doesn't
reliably expose: the official `categoryId` (YouTube category "10" = Music),
`topicDetails.topicCategories` (Wikipedia topic URLs, useful as an
independent "is this musical" signal), and precise `status.privacyStatus`.

Batches up to 50 video IDs per request, the API's documented maximum for
`videos.list`.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from tune_history.youtube_metadata.base import VideoMetadataProvider

MUSIC_CATEGORY_ID = "10"


class YouTubeAPIProvider(VideoMetadataProvider):
    name = "youtube_api"

    def __init__(self, oauth_client):
        self.oauth_client = oauth_client
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = self.oauth_client.build_service()
        return self._service

    def fetch_one(self, video_id: str) -> dict:
        return self.fetch_many([video_id]).get(video_id, {"video_id": video_id, "availability": "unknown"})

    def fetch_many(self, video_ids: list[str], progress_cb=None) -> dict[str, dict]:
        results: dict[str, dict] = {}
        chunks = [video_ids[i:i + 50] for i in range(0, len(video_ids), 50)]
        done = 0
        for chunk in chunks:
            try:
                response = self.service.videos().list(
                    part="snippet,contentDetails,status,topicDetails,statistics",
                    id=",".join(chunk),
                ).execute()
            except Exception as e:  # noqa: BLE001
                logger.warning("YouTube Data API lookup failed for chunk: %s", e)
                for vid in chunk:
                    results[vid] = {"video_id": vid, "availability": "unknown", "metadata_source": "youtube_api"}
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, len(video_ids))
                continue

            found_ids = set()
            for item in response.get("items", []):
                vid = item["id"]
                found_ids.add(vid)
                results[vid] = self._normalize(vid, item)
            for vid in chunk:
                if vid not in found_ids:
                    # Not returned by the API at all -> deleted/private/otherwise inaccessible.
                    results[vid] = {"video_id": vid, "availability": "unavailable", "metadata_source": "youtube_api"}
            done += len(chunk)
            if progress_cb:
                progress_cb(done, len(video_ids))
        return results

    @staticmethod
    def _normalize(video_id: str, item: dict) -> dict:
        snippet = item.get("snippet", {})
        content_details = item.get("contentDetails", {})
        status = item.get("status", {})
        topic_details = item.get("topicDetails", {})

        privacy = status.get("privacyStatus", "public")
        availability = "available" if privacy in ("public", "unlisted") else privacy

        return {
            "video_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "title": snippet.get("title"),
            "uploader": snippet.get("channelTitle"),
            "channel_id": snippet.get("channelId"),
            "upload_date": (snippet.get("publishedAt") or "")[:10] or None,
            "description": snippet.get("description"),
            "tags": snippet.get("tags") or [],
            "category_id": snippet.get("categoryId"),
            "is_music_category": snippet.get("categoryId") == MUSIC_CATEGORY_ID,
            "topic_categories": topic_details.get("topicCategories", []),
            "availability": availability,
            "metadata_source": "youtube_api",
            "duration_iso8601": content_details.get("duration"),
            "raw": item,
        }
