"""Parses a timestamped tracklist out of a video description, for
multi-track videos (DJ mixes, compilations, album streams, live sets)
where a single video legitimately contains multiple recordings (spec
section 11).

Many uploaders of exactly this kind of content list timestamps in the
description, e.g.:

    0:00 Artist One - Track One
    3:45 Artist Two - Track Two (Live)
    [08:12] Track Three
    1. 12:30 - Artist Four - Track Four

`music_detection.signals.description_has_tracklist` already gates whether
a description looks tracklist-shaped (3+ timestamped lines) before this
module is invoked (see music_identification/identifier.py
identify_multi_track) -- this module does the actual line-by-line
extraction and reuses `candidate_generation.title_parser.parse_title` on
each entry's remainder text so the same decorative-tag-stripping /
version-marker-preserving logic applies per-segment as it does for whole
video titles.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from tune_history.candidate_generation.title_parser import TitleParseResult, parse_title

_TIMESTAMP = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})"
_LINE_RE = re.compile(
    rf"^\s*(?:[-*•]\s*)?(?:\d+[\.\)]\s*)?\[?{_TIMESTAMP}\]?\s*[-–—:.]?\s*(\S.*?)\s*$",
    re.MULTILINE,
)


@dataclass
class TracklistEntry:
    index: int
    offset_seconds: float
    end_offset_seconds: float | None
    raw_line: str
    title_parse: TitleParseResult


def _timestamp_to_seconds(hours: str | None, minutes: str, seconds: str) -> float:
    h = int(hours) if hours else 0
    m = int(minutes)
    s = int(seconds)
    return float(h * 3600 + m * 60 + s)


def parse_tracklist(description: str | None, video_duration_seconds: float | None = None) -> list[TracklistEntry]:
    if not description:
        return []

    raw_matches = []
    for m in _LINE_RE.finditer(description):
        hours, minutes, seconds, remainder = m.group(1), m.group(2), m.group(3), m.group(4)
        if not remainder or len(remainder.strip()) < 2:
            continue
        offset = _timestamp_to_seconds(hours, minutes, seconds)
        raw_matches.append((offset, remainder.strip(), m.group(0).strip()))

    if not raw_matches:
        return []

    # Keep first occurrence per offset, sort chronologically -- duplicate
    # timestamps (e.g. a repeated intro line) shouldn't produce duplicate
    # entries.
    seen_offsets: dict[float, tuple] = {}
    for offset, remainder, raw_line in raw_matches:
        seen_offsets.setdefault(offset, (remainder, raw_line))
    ordered = sorted(seen_offsets.items(), key=lambda kv: kv[0])

    entries: list[TracklistEntry] = []
    for i, (offset, (remainder, raw_line)) in enumerate(ordered):
        next_offset = ordered[i + 1][0] if i + 1 < len(ordered) else video_duration_seconds
        end_offset = next_offset if (next_offset is not None and next_offset > offset) else None
        entries.append(TracklistEntry(
            index=i,
            offset_seconds=offset,
            end_offset_seconds=end_offset,
            raw_line=raw_line,
            title_parse=parse_title(remainder),
        ))
    return entries
