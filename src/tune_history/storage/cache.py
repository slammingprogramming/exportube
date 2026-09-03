"""TTL-aware cache helper built on Database.cache_entries.

Used by youtube_metadata and metadata_enrichment providers so repeated runs
(and interrupted/resumed jobs) never re-query the same video or the same
MusicBrainz search twice within the configured TTL.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from tune_history.storage.db import Database


class Cache:
    def __init__(self, db: Database, ttl_days: float):
        self.db = db
        self.ttl = timedelta(days=ttl_days)

    def get_or_fetch(self, namespace: str, key: str, fetch_fn: Callable[[], Any],
                      force_refresh: bool = False) -> Any:
        if not force_refresh:
            cached = self.db.cache_get(namespace, key)
            if cached is not None:
                fetched_at = datetime.fromisoformat(cached["fetched_at"])
                if fetched_at.tzinfo is None:
                    fetched_at = fetched_at.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - fetched_at < self.ttl:
                    return cached["response"]
        result = fetch_fn()
        self.db.cache_set(namespace, key, result)
        return result

    def peek(self, namespace: str, key: str) -> Any | None:
        cached = self.db.cache_get(namespace, key)
        return cached["response"] if cached else None
