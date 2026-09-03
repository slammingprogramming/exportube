"""In-memory dataclasses mirroring the SQLite schema in db.py.

These are the shapes passed between pipeline stages. They are intentionally
plain dataclasses (not an ORM) so every module can be tested without a
database.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Availability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    PRIVATE = "private"
    DELETED = "deleted"
    UNKNOWN = "unknown"


class MusicCategory(str, Enum):
    SINGLE_TRACK = "single_track"
    LIVE_OR_CONCERT = "live_or_concert"
    COMPILATION = "compilation"
    DJ_MIX = "dj_mix"
    ALBUM_STREAM = "album_stream"
    MUSIC_VIDEO_WITH_EXTRAS = "music_video_with_extras"
    NON_MUSIC = "non_music"
    UNKNOWN = "unknown"


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNIDENTIFIED = "unidentified"
    NOT_MUSIC = "not_music"


class YoutubeMusicStatus(str, Enum):
    YOUTUBE_MUSIC = "youtube_music"
    REGULAR_UPLOAD = "regular_upload"
    UNKNOWN = "unknown"


@dataclass
class WatchEvent:
    """One row from the user's watch history, exactly as encountered.

    Never deduplicated or discarded during import, even if video_id/url is
    unresolvable -- see history_import/normalize.py.
    """

    video_id: str | None
    video_url_raw: str | None
    raw_title: str | None
    raw_channel_name: str | None
    watched_at: datetime | None
    source: str  # "takeout_json" | "takeout_html" | "youtube_session" | "youtube_api"
    source_playlist_name: str | None = None
    source_playlist_id: str | None = None
    import_batch_id: str | None = None
    raw_json: str | None = None  # original record, preserved for reproducibility
    id: int | None = None


@dataclass
class VideoRecord:
    video_id: str
    url: str
    title_raw: str | None = None
    title_clean: str | None = None
    uploader: str | None = None
    channel_id: str | None = None
    channel_url: str | None = None
    duration_seconds: float | None = None
    upload_date: str | None = None  # ISO date, video_upload_date (NOT release_date)
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    yt_track: str | None = None
    yt_artist: str | None = None
    yt_album: str | None = None
    yt_release_date: str | None = None
    yt_release_year: str | None = None
    availability: Availability = Availability.UNKNOWN
    metadata_fetched_at: datetime | None = None
    metadata_source: str | None = None  # "yt-dlp" | "youtube_api" | None
    raw_metadata_json: str | None = None


@dataclass
class MusicDetectionResult:
    video_id: str
    is_music: bool
    category: MusicCategory
    score: float
    youtube_music_status: YoutubeMusicStatus
    signals: dict = field(default_factory=dict)
    evaluated_at: datetime | None = None


@dataclass
class Candidate:
    """One candidate recording identity before/after MusicBrainz enrichment."""

    artist: str | None = None
    track: str | None = None
    album: str | None = None
    release_group: str | None = None
    release_type: str | None = None
    release_country: str | None = None
    release_date: str | None = None
    isrc: str | None = None
    recording_duration_seconds: float | None = None
    duration_difference_seconds: float | None = None
    musicbrainz_recording_id: str | None = None
    musicbrainz_release_id: str | None = None
    musicbrainz_release_group_id: str | None = None
    musicbrainz_artist_id: str | None = None
    evidence: dict = field(default_factory=dict)  # evidence_name -> points
    sources: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class Identification:
    video_id: str
    selected: Candidate | None
    alternatives: list[Candidate]
    confidence_score: float
    confidence_level: ConfidenceLevel
    match_method: str
    candidate_count: int
    notes: list[str] = field(default_factory=list)
    manual_override: bool = False
    created_at: datetime | None = None
    # Set only for one segment of a multi-track (dj_mix/compilation/
    # album_stream/live_or_concert) video -- see
    # music_identification.identifier.identify_multi_track(). None for an
    # ordinary single-track video's whole-video identification.
    track_index: int | None = None
    track_offset_seconds: float | None = None
    track_end_offset_seconds: float | None = None
