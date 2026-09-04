"""End-to-end pipeline tests using fake providers (no network).

Exercises the scenarios spec section 25 calls out as important: a repeat
watch, a deleted video, a Topic-channel single track, a DJ mix, a
non-music video, and export -- including the canonical dedup CSV and the
review-correction override path.
"""
from __future__ import annotations

from datetime import datetime, timezone

from exportube.config import load_config
from exportube.export.csv_export import gather_export_rows
from exportube.history_import.base import HistoryProvider
from exportube.metadata_enrichment.base import MusicMetadataProvider
from exportube.pipeline import Pipeline
from exportube.storage.models import WatchEvent
from exportube.youtube_metadata.base import VideoMetadataProvider


class FakeMetadataProvider(VideoMetadataProvider):
    name = "fake"

    def __init__(self, records: dict[str, dict]):
        self.records = records

    def fetch_one(self, video_id: str) -> dict:
        if video_id in self.records:
            return {"video_id": video_id, "metadata_source": "yt-dlp", **self.records[video_id]}
        return {"video_id": video_id, "availability": "unavailable", "metadata_source": "yt-dlp"}


class FakeMusicBrainzProvider(MusicMetadataProvider):
    name = "fake_musicbrainz"

    def __init__(self, results_by_query: dict[tuple, list[dict]]):
        self.results_by_query = results_by_query

    def search_recordings(self, artist, track, album=None, duration_seconds=None, limit=5):
        key = ((artist or "").lower(), (track or "").lower())
        return self.results_by_query.get(key, [])

    def lookup_by_isrc(self, isrc: str):
        return []


class ListHistoryProvider(HistoryProvider):
    name = "test_fixture"

    def __init__(self, events):
        self.events = events

    def fetch(self):
        return iter(self.events)

    def describe_capabilities(self):
        return {"can_retrieve": [], "cannot_retrieve": [], "notes": ""}


def _dt(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_full_pipeline_end_to_end(db, tmp_path):
    cfg = load_config()

    events = [
        # Repeat watch of the same Topic-channel single track.
        WatchEvent("vid_single_1", "https://www.youtube.com/watch?v=vid_single_1",
                   "One More Time", "Daft Punk - Topic", _dt("2024-01-15T03:14:21"), "takeout_json"),
        WatchEvent("vid_single_1", "https://www.youtube.com/watch?v=vid_single_1",
                   "One More Time", "Daft Punk - Topic", _dt("2024-03-02T11:00:00"), "takeout_json"),
        # Deleted video -- title only, no URL.
        WatchEvent(None, None, "Watched a video that has been removed", None,
                   _dt("2024-02-01T00:00:00"), "takeout_json", raw_json='{"title": "removed"}'),
        # Non-music: podcast episode.
        WatchEvent("vid_podcast", "https://www.youtube.com/watch?v=vid_podcast",
                   "Joe Rogan Experience #1234", "PowerfulJRE", _dt("2024-02-10T20:00:00"), "takeout_json"),
        # DJ mix (multi-track category).
        WatchEvent("vid_mix", "https://www.youtube.com/watch?v=vid_mix",
                   "2 Hour Drum & Bass Mix", "DNB Channel - Topic", _dt("2024-02-15T20:00:00"), "takeout_json"),
        # Same recording encountered via a second (official video) upload.
        WatchEvent("vid_single_2", "https://www.youtube.com/watch?v=vid_single_2",
                   "Daft Punk - One More Time (Official Music Video)", "Daft Punk",
                   _dt("2024-04-01T00:00:00"), "takeout_json"),
    ]

    pipeline = Pipeline(db, cfg)
    import_result = pipeline.import_history(ListHistoryProvider(events))
    assert import_result["new"] == 6
    assert import_result["unresolved"] == 1

    # Re-importing the same events must be a no-op (idempotent dedup).
    import_result_2 = pipeline.import_history(ListHistoryProvider(events))
    assert import_result_2["new"] == 0
    assert import_result_2["duplicates"] == 6

    metadata_provider = FakeMetadataProvider({
        "vid_single_1": {
            "title": "One More Time", "uploader": "Daft Punk - Topic",
            "duration_seconds": 320, "availability": "available",
            "yt_track": "One More Time", "yt_artist": "Daft Punk", "yt_album": "Discovery",
            "description": "", "tags": [], "categories": [],
        },
        "vid_podcast": {
            "title": "Joe Rogan Experience #1234", "uploader": "PowerfulJRE",
            "duration_seconds": 9000, "availability": "available",
            "description": "talking about music the whole time", "tags": [], "categories": [],
        },
        "vid_mix": {
            "title": "2 Hour Drum & Bass Mix", "uploader": "DNB Channel - Topic",
            "duration_seconds": 7200, "availability": "available",
            "description": "", "tags": [], "categories": [],
        },
        "vid_single_2": {
            "title": "Daft Punk - One More Time (Official Music Video)", "uploader": "Daft Punk",
            "duration_seconds": 245, "availability": "available",
            "description": "", "tags": [], "categories": [],
        },
    })

    scan_result = pipeline.scan(metadata_provider)
    assert scan_result["errors"] == 0
    assert scan_result["total"] == 4  # 4 distinct resolvable video_ids

    mb_provider = FakeMusicBrainzProvider({
        ("daft punk", "one more time"): [{
            "musicbrainz_recording_id": "mb-rec-omt",
            "artist": "Daft Punk", "track": "One More Time", "album": "Discovery",
            "release_group": "Discovery", "release_type": "Album", "release_country": "FR",
            "release_date": "2001-03-07", "isrc": "FR6V80300001",
            "recording_duration_seconds": 320.0,
            "musicbrainz_release_id": "mb-rel-1", "musicbrainz_release_group_id": "mb-rg-1",
            "musicbrainz_artist_id": "mb-artist-1",
        }],
    })

    identify_result = pipeline.identify(mb_provider)
    assert identify_result["errors"] == 0
    assert identify_result["total"] == 4

    output_dir = tmp_path / "output"
    export_result = pipeline.export(output_dir)
    assert export_result["history_rows"] >= 5  # 4 videos + 1 unresolved entry

    rows_by_id = {r["video_id"]: r for r in gather_export_rows(db)}

    single1 = rows_by_id["vid_single_1"]
    assert single1["artist"] == "Daft Punk"
    assert single1["track"] == "One More Time"
    assert single1["watch_count"] == 2  # repeat watch preserved as a count, not two rows
    assert single1["music_identification_confidence"] in ("high", "medium")

    single2 = rows_by_id["vid_single_2"]
    assert single2["musicbrainz_recording_id"] == "mb-rec-omt"  # same recording, different video

    podcast = rows_by_id["vid_podcast"]
    assert podcast["music_identification_confidence"] == "not_music"

    mix = rows_by_id["vid_mix"]
    assert mix["potentially_multi_track"] is True

    unresolved_rows = [r for r in gather_export_rows(db) if r["video_id"] == ""]
    assert len(unresolved_rows) == 1

    # Canonical library should merge vid_single_1 and vid_single_2 into one
    # recording via the shared MusicBrainz recording ID.
    from exportube.export.csv_export import export_canonical_csv
    canonical_path = output_dir / "canonical_music_library.csv"
    canonical_count = export_canonical_csv(db, canonical_path)
    import csv
    with open(canonical_path, newline="", encoding="utf-8") as f:
        canonical_rows = list(csv.DictReader(f))
    omt_row = next(r for r in canonical_rows if r["musicbrainz_recording_id"] == "mb-rec-omt")
    assert omt_row["youtube_video_count"] == "2"


def test_manual_correction_overrides_automated_identification(db, tmp_path):
    cfg = load_config()
    events = [
        WatchEvent("vid_x", "https://www.youtube.com/watch?v=vid_x",
                   "Unclear Title", "Random Channel", _dt("2024-01-01T00:00:00"), "takeout_json"),
    ]
    pipeline = Pipeline(db, cfg)
    pipeline.import_history(ListHistoryProvider(events))
    metadata_provider = FakeMetadataProvider({
        "vid_x": {"title": "Unclear Title", "uploader": "Random Channel", "duration_seconds": 200,
                  "availability": "available", "description": "", "tags": [], "categories": []},
    })
    pipeline.scan(metadata_provider)
    pipeline.identify(FakeMusicBrainzProvider({}))

    rows_before = {r["video_id"]: r for r in gather_export_rows(db)}
    # A title/channel with zero music-indicating signal at all is
    # conservatively classified not_music (false-positive protection),
    # distinct from "unidentified" (plausibly music, song unknown).
    assert rows_before["vid_x"]["music_identification_confidence"] == "not_music"

    db.save_correction("vid_x", "manual_edit", {"artist": "Corrected Artist", "track": "Corrected Track"})

    rows_after = {r["video_id"]: r for r in gather_export_rows(db)}
    assert rows_after["vid_x"]["artist"] == "Corrected Artist"
    assert rows_after["vid_x"]["manual_override"] is True


def test_multi_track_video_exports_one_row_per_segment(db, tmp_path):
    cfg = load_config()
    events = [
        WatchEvent("vid_mix", "https://www.youtube.com/watch?v=vid_mix",
                   "2 Hour Mix", "DJ Channel - Topic", _dt("2024-02-15T20:00:00"), "takeout_json"),
    ]
    pipeline = Pipeline(db, cfg)
    pipeline.import_history(ListHistoryProvider(events))

    tracklist_description = (
        "Tracklist:\n"
        "0:00 Artist One - Track One\n"
        "3:00 Artist Two - Track Two\n"
        "6:00 Artist Three - Track Three\n"
    )
    metadata_provider = FakeMetadataProvider({
        "vid_mix": {"title": "2 Hour Mix", "uploader": "DJ Channel - Topic",
                    "duration_seconds": 540, "availability": "available",
                    "description": tracklist_description, "tags": [], "categories": []},
    })
    pipeline.scan(metadata_provider)

    mb_provider = FakeMusicBrainzProvider({
        ("artist one", "track one"): [{"musicbrainz_recording_id": "mb1", "artist": "Artist One",
                                        "track": "Track One", "recording_duration_seconds": 178}],
        ("artist two", "track two"): [{"musicbrainz_recording_id": "mb2", "artist": "Artist Two",
                                        "track": "Track Two", "recording_duration_seconds": 182}],
        ("artist three", "track three"): [{"musicbrainz_recording_id": "mb3", "artist": "Artist Three",
                                            "track": "Track Three", "recording_duration_seconds": 175}],
    })
    pipeline.identify(mb_provider)

    rows = [r for r in gather_export_rows(db) if r["video_id"] == "vid_mix"]
    assert len(rows) == 3
    track_indices = sorted(r["track_index"] for r in rows)
    assert track_indices == [0, 1, 2]
    recording_ids = {r["musicbrainz_recording_id"] for r in rows}
    assert recording_ids == {"mb1", "mb2", "mb3"}
    # Every segment shares the same video-level fields (same video, same watch stats).
    assert len({r["video_url"] for r in rows}) == 1
    assert len({r["watch_count"] for r in rows}) == 1

    output_dir = tmp_path / "output"
    from exportube.export.csv_export import export_canonical_csv
    canonical_path = output_dir / "canonical_music_library.csv"
    export_canonical_csv(db, canonical_path)
    import csv
    with open(canonical_path, newline="", encoding="utf-8") as f:
        canonical_rows = list(csv.DictReader(f))
    # Three distinct recordings from one video -> three canonical entries,
    # each crediting exactly one YouTube video.
    mix_rows = [r for r in canonical_rows if r["musicbrainz_recording_id"] in ("mb1", "mb2", "mb3")]
    assert len(mix_rows) == 3
    assert all(r["youtube_video_count"] == "1" for r in mix_rows)


def test_multi_track_video_correction_collapses_to_one_row(db, tmp_path):
    cfg = load_config()
    events = [
        WatchEvent("vid_mix2", "https://www.youtube.com/watch?v=vid_mix2",
                   "2 Hour Mix", "DJ Channel - Topic", _dt("2024-02-15T20:00:00"), "takeout_json"),
    ]
    pipeline = Pipeline(db, cfg)
    pipeline.import_history(ListHistoryProvider(events))
    tracklist_description = (
        "0:00 Artist One - Track One\n3:00 Artist Two - Track Two\n6:00 Artist Three - Track Three\n"
    )
    metadata_provider = FakeMetadataProvider({
        "vid_mix2": {"title": "2 Hour Mix", "uploader": "DJ Channel - Topic", "duration_seconds": 540,
                     "availability": "available", "description": tracklist_description,
                     "tags": [], "categories": []},
    })
    pipeline.scan(metadata_provider)
    pipeline.identify(FakeMusicBrainzProvider({}))

    rows_before = [r for r in gather_export_rows(db) if r["video_id"] == "vid_mix2"]
    assert len(rows_before) == 3  # unidentified segments still each get a row

    db.save_correction("vid_mix2", "manual_edit", {"artist": "Real Artist", "track": "Real Track"})
    rows_after = [r for r in gather_export_rows(db) if r["video_id"] == "vid_mix2"]
    assert len(rows_after) == 1
    assert rows_after[0]["artist"] == "Real Artist"
    assert rows_after[0]["track_index"] == ""


def test_scan_and_identify_limit_processes_only_a_subset(db):
    cfg = load_config()
    events = [
        WatchEvent(f"vid_{i}", f"https://www.youtube.com/watch?v=vid_{i}",
                   f"Artist {i} - Track {i}", f"Artist {i} - Topic", _dt("2024-01-01T00:00:00"), "takeout_json")
        for i in range(5)
    ]
    pipeline = Pipeline(db, cfg)
    pipeline.import_history(ListHistoryProvider(events))

    metadata_provider = FakeMetadataProvider({
        f"vid_{i}": {"title": f"Artist {i} - Track {i}", "uploader": f"Artist {i} - Topic",
                     "duration_seconds": 200, "availability": "available",
                     "description": "", "tags": [], "categories": []}
        for i in range(5)
    })

    scan_result = pipeline.scan(metadata_provider, limit=2)
    assert scan_result["total"] == 2
    assert scan_result["processed"] == 2
    assert len(pipeline.db.all_videos()) == 2  # only the limited slice got a videos row so far

    # The rest remain pending, not errored -- a later call without --limit
    # (or a higher one) picks up exactly the remaining 3.
    scan_result_2 = pipeline.scan(metadata_provider)
    assert scan_result_2["total"] == 3
    assert scan_result_2["processed"] == 3
    assert len(pipeline.db.all_videos()) == 5

    identify_result = pipeline.identify(FakeMusicBrainzProvider({}), limit=3)
    assert identify_result["total"] == 3
    assert len(pipeline.db.videos_pending("identification")) == 2
