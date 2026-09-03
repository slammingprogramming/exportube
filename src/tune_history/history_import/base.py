"""HistoryProvider interface.

Every acquisition method (Google Takeout, YouTube API, browser-session
scrape) implements this and yields tune_history.storage.models.WatchEvent
objects. The rest of the pipeline never needs to know which acquisition
method produced a given event -- this is the "common internal
representation" required regardless of source.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from tune_history.storage.models import WatchEvent


class HistoryProvider(ABC):
    name: str

    @abstractmethod
    def fetch(self) -> Iterator[WatchEvent]:
        """Yield WatchEvent records. Must not raise on a single bad record;
        log and skip (but never silently invent data)."""
        raise NotImplementedError

    @abstractmethod
    def describe_capabilities(self) -> dict:
        """Return a small dict describing what this provider can and cannot
        retrieve, for display in the UI/CLI before the user commits to it.
        Keys: can_retrieve (list[str]), cannot_retrieve (list[str]),
        notes (str).
        """
        raise NotImplementedError
