"""Individual, independently-testable signals used by detector.py.

Each function inspects one piece of evidence and returns a plain value
(bool/str/list) -- no scoring here. Scoring/weighting lives in detector.py
so the evidence-extraction logic can be unit tested without caring about
weights, and weights can be retuned without touching extraction.
"""
from __future__ import annotations

import re

TOPIC_CHANNEL_RE = re.compile(r"-\s*Topic$")
VEVO_RE = re.compile(r"vevo", re.IGNORECASE)
OFFICIAL_SUFFIX_RE = re.compile(r"\bofficial\b", re.IGNORECASE)

# "Artist - Track" / "Artist – Track" / "Artist — Track" style separators.
TITLE_DASH_SPLIT_RE = re.compile(r"\s+[-–—]\s+")

# "Official Video"/"Official Music Video"/"Official Audio"/"Official Lyric
# Video" is a conventional marker artists and labels use specifically for
# song uploads (spec section 5, example E) -- distinct from "official"
# alone, which is too generic (official trailers, official channels for
# non-music content, etc).
#   "(Official Music Video)"  "[Official HD Video]"  "- Official 4K Video"
# A quality/resolution descriptor (HD/HQ/4K/UHD/1080p/...) commonly sits
# between "Official" and "Video" -- confirmed missing against a real
# example ("CAKE - The Distance (Official HD Video)") that this originally
# failed to recognize, dropping the video's music_detection score below
# threshold entirely. See AGENTS.md "Verified against a real Google
# Takeout export".
_QUALITY_WORD = r"(hd|hq|4k|uhd|8k|1080p?|720p?|high\s*quality)\s+"
OFFICIAL_VIDEO_TITLE_MARKER_RE = re.compile(
    rf"\((official\s+(music\s+)?({_QUALITY_WORD})?(video|audio|lyric(s)?\s*video))\)|"
    rf"\[(official\s+(music\s+)?({_QUALITY_WORD})?(video|audio|lyric(s)?\s*video))\]|"
    rf"-\s*official\s+(music\s+)?({_QUALITY_WORD})?(video|audio|lyric(s)?\s*video)\s*$",
    re.IGNORECASE,
)

PROVIDED_TO_YOUTUBE_RE = re.compile(r"provided to youtube by", re.IGNORECASE)
STREAMING_LINK_RE = re.compile(
    r"(open\.spotify\.com|music\.apple\.com|deezer\.com|tidal\.com|soundcloud\.com/(?!.*playlist))",
    re.IGNORECASE,
)
ISRC_RE = re.compile(r"\bISRC[:\s]*([A-Z]{2}[A-Z0-9]{3}\d{7})\b", re.IGNORECASE)

# A track-listing description has several "M:SS Title" or "H:MM:SS Title" lines.
TRACKLIST_LINE_RE = re.compile(r"^\s*(\d{1,2}:)?\d{1,2}:\d{2}\s+\S", re.MULTILINE)

MIX_DJ_KEYWORDS = re.compile(
    r"\b(dj set|full mix|continuous mix|megamix|non[- ]?stop|mixtape|"
    r"\d+\s*(hour|hr|min)s?\s*(mix|set)|drum\s*&?\s*bass mix)\b", re.IGNORECASE
)
COMPILATION_KEYWORDS = re.compile(
    r"\b(compilation|greatest hits|best of|top\s*\d+\s*(songs|tracks|hits)|"
    r"playlist|mega\s*mix|essential(s)?|anthology)\b", re.IGNORECASE
)
LIVE_CONCERT_KEYWORDS = re.compile(
    r"\b(live at|live in|live from|full concert|concert film|tour \d{4}|"
    r"live performance|unplugged|mtv unplugged|live session)\b", re.IGNORECASE
)
ALBUM_STREAM_KEYWORDS = re.compile(
    r"\b(full album|album stream|entire album|complete album)\b", re.IGNORECASE
)

# Videos ABOUT music/other topics, not videos OF music. Guards against
# false positives like "Why Taylor Swift's New Album Is Bad".
META_CONTENT_KEYWORDS = re.compile(
    r"\b(review|reaction|react(s|ing)?|explained|breakdown|interview|podcast|"
    r"episode|documentary|analysis|explain(s|ed)?|top\s*\d+\s*(songs|tracks)?\s*(used|in|from)|"
    r"worst|ranking|ranked|tier list|news|update|vlog|behind the scenes|making of|"
    r"how (to|.*was made)|q&a|ask me anything)\b", re.IGNORECASE
)
PODCAST_EPISODE_NUMBER_RE = re.compile(r"#\d{2,5}\b")

VERSION_MARKERS = {
    "live": re.compile(r"\blive\b", re.IGNORECASE),
    "remix": re.compile(r"\bremix\b", re.IGNORECASE),
    "acoustic": re.compile(r"\bacoustic\b", re.IGNORECASE),
    "extended_mix": re.compile(r"\bextended (mix|version)\b", re.IGNORECASE),
    "radio_edit": re.compile(r"\bradio edit\b", re.IGNORECASE),
    "remaster": re.compile(r"\bremaster(ed)?\b", re.IGNORECASE),
    "cover": re.compile(r"\bcover\b", re.IGNORECASE),
    "instrumental": re.compile(r"\binstrumental\b", re.IGNORECASE),
    "demo": re.compile(r"\bdemo\b", re.IGNORECASE),
    "edit": re.compile(r"\b(club|radio|extended|clean|dirty)\s+edit\b", re.IGNORECASE),
}

MUSIC_TOPIC_URL_HINTS = (
    "wikipedia.org/wiki/Music", "_music", "wikipedia.org/wiki/Hip_hop",
    "wikipedia.org/wiki/Pop_music", "wikipedia.org/wiki/Rock_music",
    "wikipedia.org/wiki/Independent_music", "wikipedia.org/wiki/Electronic_music",
    "wikipedia.org/wiki/Soul_music", "wikipedia.org/wiki/Country_music",
    "wikipedia.org/wiki/Jazz", "wikipedia.org/wiki/Classical_music",
    "wikipedia.org/wiki/Reggae", "wikipedia.org/wiki/Rhythm_and_blues",
    "wikipedia.org/wiki/Christian_music", "wikipedia.org/wiki/K-pop",
)


def is_topic_channel(uploader: str | None) -> bool:
    return bool(uploader and TOPIC_CHANNEL_RE.search(uploader.strip()))


def is_vevo_channel(uploader: str | None) -> bool:
    return bool(uploader and VEVO_RE.search(uploader))


def looks_like_official_artist_channel(uploader: str | None) -> bool:
    return bool(uploader and OFFICIAL_SUFFIX_RE.search(uploader))


def has_yt_music_fields(video: dict) -> bool:
    return bool(video.get("yt_track") or video.get("yt_artist"))


def title_has_dash_split(title: str | None) -> bool:
    return bool(title and TITLE_DASH_SPLIT_RE.search(title.strip()))


def title_has_official_video_marker(title: str | None) -> bool:
    return bool(title and OFFICIAL_VIDEO_TITLE_MARKER_RE.search(title))


def description_has_provided_to_youtube(description: str | None) -> bool:
    return bool(description and PROVIDED_TO_YOUTUBE_RE.search(description))


def description_has_streaming_links(description: str | None) -> bool:
    return bool(description and STREAMING_LINK_RE.search(description))


def description_isrc(description: str | None) -> str | None:
    if not description:
        return None
    m = ISRC_RE.search(description)
    return m.group(1).upper() if m else None


def description_has_tracklist(description: str | None) -> bool:
    if not description:
        return False
    return len(TRACKLIST_LINE_RE.findall(description)) >= 3


def detect_version_markers(text: str | None) -> list[str]:
    if not text:
        return []
    return [name for name, pattern in VERSION_MARKERS.items() if pattern.search(text)]


def matches_mix_dj_keywords(title: str | None) -> bool:
    return bool(title and MIX_DJ_KEYWORDS.search(title))


def matches_compilation_keywords(title: str | None) -> bool:
    return bool(title and COMPILATION_KEYWORDS.search(title))


def matches_live_concert_keywords(title: str | None) -> bool:
    return bool(title and LIVE_CONCERT_KEYWORDS.search(title))


def matches_album_stream_keywords(title: str | None) -> bool:
    return bool(title and ALBUM_STREAM_KEYWORDS.search(title))


def matches_meta_content_keywords(title: str | None) -> bool:
    if not title:
        return False
    if META_CONTENT_KEYWORDS.search(title):
        return True
    return bool(PODCAST_EPISODE_NUMBER_RE.search(title))


def category_id_is_music(video: dict) -> bool:
    return bool(video.get("is_music_category"))


def topic_categories_indicate_music(video: dict) -> bool:
    topics = video.get("topic_categories") or []
    return any(any(hint in t for hint in MUSIC_TOPIC_URL_HINTS) for t in topics)
