"""VideoMetadataProvider interface.

A provider takes a video_id (or URL) and returns a normalized dict (never
a VideoRecord directly, to keep providers decoupled from storage). Fields
are a superset across providers; a provider that can't determine a field
simply omits/None's it rather than guessing.

Normalized field set (all optional except video_id/url):
  video_id, url, title, uploader, channel_id, channel_url, duration_seconds,
  upload_date (ISO "YYYY-MM-DD"), description, tags (list[str]),
  categories (list[str]), availability ("available"|"unavailable"|
  "private"|"deleted"|"unknown"),
  yt_track, yt_artist, yt_album, yt_release_date, yt_release_year,
  raw (the provider's full untouched response, for provenance/debugging).
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class VideoMetadataProvider(ABC):
    name: str

    @abstractmethod
    def fetch_one(self, video_id: str) -> dict:
        raise NotImplementedError

    def fetch_many(self, video_ids: list[str], progress_cb=None) -> dict[str, dict]:
        results = {}
        for i, vid in enumerate(video_ids):
            results[vid] = self.fetch_one(vid)
            if progress_cb:
                progress_cb(i + 1, len(video_ids))
        return results
