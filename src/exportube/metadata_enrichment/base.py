"""MusicMetadataProvider interface.

Additional providers (AcoustID, Discogs, Cover Art Archive, ...) plug in
here later without touching music_identification -- see docs/ARCHITECTURE.md
"Adding a new metadata_enrichment provider".
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class MusicMetadataProvider(ABC):
    name: str

    @abstractmethod
    def search_recordings(self, artist: str | None, track: str, album: str | None = None,
                           duration_seconds: float | None = None, limit: int = 5) -> list[dict]:
        """Return a list of candidate recording dicts (see
        musicbrainz_provider.py's docstring for the normalized shape)."""
        raise NotImplementedError

    def lookup_by_isrc(self, isrc: str) -> list[dict]:
        return []
