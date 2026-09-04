"""Fans a candidate search out across multiple MusicMetadataProvider
instances and concatenates their results, tagging each result's `sources`
with the provider that produced it. This is how a second/third provider
(Discogs today; AcoustID/Cover Art Archive/etc. later -- spec section 7)
gets used by music_identification/identifier.py without that module
needing to know how many providers are configured: it always talks to one
object satisfying the MusicMetadataProvider interface.

One provider failing (network error, bad token, rate limited) never takes
down the others -- each is wrapped in its own try/except.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from exportube.metadata_enrichment.base import MusicMetadataProvider


class FanOutProvider(MusicMetadataProvider):
    name = "fan_out"

    def __init__(self, providers: list[MusicMetadataProvider]):
        self.providers = providers

    def search_recordings(self, artist: str | None, track: str, album: str | None = None,
                           duration_seconds: float | None = None, limit: int = 5) -> list[dict]:
        results: list[dict] = []
        for provider in self.providers:
            try:
                provider_results = provider.search_recordings(artist, track, album, duration_seconds, limit)
            except Exception as e:  # noqa: BLE001 - one provider's failure must not sink the others
                logger.warning("%s.search_recordings failed: %s", provider.name, e)
                continue
            for r in provider_results:
                r = dict(r)
                r["_provider"] = provider.name
                results.append(r)
        return results

    def lookup_by_isrc(self, isrc: str) -> list[dict]:
        results: list[dict] = []
        for provider in self.providers:
            try:
                provider_results = provider.lookup_by_isrc(isrc)
            except Exception as e:  # noqa: BLE001
                logger.warning("%s.lookup_by_isrc failed: %s", provider.name, e)
                continue
            for r in provider_results:
                r = dict(r)
                r["_provider"] = provider.name
                results.append(r)
        return results
