"""Confirms a second metadata_enrichment provider (Discogs-shaped, via
FanOutProvider) actually earns confidence credit for corroborating a
candidate, distinct from and additive to musicbrainz_match."""
from datetime import datetime, timezone

from exportube.candidate_generation.title_parser import parse_title
from exportube.confidence.engine import ConfidenceEngine
from exportube.metadata_enrichment.base import MusicMetadataProvider
from exportube.metadata_enrichment.multi_provider import FanOutProvider
from exportube.music_identification.identifier import identify
from exportube.storage.models import MusicCategory, MusicDetectionResult, YoutubeMusicStatus


class FakeMB(MusicMetadataProvider):
    name = "musicbrainz"

    def search_recordings(self, artist, track, album=None, duration_seconds=None, limit=5):
        return [{"musicbrainz_recording_id": "mb1", "artist": "Artist", "track": "Track",
                  "recording_duration_seconds": 200}]

    def lookup_by_isrc(self, isrc):
        return []


class FakeDiscogs(MusicMetadataProvider):
    name = "discogs"

    def search_recordings(self, artist, track, album=None, duration_seconds=None, limit=5):
        return [{"artist": "Artist", "track": "Track", "release_date": "2020"}]

    def lookup_by_isrc(self, isrc):
        return []


def _video_and_detection():
    video = {"video_id": "vid1", "title": "Artist - Track", "uploader": "Artist - Topic",
             "description": "", "duration_seconds": 200}
    detection = MusicDetectionResult(
        video_id="vid1", is_music=True, category=MusicCategory.SINGLE_TRACK, score=8,
        youtube_music_status=YoutubeMusicStatus.UNKNOWN, signals={},
        evaluated_at=datetime.now(timezone.utc),
    )
    return video, detection


def test_musicbrainz_only_does_not_get_secondary_source_credit():
    video, detection = _video_and_detection()
    result = identify(video, detection, parse_title(video["title"]), FakeMB(), ConfidenceEngine())
    assert result.selected is not None
    assert "secondary_metadata_source_match" not in result.selected.evidence


def test_fan_out_with_discogs_adds_secondary_source_credit():
    video, detection = _video_and_detection()
    fan_out = FanOutProvider([FakeMB(), FakeDiscogs()])
    result = identify(video, detection, parse_title(video["title"]), fan_out, ConfidenceEngine())
    assert result.selected is not None
    # The MusicBrainz candidate and Discogs candidate should merge (same
    # artist/track identity) and the merged candidate should carry both
    # musicbrainz_match and secondary_metadata_source_match.
    assert "musicbrainz_match" in result.selected.evidence
    assert "secondary_metadata_source_match" in result.selected.evidence


def test_discogs_corroboration_raises_confidence_score():
    video, detection = _video_and_detection()
    mb_only_result = identify(video, detection, parse_title(video["title"]), FakeMB(), ConfidenceEngine())
    fan_out_result = identify(
        video, detection, parse_title(video["title"]),
        FanOutProvider([FakeMB(), FakeDiscogs()]), ConfidenceEngine(),
    )
    assert fan_out_result.confidence_score > mb_only_result.confidence_score
