"""Default evidence weights and confidence-level thresholds.

Nothing here is hard-coded as "always trusted" -- every weight is a
configurable point value (config/default_config.yaml `confidence.weights`
and `confidence.thresholds`), and ConfidenceEngine sums whichever pieces of
evidence a given candidate actually has.
"""
from __future__ import annotations

DEFAULT_WEIGHTS = {
    "youtube_music_track_field": 5,
    "youtube_music_artist_field": 5,
    "youtube_music_album_field": 4,
    "title_exact_or_near_match": 4,
    "artist_name_match": 4,
    "duration_match": 4,
    "musicbrainz_match": 5,
    "isrc_match": 9,
    # Any metadata_enrichment provider besides MusicBrainz (Discogs today)
    # independently returning the same candidate identity -- see
    # metadata_enrichment/multi_provider.py and confidence/engine.py.
    "secondary_metadata_source_match": 3,
    "topic_channel_identity": 3,
    "official_artist_channel": 3,
    "vevo_channel": 3,
    "description_evidence": 2,
    "release_metadata_present": 2,
    "multiple_candidate_agreement": 3,
}

DEFAULT_THRESHOLDS = {"high": 0.80, "medium": 0.55, "low": 0.30}

# Which evidence keys roll up into which human-readable match_method segment.
EVIDENCE_GROUPS = {
    "youtube_music_track_field": "youtube_music_metadata",
    "youtube_music_artist_field": "youtube_music_metadata",
    "youtube_music_album_field": "youtube_music_metadata",
    "title_exact_or_near_match": "title_text_match",
    "artist_name_match": "artist_text_match",
    "duration_match": "duration",
    "musicbrainz_match": "musicbrainz",
    "isrc_match": "musicbrainz_isrc",
    "secondary_metadata_source_match": "secondary_metadata_source",
    "topic_channel_identity": "channel_identity",
    "official_artist_channel": "channel_identity",
    "vevo_channel": "channel_identity",
    "description_evidence": "description",
    "release_metadata_present": "release_metadata",
    "multiple_candidate_agreement": "cross_source_agreement",
}


def load_weights(cfg) -> dict:
    configured = cfg.get("confidence.weights", {}) or {}
    return {**DEFAULT_WEIGHTS, **configured}


def load_thresholds(cfg) -> dict:
    configured = cfg.get("confidence.thresholds", {}) or {}
    return {**DEFAULT_THRESHOLDS, **configured}
