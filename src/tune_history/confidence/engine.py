"""Weighted, multi-signal confidence scoring for a single candidate
recording identity (spec section 9).

score_candidate mutates candidate.evidence with the points actually
awarded (kept for provenance/export as `identification_evidence`) and
returns the normalized 0-1 score, ConfidenceLevel, and a `match_method`
string summarizing which evidence groups contributed.

Normalization: score = min(1.0, raw_points / reference_denominator), where
the denominator is the sum of all configured weights. Real matches rarely
trigger every weight simultaneously (e.g. "youtube_music_track_field" and
"title_exact_or_near_match" are somewhat redundant), so a strong match
with several independent signals typically lands in the 0.7-1.0 band while
a single weak signal lands well below the "low" threshold -- see
docs/METHODOLOGY.md for worked examples.
"""
from __future__ import annotations

from tune_history.confidence.weights import DEFAULT_THRESHOLDS, DEFAULT_WEIGHTS, EVIDENCE_GROUPS
from tune_history.matching.duration_match import DurationToleranceConfig, score_duration
from tune_history.matching.text_match import artist_similarity, is_fuzzy_match, text_similarity
from tune_history.storage.models import Candidate, ConfidenceLevel


class ConfidenceEngine:
    def __init__(self, weights: dict | None = None, thresholds: dict | None = None,
                 duration_cfg: DurationToleranceConfig | None = None,
                 fuzzy_threshold: float = 0.72):
        self.weights = {**DEFAULT_WEIGHTS, **(weights or {})}
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self.duration_cfg = duration_cfg or DurationToleranceConfig()
        self.fuzzy_threshold = fuzzy_threshold
        self._denominator = sum(self.weights.values())

    def score_candidate(self, candidate: Candidate, video: dict, title_parse,
                         channel_signals: dict) -> tuple[float, ConfidenceLevel, str]:
        evidence: dict[str, float] = {}

        if "youtube_music_track_field" in candidate.evidence:
            evidence["youtube_music_track_field"] = self.weights["youtube_music_track_field"]

        if video.get("yt_artist") and candidate.artist and \
                is_fuzzy_match(video["yt_artist"], candidate.artist, self.fuzzy_threshold):
            evidence["youtube_music_artist_field"] = self.weights["youtube_music_artist_field"]

        if video.get("yt_album") and candidate.album and \
                is_fuzzy_match(video["yt_album"], candidate.album, self.fuzzy_threshold):
            evidence["youtube_music_album_field"] = self.weights["youtube_music_album_field"]

        title_ref = title_parse.track_guess or title_parse.clean_title or video.get("title")
        title_sim = text_similarity(title_ref, candidate.track)
        if title_sim >= self.fuzzy_threshold:
            evidence["title_exact_or_near_match"] = self.weights["title_exact_or_near_match"] * title_sim

        artist_ref = title_parse.artist_guess or video.get("uploader")
        artist_sim = artist_similarity(artist_ref, candidate.artist)
        if artist_sim >= self.fuzzy_threshold:
            evidence["artist_name_match"] = self.weights["artist_name_match"] * artist_sim

        duration_score, duration_diff = score_duration(
            video.get("duration_seconds"), candidate.recording_duration_seconds, self.duration_cfg
        )
        candidate.duration_difference_seconds = duration_diff if duration_diff is not None else None
        if duration_score > 0:
            evidence["duration_match"] = self.weights["duration_match"] * duration_score

        if candidate.musicbrainz_recording_id:
            evidence["musicbrainz_match"] = self.weights["musicbrainz_match"]

        if candidate.isrc:
            evidence["isrc_match"] = self.weights["isrc_match"]

        # Any source besides YouTube itself/title-parsing/channel-identity
        # -- i.e. an actual metadata_enrichment provider lookup succeeded.
        # "musicbrainz" already earns musicbrainz_match above; this fires
        # for additional independent providers (Discogs today) so a
        # second corroborating lookup is still worth something even when
        # it isn't MusicBrainz -- see metadata_enrichment/multi_provider.py.
        non_mb_enrichment_sources = {
            s for s in candidate.sources
            if s not in ("youtube_music", "title_parse", "title_parse_whole", "title_parse_swapped",
                         "channel_identity", "musicbrainz", "musicbrainz_isrc", "description")
        }
        if non_mb_enrichment_sources:
            evidence["secondary_metadata_source_match"] = self.weights["secondary_metadata_source_match"]

        if channel_signals.get("topic_channel"):
            evidence["topic_channel_identity"] = self.weights["topic_channel_identity"]
        if channel_signals.get("official_artist_channel"):
            evidence["official_artist_channel"] = self.weights["official_artist_channel"]
        if channel_signals.get("vevo_channel"):
            evidence["vevo_channel"] = self.weights["vevo_channel"]

        if channel_signals.get("provided_to_youtube") or channel_signals.get("streaming_links"):
            evidence["description_evidence"] = self.weights["description_evidence"]

        if candidate.release_date:
            evidence["release_metadata_present"] = self.weights["release_metadata_present"]

        if len(set(candidate.sources)) > 1:
            evidence["multiple_candidate_agreement"] = self.weights["multiple_candidate_agreement"]

        candidate.evidence = evidence
        raw_points = sum(evidence.values())
        score = round(min(1.0, raw_points / self._denominator) if self._denominator else 0.0, 4)
        candidate.score = score

        level = self._level_for_score(score)
        match_method = self._match_method(evidence)
        return score, level, match_method

    def _level_for_score(self, score: float) -> ConfidenceLevel:
        if score >= self.thresholds["high"]:
            return ConfidenceLevel.HIGH
        if score >= self.thresholds["medium"]:
            return ConfidenceLevel.MEDIUM
        if score >= self.thresholds["low"]:
            return ConfidenceLevel.LOW
        return ConfidenceLevel.UNIDENTIFIED

    @staticmethod
    def _match_method(evidence: dict) -> str:
        groups = []
        for key in evidence:
            group = EVIDENCE_GROUPS.get(key, key)
            if group not in groups:
                groups.append(group)
        return "+".join(groups) if groups else "no_evidence"
