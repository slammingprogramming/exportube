from exportube.candidate_generation.title_parser import parse_title
from exportube.confidence.engine import ConfidenceEngine
from exportube.storage.models import Candidate, ConfidenceLevel


def test_strong_multi_signal_match_is_high_confidence():
    engine = ConfidenceEngine()
    video = {
        "title_raw": "Daft Punk - One More Time",
        "uploader": "Daft Punk - Topic",
        "yt_track": "One More Time", "yt_artist": "Daft Punk", "yt_album": "Discovery",
        "duration_seconds": 320,
    }
    title_parse = parse_title(video["title_raw"])
    candidate = Candidate(
        artist="Daft Punk", track="One More Time", album="Discovery",
        recording_duration_seconds=320, musicbrainz_recording_id="mb-rec-1",
        isrc="FR6V80300001", release_date="2001-03-07",
        evidence={"youtube_music_track_field": 1}, sources=["youtube_music", "musicbrainz"],
    )
    score, level, method = engine.score_candidate(candidate, video, title_parse, {
        "topic_channel": True, "provided_to_youtube": False,
    })
    assert level == ConfidenceLevel.HIGH
    assert score > 0.8
    assert "musicbrainz" in method


def test_weak_evidence_is_low_or_unidentified():
    engine = ConfidenceEngine()
    video = {"title_raw": "Random Video Title", "uploader": "Random Channel", "duration_seconds": 400}
    title_parse = parse_title(video["title_raw"])
    candidate = Candidate(artist=None, track="Random Video Title", evidence={}, sources=["title_parse"])
    score, level, method = engine.score_candidate(candidate, video, title_parse, {})
    assert level in (ConfidenceLevel.LOW, ConfidenceLevel.UNIDENTIFIED)


def test_duration_mismatch_reduces_score_vs_match():
    engine = ConfidenceEngine()
    video = {"title_raw": "Artist - Track", "uploader": "Artist - Topic", "duration_seconds": 240,
              "yt_track": "Track", "yt_artist": "Artist"}
    title_parse = parse_title(video["title_raw"])

    good = Candidate(artist="Artist", track="Track", recording_duration_seconds=238,
                      musicbrainz_recording_id="id1", evidence={}, sources=["youtube_music"])
    bad = Candidate(artist="Artist", track="Track", recording_duration_seconds=7200,
                     musicbrainz_recording_id="id2", evidence={}, sources=["youtube_music"])

    score_good, _, _ = engine.score_candidate(good, video, title_parse, {})
    score_bad, _, _ = engine.score_candidate(bad, video, title_parse, {})
    assert score_good > score_bad


def test_isrc_match_is_strong_evidence():
    engine = ConfidenceEngine()
    video = {"title_raw": "Some Title", "uploader": "Some Channel", "duration_seconds": 200}
    title_parse = parse_title(video["title_raw"])
    candidate = Candidate(artist="Artist", track="Track", isrc="USRC17607839",
                           musicbrainz_recording_id="id1", evidence={}, sources=["musicbrainz_isrc"])
    score, level, method = engine.score_candidate(candidate, video, title_parse, {})
    assert "isrc" in candidate.evidence or "musicbrainz_isrc" in method


def test_confidence_thresholds_configurable():
    engine = ConfidenceEngine(thresholds={"high": 0.99, "medium": 0.5, "low": 0.1})
    video = {"title_raw": "Artist - Track", "uploader": "Artist - Topic", "duration_seconds": 240,
             "yt_track": "Track", "yt_artist": "Artist"}
    title_parse = parse_title(video["title_raw"])
    candidate = Candidate(artist="Artist", track="Track", recording_duration_seconds=238,
                           musicbrainz_recording_id="id1", isrc="USRC17607839",
                           evidence={}, sources=["youtube_music"])
    score, level, method = engine.score_candidate(candidate, video, title_parse, {})
    assert level != ConfidenceLevel.HIGH  # threshold set unreachable on purpose
