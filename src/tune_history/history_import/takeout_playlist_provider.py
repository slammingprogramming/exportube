"""Google Takeout playlist import (Liked videos, Watch later, custom
playlists) -- supplementary to watch-history import, spec section 18.

**Verified against a real Google Takeout export** (September 2026, default
export settings, see AGENTS.md "Needs verification against a real Google
Takeout export" -- this is that verification). Actual observed structure
under `playlists/`:

    playlists.csv                    <- one manifest row per playlist:
                                         Playlist ID, Playlist Title (Original), ...
    Watch later-videos.csv           <- one file per playlist, ID + add-timestamp only:
                                         Video Id,Playlist Video Creation Timestamp
                                         sampleVid01,2024-01-01T00:00:00+00:00

Two things differ from what was assumed before verification:

  1. Each playlist's membership file is named "<Playlist Title>-videos.csv",
     not "<Playlist Title>.csv" -- `_derive_playlist_name` strips the
     trailing "-videos"/"_videos" suffix so `source_playlist_name` reads
     "Watch later", not "Watch later-videos".
  2. There is a separate `playlists.csv` manifest carrying each playlist's
     real Playlist ID (which the per-playlist file's rows never repeat).
     `_parse_playlists_manifest` reads it and maps title -> ID so
     `source_playlist_id` can actually be populated instead of always
     being blank.

The per-playlist membership row shape (`Video Id,Playlist Video Creation
Timestamp`) matches what was assumed pre-verification exactly. That
timestamp is when the video was ADDED TO THE PLAYLIST, a fundamentally
different thing from when it was WATCHED (spec section 18: "Do not
confuse playlist date added with video upload date" -- the same principle
extends to watch date). Events from this provider therefore carry
`watched_at=None`; downstream, `Database.WATCH_SOURCES` excludes this
provider's `source` value from `watch_count`/first-seen/last-seen
calculations, so playlist membership never masquerades as a watch. It
still contributes real, useful `source_playlist_name`/`source_playlist_id`
context (both for videos separately confirmed watched, and as its own
"encountered via playlist" discovery path for videos never otherwise seen
in watch history).

Remaining locale caveat: the `playlists/` directory name itself may still
be localized (not verified in a non-English export), so, exactly like
TakeoutProvider, we never search for that literal folder name -- CSVs are
found by header-content, not directory name or exact filename.
"Liked videos" wasn't present in the account this was verified against
(no liked videos, or the account predates that feature), so its exact
filename is still unconfirmed; the code makes no special case for it and
should handle it the same as any other playlist if/when seen.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterator

from tune_history.history_import.base import HistoryProvider
from tune_history.storage.models import WatchEvent

logger = logging.getLogger(__name__)

VIDEO_ID_HEADER_RE = re.compile(r"video\s*id", re.IGNORECASE)
TIMESTAMP_HEADER_RE = re.compile(r"timestamp", re.IGNORECASE)
PLAYLIST_ID_HEADER_RE = re.compile(r"playlist\s*id", re.IGNORECASE)
PLAYLIST_TITLE_HEADER_RE = re.compile(r"playlist\s*title", re.IGNORECASE)
NAME_VIDEOS_SUFFIX_RE = re.compile(r"[-_]videos$", re.IGNORECASE)

# Filenames that are Takeout playlist exports but are NOT actually music-
# relevant playlists worth importing as discovery context (none currently
# excluded by default -- kept as an extension point / documented decision
# point rather than a silent skip list).
EXCLUDE_PLAYLIST_NAMES: set[str] = set()


def _derive_playlist_name(filename: str) -> str:
    stem = Path(filename).stem
    return NAME_VIDEOS_SUFFIX_RE.sub("", stem).strip()


class TakeoutPlaylistProvider(HistoryProvider):
    name = "takeout_playlists"

    def __init__(self, source_path: str | Path):
        self.source_path = Path(source_path)
        self.batch_id = uuid.uuid4().hex[:12]

    def describe_capabilities(self) -> dict:
        return {
            "can_retrieve": [
                "Which of your playlists (including Liked videos / Watch "
                "later) each video belongs to, and when it was added to that playlist",
            ],
            "cannot_retrieve": [
                "Whether/when you actually watched the video (playlist "
                "membership is not a watch event, and is never counted as one)",
                "Video title/channel (fetched separately in the youtube_metadata stage)",
            ],
            "notes": "Supplementary to watch-history import -- run alongside "
                     "`import <takeout>`, not instead of it.",
        }

    def fetch(self) -> Iterator[WatchEvent]:
        import json

        all_csvs = list(self._iter_csv_files())
        playlist_ids_by_name = self._build_manifest_lookup(all_csvs)

        for filename, raw in all_csvs:
            parsed = self._parse_membership_csv(raw)
            if not parsed:
                continue  # not a playlist-membership CSV (e.g. it's playlists.csv itself, or subscriptions.csv)

            playlist_name = _derive_playlist_name(filename)
            if playlist_name in EXCLUDE_PLAYLIST_NAMES:
                continue
            playlist_id = playlist_ids_by_name.get(playlist_name)

            (video_id_col, timestamp_col), rows = parsed
            for row in rows:
                video_id = (row.get(video_id_col) or "").strip() or None
                if not video_id:
                    continue
                timestamp_raw = row.get(timestamp_col) if timestamp_col else None
                yield WatchEvent(
                    video_id=video_id,
                    video_url_raw=f"https://www.youtube.com/watch?v={video_id}",
                    raw_title=None,
                    raw_channel_name=None,
                    watched_at=None,  # never a watch event -- see module docstring
                    source="takeout_playlist",
                    source_playlist_name=playlist_name,
                    source_playlist_id=playlist_id,
                    import_batch_id=self.batch_id,
                    # Playlist-add timestamp preserved for provenance/
                    # debugging even though it is not treated as a watch date.
                    raw_json=json.dumps({**row, "_playlist_added_at": timestamp_raw}, ensure_ascii=False),
                )

    # ---------------------------------------------------------------- locate
    def _iter_csv_files(self) -> Iterator[tuple[str, str]]:
        """Yields (filename, raw_text) for every .csv file found under
        source_path, whatever it turns out to be -- callers classify each
        one (membership file vs. manifest vs. irrelevant) by content."""
        if self.source_path.is_file() and self.source_path.suffix.lower() == ".zip":
            zf = zipfile.ZipFile(self.source_path)
            try:
                for name in zf.namelist():
                    if not name.lower().endswith(".csv"):
                        continue
                    try:
                        raw = zf.read(name).decode("utf-8-sig")
                    except (KeyError, UnicodeDecodeError):
                        continue
                    yield PurePosixPath(name).name, raw
            finally:
                zf.close()
            return

        if self.source_path.is_dir():
            for path in self.source_path.rglob("*.csv"):
                try:
                    raw = path.read_text(encoding="utf-8-sig")
                except (UnicodeDecodeError, OSError):
                    continue
                yield path.name, raw
            return

        if self.source_path.is_file() and self.source_path.suffix.lower() == ".csv":
            raw = self.source_path.read_text(encoding="utf-8-sig")
            yield self.source_path.name, raw

    @staticmethod
    def _parse_membership_csv(raw: str):
        """A per-playlist membership CSV: `Video Id,Playlist Video Creation
        Timestamp` (verified against a real export -- see module
        docstring). Returns None for anything else (playlists.csv,
        subscriptions.csv, ...)."""
        reader = csv.DictReader(io.StringIO(raw))
        if not reader.fieldnames or len(reader.fieldnames) < 2:
            return None
        video_id_col = next((f for f in reader.fieldnames if VIDEO_ID_HEADER_RE.search(f)), None)
        timestamp_col = next((f for f in reader.fieldnames if TIMESTAMP_HEADER_RE.search(f)), None)
        if not video_id_col:
            return None
        rows = list(reader)
        return ((video_id_col, timestamp_col), rows)

    @staticmethod
    def _build_manifest_lookup(all_csvs: list[tuple[str, str]]) -> dict[str, str]:
        """Finds playlists.csv (by header content, not filename -- see
        module docstring) and returns {playlist title: playlist ID}. Empty
        dict (not an error) if no manifest is present -- source_playlist_id
        then just stays blank, same as before this existed."""
        for _filename, raw in all_csvs:
            reader = csv.DictReader(io.StringIO(raw))
            if not reader.fieldnames:
                continue
            id_col = next((f for f in reader.fieldnames if PLAYLIST_ID_HEADER_RE.search(f)), None)
            title_col = next((f for f in reader.fieldnames if PLAYLIST_TITLE_HEADER_RE.search(f)), None)
            if not id_col or not title_col:
                continue
            lookup = {}
            for row in reader:
                title = (row.get(title_col) or "").strip()
                playlist_id = (row.get(id_col) or "").strip()
                if title and playlist_id:
                    lookup[title] = playlist_id
            if lookup:
                return lookup
        return {}
