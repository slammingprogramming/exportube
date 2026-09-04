"""Google Takeout import.

Takeout's YouTube export is a directory tree whose top-level folder name is
localized ("YouTube and YouTube Music", "YouTube et YouTube Music", ...), so
we never hard-code that path. We instead search recursively (inside a zip
or an already-extracted directory) for filenames matching the
watch-history JSON/HTML patterns Google has shipped historically:

    watch-history.json
    watch-history.html
    (occasionally suffixed, e.g. "watch-history(1).json" on repeated
    exports/downloads)

JSON is preferred when both are present: it is more structurely reliable
(HTML requires DOM scraping and localized date-string parsing). We keep the
HTML parser because some older/alternate Takeout exports only include HTML.

Reproducibility: whatever file we actually parsed is copied verbatim into
data_dir/raw_imports/<batch_id>/ before parsing, and raw_json on every
WatchEvent holds the untouched source record.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterator

from bs4 import BeautifulSoup

from exportube.history_import.base import HistoryProvider
from exportube.history_import.normalize import normalize_timestamp
from exportube.history_import.url_parse import parse_video_url
from exportube.storage.models import WatchEvent

logger = logging.getLogger(__name__)

WATCH_HISTORY_JSON_RE = re.compile(r"watch-history(\(\d+\))?\.json$", re.IGNORECASE)
WATCH_HISTORY_HTML_RE = re.compile(r"watch-history(\(\d+\))?\.html?$", re.IGNORECASE)

REMOVED_VIDEO_MARKERS = (
    "watched a video that has been removed",
    "a video that isn't available anymore",
    "a video that has been removed",
)


class TakeoutProvider(HistoryProvider):
    name = "takeout"

    def __init__(self, source_path: str | Path, raw_archive_dir: Path):
        self.source_path = Path(source_path)
        self.raw_archive_dir = raw_archive_dir
        self.batch_id = uuid.uuid4().hex[:12]

    def describe_capabilities(self) -> dict:
        return {
            "can_retrieve": [
                "Full watch history as recorded by Google (title, video URL, "
                "channel name, watch timestamp) for as far back as Google retained it",
                "Works fully offline once exported; no live API quota use",
            ],
            "cannot_retrieve": [
                "Videos watched while watch history was paused/disabled",
                "Anything Google has since purged from your account activity",
                "Current video metadata (duration, availability) -- fetched "
                "separately in the youtube_metadata stage",
            ],
            "notes": "Takeout export must include the 'YouTube and YouTube Music' "
                     "product with the 'history' data type selected, JSON format recommended.",
        }

    def fetch(self) -> Iterator[WatchEvent]:
        target_file, opener = self._locate_watch_history()
        if target_file is None:
            raise FileNotFoundError(
                f"No watch-history.json or watch-history.html found under {self.source_path}. "
                "Make sure the Takeout export includes YouTube and YouTube Music > history."
            )

        preserved_path = self._preserve_raw(target_file, opener)

        if WATCH_HISTORY_JSON_RE.search(target_file.name):
            yield from self._parse_json(preserved_path)
        else:
            yield from self._parse_html(preserved_path)

    # ------------------------------------------------------------------ locate
    def _locate_watch_history(self):
        """Returns (member_path_or_fspath, opener) where opener is either
        None (plain filesystem path) or a zipfile.ZipFile to read the member
        from. Prefers JSON over HTML; prefers non-numbered filenames."""
        if self.source_path.is_file() and self.source_path.suffix.lower() == ".zip":
            zf = zipfile.ZipFile(self.source_path)
            names = zf.namelist()
            json_matches = sorted(n for n in names if WATCH_HISTORY_JSON_RE.search(Path(n).name))
            html_matches = sorted(n for n in names if WATCH_HISTORY_HTML_RE.search(Path(n).name))
            chosen = (json_matches or html_matches)
            if not chosen:
                zf.close()
                return None, None
            # Keep zip member paths as PurePosixPath: zip entries always use
            # "/" separators internally, and wrapping them in a plain Path
            # on Windows would silently rewrite that to "\", breaking the
            # later zf.open(str(target_file)) lookup.
            return PurePosixPath(chosen[0]), zf

        if self.source_path.is_dir():
            json_matches = sorted(
                p for p in self.source_path.rglob("*") if WATCH_HISTORY_JSON_RE.search(p.name)
            )
            html_matches = sorted(
                p for p in self.source_path.rglob("*") if WATCH_HISTORY_HTML_RE.search(p.name)
            )
            chosen = (json_matches or html_matches)
            if not chosen:
                return None, None
            return chosen[0], None

        if self.source_path.is_file():
            # User pointed straight at the watch-history file itself.
            if WATCH_HISTORY_JSON_RE.search(self.source_path.name) or \
               WATCH_HISTORY_HTML_RE.search(self.source_path.name):
                return self.source_path, None

        return None, None

    def _preserve_raw(self, target_file: Path, opener: zipfile.ZipFile | None) -> Path:
        dest_dir = self.raw_archive_dir / self.batch_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / Path(target_file.name).name
        if opener is not None:
            with opener.open(str(target_file)) as src, open(dest_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            opener.close()
        else:
            shutil.copyfile(target_file, dest_path)
        return dest_path

    # -------------------------------------------------------------------- json
    def _parse_json(self, path: Path) -> Iterator[WatchEvent]:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for entry in data:
            title = entry.get("title", "") or ""
            title_url = entry.get("titleUrl")
            channel_name = None
            subtitles = entry.get("subtitles") or []
            if subtitles:
                channel_name = subtitles[0].get("name")

            is_removed = title_url is None and any(
                marker in title.lower() for marker in REMOVED_VIDEO_MARKERS
            )
            clean_title = re.sub(r"^Watched\s+", "", title).strip() if title else None

            parsed = parse_video_url(title_url)
            watched_at = normalize_timestamp(entry.get("time"))

            yield WatchEvent(
                video_id=parsed.video_id,
                video_url_raw=title_url,
                raw_title=None if is_removed else clean_title,
                raw_channel_name=channel_name,
                watched_at=watched_at,
                source="takeout_json",
                source_playlist_name=None,
                source_playlist_id=None,
                import_batch_id=self.batch_id,
                raw_json=json.dumps(entry, ensure_ascii=False),
            )

    # -------------------------------------------------------------------- html
    def _parse_html(self, path: Path) -> Iterator[WatchEvent]:
        with open(path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f, "lxml")

        cells = soup.find_all("div", class_=lambda c: c and "content-cell" in c and "mdl-typography--body-1" in c)

        for cell in cells:
            links = cell.find_all("a")
            if not links:
                continue
            video_link = links[0]
            title_url = video_link.get("href")
            title = video_link.get_text(strip=True)
            channel_name = links[1].get_text(strip=True) if len(links) > 1 else None

            full_text = cell.get_text(" ", strip=True)
            # Timestamp is the trailing text after the last <a>, e.g.
            # "... Channel Name Jan 15, 2024, 3:14:21 AM PST"
            timestamp_str = None
            m = re.search(r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4},.*)$", full_text)
            if m:
                timestamp_str = m.group(1)

            parsed = parse_video_url(title_url)
            watched_at = normalize_timestamp(timestamp_str)

            yield WatchEvent(
                video_id=parsed.video_id,
                video_url_raw=title_url,
                raw_title=title or None,
                raw_channel_name=channel_name,
                watched_at=watched_at,
                source="takeout_html",
                source_playlist_name=None,
                source_playlist_id=None,
                import_batch_id=self.batch_id,
                raw_json=json.dumps({
                    "title": title, "titleUrl": title_url,
                    "channel": channel_name, "timestamp_str": timestamp_str,
                }, ensure_ascii=False),
            )
