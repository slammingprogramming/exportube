"""Multi-signal music detection.

Determines (a) whether a watched video plausibly contains identifiable
music at all, and (b) what *shape* of music content it is -- a single
track, a live/concert recording, a compilation, a DJ mix, an album stream,
a music video with substantial non-song material, or not music at all.
That category matters downstream: candidate_generation and
music_identification treat a single_track video very differently from a
dj_mix (where "identifying the song" may mean "identifying zero, one, or
many songs" -- see storage/models.Candidate / docs/ARCHITECTURE.md "multi-track model").

This is deliberately NOT keyword-only: see signals.py for the full list of
independent signals (YouTube Music panel data, channel identity, official
Data API category/topic data, description structure, duration, title
patterns) that are combined here with configurable weights. No single
signal is treated as proof.
"""
from __future__ import annotations

from datetime import datetime, timezone

from exportube.music_detection import signals as sig
from exportube.storage.models import MusicCategory, MusicDetectionResult, YoutubeMusicStatus

DEFAULT_WEIGHTS = {
    "yt_music_fields": 5,
    "topic_channel": 3,
    "vevo_channel": 3,
    "official_artist_channel": 2,
    "category_id_music": 3,
    "topic_categories_music": 2,
    "provided_to_youtube": 4,
    "streaming_links": 2,
    "isrc_present": 3,
    "tracklist_present": 1,
    "title_dash_split": 1,
    "official_video_title_marker": 3,
    "meta_content_keywords": -6,
}

DEFAULT_TYPICAL_SONG_MAX_SECONDS = 720  # 12 min; above this a plain "single track"
DEFAULT_TYPICAL_SONG_MIN_SECONDS = 30


class MusicDetector:
    def __init__(self, weights: dict | None = None, min_score_to_consider_music: float = 3,
                 long_form_duration_seconds: float = 1200, very_long_duration_seconds: float = 2700):
        self.weights = {**DEFAULT_WEIGHTS, **(weights or {})}
        self.min_score = min_score_to_consider_music
        self.long_form = long_form_duration_seconds
        self.very_long = very_long_duration_seconds

    def detect(self, video: dict) -> MusicDetectionResult:
        title = video.get("title")
        uploader = video.get("uploader")
        description = video.get("description")
        duration = video.get("duration_seconds")

        found = {
            "yt_music_fields": sig.has_yt_music_fields(video),
            "topic_channel": sig.is_topic_channel(uploader),
            "vevo_channel": sig.is_vevo_channel(uploader),
            "official_artist_channel": sig.looks_like_official_artist_channel(uploader),
            "category_id_music": sig.category_id_is_music(video),
            "topic_categories_music": sig.topic_categories_indicate_music(video),
            "provided_to_youtube": sig.description_has_provided_to_youtube(description),
            "streaming_links": sig.description_has_streaming_links(description),
            "isrc_present": sig.description_isrc(description) is not None,
            "tracklist_present": sig.description_has_tracklist(description),
            "title_dash_split": sig.title_has_dash_split(title),
            "official_video_title_marker": sig.title_has_official_video_marker(title),
            "meta_content_keywords": sig.matches_meta_content_keywords(title),
        }

        score = sum(self.weights[name] for name, present in found.items() if present)
        is_music = score >= self.min_score

        category = self._categorize(video, title, duration, found, is_music)
        yt_music_status = self._youtube_music_status(video, found)

        signals_out = {k: v for k, v in found.items() if v}
        signals_out["isrc"] = sig.description_isrc(description)
        signals_out["version_markers"] = sig.detect_version_markers(title) + sig.detect_version_markers(description)
        signals_out["score"] = score

        return MusicDetectionResult(
            video_id=video["video_id"],
            is_music=is_music,
            category=category,
            score=score,
            youtube_music_status=yt_music_status,
            signals=signals_out,
            evaluated_at=datetime.now(timezone.utc),
        )

    def _categorize(self, video: dict, title: str | None, duration: float | None,
                     found: dict, is_music: bool) -> MusicCategory:
        if not is_music:
            return MusicCategory.NON_MUSIC

        if sig.matches_mix_dj_keywords(title):
            return MusicCategory.DJ_MIX
        if sig.matches_album_stream_keywords(title):
            return MusicCategory.ALBUM_STREAM
        if sig.matches_compilation_keywords(title):
            return MusicCategory.COMPILATION
        if sig.matches_live_concert_keywords(title):
            return MusicCategory.LIVE_OR_CONCERT

        has_track_evidence = found["yt_music_fields"] or found["topic_channel"] or found["vevo_channel"]

        if duration is not None:
            if duration >= self.very_long:
                return MusicCategory.COMPILATION
            if duration >= self.long_form:
                # Long, but a specific-track signal (Topic channel, YT Music
                # fields, VEVO) outweighs the generic "long video" guess --
                # most likely a single track with a long intro/outro/credits,
                # not a live set or mix.
                return MusicCategory.MUSIC_VIDEO_WITH_EXTRAS if has_track_evidence \
                    else MusicCategory.LIVE_OR_CONCERT
            if found["tracklist_present"] and duration > DEFAULT_TYPICAL_SONG_MAX_SECONDS:
                return MusicCategory.COMPILATION

        version_markers = sig.detect_version_markers(title)
        if "live" in version_markers:
            return MusicCategory.LIVE_OR_CONCERT

        if duration is not None and DEFAULT_TYPICAL_SONG_MIN_SECONDS <= duration <= DEFAULT_TYPICAL_SONG_MAX_SECONDS:
            return MusicCategory.SINGLE_TRACK

        if has_track_evidence:
            # Strong per-track evidence even if duration is atypical (short
            # intro-heavy edit, or slightly long official video with outro).
            return MusicCategory.SINGLE_TRACK if (duration is None or duration <= self.long_form) \
                else MusicCategory.MUSIC_VIDEO_WITH_EXTRAS

        return MusicCategory.UNKNOWN

    @staticmethod
    def _youtube_music_status(video: dict, found: dict) -> YoutubeMusicStatus:
        if found["yt_music_fields"] or found["provided_to_youtube"]:
            return YoutubeMusicStatus.YOUTUBE_MUSIC
        if video.get("availability") == "available" and video.get("category_id") is not None \
                and video.get("category_id") != "10":
            return YoutubeMusicStatus.REGULAR_UPLOAD
        return YoutubeMusicStatus.UNKNOWN
