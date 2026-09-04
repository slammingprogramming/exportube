"""Title parsing: raw -> clean, with version info preserved separately.

Guiding rule (see docs/ARCHITECTURE.md "Title parsing philosophy" and spec section 12):
strip only purely decorative wrapper text ("[Official Video]", "(HD)"),
never information that distinguishes a recording version ("Live",
"Remix", "Acoustic", "2024 Remaster", "Radio Edit" ...). When in doubt,
KEEP the text in title_clean and let it fall through to matching evidence
rather than deleting it.

The parser never overwrites/loses the original: callers must keep
video_title_raw alongside video_title_clean.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from exportube.music_detection.signals import VERSION_MARKERS, TITLE_DASH_SPLIT_RE

BRACKET_RE = re.compile(r"[\[(]([^\[\]()]+)[\])]")

_DECORATIVE_PHRASES = {
    "official video", "official music video", "official audio",
    "official lyric video", "official lyrics video", "lyric video", "lyrics",
    "audio", "visualizer", "official", "hd", "hq", "4k", "uhd", "8k", "full song",
    "music video", "hd video", "4k video", "official 4k video", "video",
    "official visualizer", "full video", "official trailer",
    # Quality descriptor between "Official" and "Video" -- confirmed
    # against a real example ("Skyline Echo - Fading Light (Official HD Video)")
    # that this set originally missed. See music_detection/signals.py
    # OFFICIAL_VIDEO_TITLE_MARKER_RE, which has the matching fix.
    "official hd video", "official hq video", "official uhd video",
    "hq video", "uhd video",
}

_DECORATIVE_TRAILING_PHRASES = _DECORATIVE_PHRASES  # same set, used on dash-split suffixes

_EXTRA_VERSION_KEYWORDS = {
    "sped_up": re.compile(r"\bsped[\s-]?up\b", re.IGNORECASE),
    "slowed": re.compile(r"\bslowed( down)?\b", re.IGNORECASE),
    "nightcore": re.compile(r"\bnightcore\b", re.IGNORECASE),
    "8d_audio": re.compile(r"\b8d\s*audio\b", re.IGNORECASE),
    "clean": re.compile(r"\bclean( version)?\b", re.IGNORECASE),
    "explicit": re.compile(r"\bexplicit\b", re.IGNORECASE),
    "mono": re.compile(r"\bmono\b", re.IGNORECASE),
    "stereo": re.compile(r"\bstereo\b", re.IGNORECASE),
    "reprise": re.compile(r"\breprise\b", re.IGNORECASE),
    "bonus_track": re.compile(r"\bbonus track\b", re.IGNORECASE),
    "single_version": re.compile(r"\bsingle version\b", re.IGNORECASE),
    "album_version": re.compile(r"\balbum version\b", re.IGNORECASE),
}

ALL_VERSION_PATTERNS = {**VERSION_MARKERS, **_EXTRA_VERSION_KEYWORDS}


@dataclass
class TitleParseResult:
    raw_title: str
    clean_title: str
    artist_guess: str | None
    track_guess: str | None
    version_markers: list[str] = field(default_factory=list)
    removed_tags: list[str] = field(default_factory=list)


def _normalize_phrase(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _find_version_markers(text: str) -> list[str]:
    return sorted({name for name, pattern in ALL_VERSION_PATTERNS.items() if pattern.search(text)})


def _strip_decorative_brackets(title: str) -> tuple[str, list[str]]:
    removed = []

    def _replace(m: re.Match) -> str:
        content = m.group(1)
        normalized = _normalize_phrase(content)
        if _find_version_markers(content):
            return m.group(0)  # keep whole bracket, it carries version info
        if normalized in _DECORATIVE_PHRASES:
            removed.append(content.strip())
            return ""
        return m.group(0)  # unknown content: preserve, don't guess

    result = BRACKET_RE.sub(_replace, title)
    return result, removed


def _strip_decorative_dash_suffixes(title: str) -> tuple[str, list[str]]:
    removed = []
    segments = TITLE_DASH_SPLIT_RE.split(title)
    while len(segments) > 1:
        candidate = _normalize_phrase(segments[-1])
        if candidate in _DECORATIVE_TRAILING_PHRASES and not _find_version_markers(segments[-1]):
            removed.append(segments.pop().strip())
        else:
            break
    return " - ".join(s.strip() for s in segments), removed


def _collapse_whitespace_and_punct(title: str) -> str:
    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"\s*\|\s*$", "", title)
    title = re.sub(r"^[\s\-–—:|]+|[\s\-–—:|]+$", "", title)
    title = re.sub(r"\(\s*\)|\[\s*\]", "", title)
    return title.strip()


def parse_title(raw_title: str | None) -> TitleParseResult:
    if not raw_title or not raw_title.strip():
        return TitleParseResult(raw_title or "", "", None, None, [], [])

    raw_title = raw_title.strip()
    version_markers = _find_version_markers(raw_title)

    after_brackets, removed_brackets = _strip_decorative_brackets(raw_title)
    after_dash_strip, removed_suffixes = _strip_decorative_dash_suffixes(after_brackets)
    clean = _collapse_whitespace_and_punct(after_dash_strip)

    segments = [s.strip() for s in TITLE_DASH_SPLIT_RE.split(clean) if s.strip()]
    if len(segments) == 2:
        artist_guess, track_guess = segments[0], segments[1]
    elif len(segments) == 1:
        artist_guess, track_guess = None, segments[0]
    elif len(segments) > 2:
        artist_guess = segments[0]
        track_guess = " - ".join(segments[1:])
    else:
        artist_guess, track_guess = None, None

    return TitleParseResult(
        raw_title=raw_title,
        clean_title=clean,
        artist_guess=artist_guess or None,
        track_guess=track_guess or None,
        version_markers=version_markers,
        removed_tags=removed_brackets + removed_suffixes,
    )
