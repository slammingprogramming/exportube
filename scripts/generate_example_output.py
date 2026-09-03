"""Generates examples/example_output/*.csv using canned (non-network)
metadata so the repo can ship a realistic example export. Not part of the
application itself -- a one-off doc-generation helper.

Run: python scripts/generate_example_output.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tune_history.config import load_config
from tune_history.history_import.base import HistoryProvider
from tune_history.metadata_enrichment.base import MusicMetadataProvider
from tune_history.pipeline import Pipeline
from tune_history.storage.db import Database
from tune_history.storage.models import WatchEvent
from tune_history.youtube_metadata.base import VideoMetadataProvider


class FakeMetadataProvider(VideoMetadataProvider):
    name = "fake"

    def __init__(self, records):
        self.records = records

    def fetch_one(self, video_id):
        if video_id in self.records:
            return {"video_id": video_id, "metadata_source": "yt-dlp", **self.records[video_id]}
        return {"video_id": video_id, "availability": "unavailable", "metadata_source": "yt-dlp"}


class FakeMusicBrainzProvider(MusicMetadataProvider):
    name = "fake_musicbrainz"

    def __init__(self, results_by_query):
        self.results_by_query = results_by_query

    def search_recordings(self, artist, track, album=None, duration_seconds=None, limit=5):
        return self.results_by_query.get(((artist or "").lower(), (track or "").lower()), [])

    def lookup_by_isrc(self, isrc):
        return []


class ListHistoryProvider(HistoryProvider):
    name = "example"

    def __init__(self, events):
        self.events = events

    def fetch(self):
        return iter(self.events)

    def describe_capabilities(self):
        return {"can_retrieve": [], "cannot_retrieve": [], "notes": ""}


def _dt(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def main():
    db_path = ROOT / "examples" / "_example.sqlite3"
    db_path.unlink(missing_ok=True)
    db = Database(db_path)
    cfg = load_config()

    events = [
        WatchEvent("FGBhQbmPwH8", "https://www.youtube.com/watch?v=FGBhQbmPwH8",
                   "One More Time", "Daft Punk - Topic", _dt("2024-01-15T03:14:21"), "takeout_json"),
        WatchEvent("FGBhQbmPwH8", "https://www.youtube.com/watch?v=FGBhQbmPwH8",
                   "One More Time", "Daft Punk - Topic", _dt("2024-03-02T11:00:00"), "takeout_json"),
        WatchEvent("dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                   "Rick Astley - Never Gonna Give You Up (Official Music Video)", "Rick Astley",
                   _dt("2024-01-20T18:00:00"), "takeout_json"),
        WatchEvent("jNQXAC9IVRw", "https://www.youtube.com/watch?v=jNQXAC9IVRw",
                   "Joe Rogan Experience #1234 - Guest Name", "PowerfulJRE",
                   _dt("2024-02-10T20:00:00"), "takeout_json"),
        WatchEvent("mixvideoid01", "https://www.youtube.com/watch?v=mixvideoid01",
                   "Deep House DJ Set", "Deep House Channel - Topic",
                   _dt("2024-02-15T20:00:00"), "takeout_json"),
        WatchEvent(None, None, "Watched a video that has been removed", None,
                   _dt("2024-02-01T00:00:00"), "takeout_json"),
        # Also present in a Takeout playlist export -- not a watch event,
        # just supplementary source_playlist context (see
        # history_import/takeout_playlist_provider.py).
        WatchEvent("FGBhQbmPwH8", "https://www.youtube.com/watch?v=FGBhQbmPwH8",
                   None, None, None, "takeout_playlist", source_playlist_name="Liked videos"),
    ]

    pipeline = Pipeline(db, cfg)
    pipeline.import_history(ListHistoryProvider(events))

    metadata_provider = FakeMetadataProvider({
        "FGBhQbmPwH8": {"title": "One More Time", "uploader": "Daft Punk - Topic", "duration_seconds": 320,
                         "availability": "available", "yt_track": "One More Time", "yt_artist": "Daft Punk",
                         "yt_album": "Discovery", "description": "Provided to YouTube by Because Music",
                         "tags": [], "categories": ["Music"]},
        "dQw4w9WgXcQ": {"title": "Rick Astley - Never Gonna Give You Up (Official Music Video)",
                         "uploader": "Rick Astley", "duration_seconds": 213, "availability": "available",
                         "description": "", "tags": [], "categories": ["Music"]},
        "jNQXAC9IVRw": {"title": "Joe Rogan Experience #1234 - Guest Name", "uploader": "PowerfulJRE",
                         "duration_seconds": 9840, "availability": "available",
                         "description": "", "tags": [], "categories": ["Comedy"]},
        "mixvideoid01": {"title": "Deep House DJ Set", "uploader": "Deep House Channel - Topic",
                          "duration_seconds": 540, "availability": "available",
                          "description": (
                              "Tracklist:\n"
                              "0:00 Lane 8 - Atlas\n"
                              "3:00 Yotto - Straw\n"
                              "6:00 Ben Bohmer - Breathing\n"
                          ),
                          "tags": [], "categories": ["Music"]},
    })
    pipeline.scan(metadata_provider)

    mb_provider = FakeMusicBrainzProvider({
        ("daft punk", "one more time"): [{
            "musicbrainz_recording_id": "b06e0e6b-6ce7-4e6f-9e3a-1d2a2f8c1234",
            "artist": "Daft Punk", "track": "One More Time", "album": "Discovery",
            "release_group": "Discovery", "release_type": "Album", "release_country": "FR",
            "release_date": "2001-03-07", "isrc": "GBDUW0000059",
            "recording_duration_seconds": 320.2,
            "musicbrainz_release_id": "9c6b5c1e-1111-2222-3333-444455556666",
            "musicbrainz_release_group_id": "1a2b3c4d-1111-2222-3333-444455556666",
            "musicbrainz_artist_id": "056e4f3e-d505-4dad-8ec1-d04f521cbb56",
        }],
        ("rick astley", "never gonna give you up"): [{
            "musicbrainz_recording_id": "7a4c2e1a-1111-2222-3333-444455556677",
            "artist": "Rick Astley", "track": "Never Gonna Give You Up", "album": "Whenever You Need Somebody",
            "release_group": "Whenever You Need Somebody", "release_type": "Album", "release_country": "GB",
            "release_date": "1987-11-16", "isrc": "GBARL9300135",
            "recording_duration_seconds": 213.0,
            "musicbrainz_release_id": "aaaa1111-1111-2222-3333-444455556677",
            "musicbrainz_release_group_id": "bbbb1111-1111-2222-3333-444455556677",
            "musicbrainz_artist_id": "b3ae82c2-e60b-4851-8317-9b57fda5c6d0",
        }],
        ("lane 8", "atlas"): [{
            "musicbrainz_recording_id": "c1c1c1c1-1111-2222-3333-444455556601",
            "artist": "Lane 8", "track": "Atlas", "album": "Atlas",
            "release_group": "Atlas", "release_type": "Album", "release_country": "US",
            "release_date": "2018-06-15", "isrc": "USUG11800601",
            "recording_duration_seconds": 179.0,
            "musicbrainz_release_id": "d1d1d1d1-1111-2222-3333-444455556601",
            "musicbrainz_release_group_id": "e1e1e1e1-1111-2222-3333-444455556601",
            "musicbrainz_artist_id": "f1f1f1f1-1111-2222-3333-444455556601",
        }],
        ("yotto", "straw"): [{
            "musicbrainz_recording_id": "c2c2c2c2-1111-2222-3333-444455556602",
            "artist": "Yotto", "track": "Straw", "album": "Straw",
            "release_group": "Straw", "release_type": "Single", "release_country": "FI",
            "release_date": "2019-03-01", "isrc": "FI1234567890",
            "recording_duration_seconds": 183.0,
            "musicbrainz_release_id": "d2d2d2d2-1111-2222-3333-444455556602",
            "musicbrainz_release_group_id": "e2e2e2e2-1111-2222-3333-444455556602",
            "musicbrainz_artist_id": "f2f2f2f2-1111-2222-3333-444455556602",
        }],
        # Deliberately no MusicBrainz entry for "Ben Bohmer - Breathing" --
        # the example ships one segment that stays unidentified, matching
        # the "preserve uncertainty" philosophy (spec section 16).
    })
    pipeline.identify(mb_provider)

    out_dir = ROOT / "examples" / "example_output"
    result = pipeline.export(out_dir)
    print(result)
    db.close()
    db_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
