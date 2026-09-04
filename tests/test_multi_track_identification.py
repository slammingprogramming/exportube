from datetime import datetime, timezone

from exportube.candidate_generation.title_parser import parse_title
from exportube.confidence.engine import ConfidenceEngine
from exportube.metadata_enrichment.base import MusicMetadataProvider
from exportube.music_identification.identifier import identify_multi_track
from exportube.storage.models import MusicCategory, MusicDetectionResult, YoutubeMusicStatus


class FakeMB(MusicMetadataProvider):
    name = "fake"

    def __init__(self, results_by_query):
        self.results_by_query = results_by_query

    def search_recordings(self, artist, track, album=None, duration_seconds=None, limit=5):
        return self.results_by_query.get(((artist or "").lower(), (track or "").lower()), [])

    def lookup_by_isrc(self, isrc):
        return []


TRACKLIST_DESCRIPTION = """Tracklist:
0:00 Artist One - Track One
3:00 Artist Two - Track Two
6:00 Artist Three - Track Three
"""


def _detection(category, description_has_tracklist=True):
    return MusicDetectionResult(
        video_id="vid_mix", is_music=True, category=category, score=10,
        youtube_music_status=YoutubeMusicStatus.UNKNOWN,
        signals={"tracklist_present": description_has_tracklist},
        evaluated_at=datetime.now(timezone.utc),
    )


def test_multi_track_video_produces_one_identification_per_segment():
    video = {
        "video_id": "vid_mix", "title": "2 Hour Mix", "uploader": "DJ Channel",
        "description": TRACKLIST_DESCRIPTION, "duration_seconds": 540,
    }
    detection = _detection(MusicCategory.DJ_MIX)
    mb = FakeMB({
        ("artist one", "track one"): [{"musicbrainz_recording_id": "mb1", "artist": "Artist One",
                                        "track": "Track One", "recording_duration_seconds": 180}],
        ("artist two", "track two"): [{"musicbrainz_recording_id": "mb2", "artist": "Artist Two",
                                        "track": "Track Two", "recording_duration_seconds": 180}],
        ("artist three", "track three"): [{"musicbrainz_recording_id": "mb3", "artist": "Artist Three",
                                            "track": "Track Three", "recording_duration_seconds": 180}],
    })
    engine = ConfidenceEngine()
    results = identify_multi_track(video, detection, parse_title(video["title"]), mb, engine)

    assert len(results) == 3
    assert [r.track_index for r in results] == [0, 1, 2]
    assert results[0].track_offset_seconds == 0
    assert results[1].track_offset_seconds == 180
    assert results[0].selected.musicbrainz_recording_id == "mb1"
    assert results[1].selected.musicbrainz_recording_id == "mb2"
    assert results[2].selected.musicbrainz_recording_id == "mb3"


def test_segment_duration_derived_from_offsets_not_whole_video():
    video = {
        "video_id": "vid_mix", "title": "Mix", "uploader": "DJ Channel",
        "description": TRACKLIST_DESCRIPTION, "duration_seconds": 540,
    }
    detection = _detection(MusicCategory.DJ_MIX)
    mb = FakeMB({
        ("artist one", "track one"): [{"musicbrainz_recording_id": "mb1", "artist": "Artist One",
                                        "track": "Track One", "recording_duration_seconds": 178}],
    })
    engine = ConfidenceEngine()
    results = identify_multi_track(video, detection, parse_title(video["title"]), mb, engine)
    # Segment 0 spans 0-180s (180s), close to the 178s recording -> strong duration match,
    # not compared against the whole 540s mix.
    assert results[0].selected.duration_difference_seconds == 2


def test_no_tracklist_falls_back_to_single_whole_video_guess():
    video = {
        "video_id": "vid_mix", "title": "Untitled DJ Set", "uploader": "DJ Channel - Topic",
        "description": "no timestamps here", "duration_seconds": 5400,
    }
    detection = _detection(MusicCategory.DJ_MIX, description_has_tracklist=False)
    mb = FakeMB({})
    engine = ConfidenceEngine()
    results = identify_multi_track(video, detection, parse_title(video["title"]), mb, engine)
    assert len(results) == 1
    assert results[0].track_index is None
    assert any("no usable timestamped tracklist" in n for n in results[0].notes)


def test_single_track_category_passthrough_unaffected():
    video = {"video_id": "vid1", "title": "Artist - Track", "uploader": "Artist - Topic",
              "description": "", "duration_seconds": 200}
    detection = MusicDetectionResult(
        video_id="vid1", is_music=True, category=MusicCategory.SINGLE_TRACK, score=8,
        youtube_music_status=YoutubeMusicStatus.UNKNOWN, signals={},
        evaluated_at=datetime.now(timezone.utc),
    )
    mb = FakeMB({})
    engine = ConfidenceEngine()
    results = identify_multi_track(video, detection, parse_title(video["title"]), mb, engine)
    assert len(results) == 1
    assert results[0].track_index is None
