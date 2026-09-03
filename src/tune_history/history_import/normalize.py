"""Timestamp normalization and dedup-key computation shared by all
HistoryProvider implementations.

Date semantics reminder (see AGENTS.md "Date semantics"): everything here
produces `watched_at`, i.e. when the user encountered the video. It is
NEVER conflated with video_upload_date or release_date, which are
determined later by youtube_metadata / metadata_enrichment.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from dateutil import parser as dateutil_parser

# dateutil cannot resolve bare timezone-name abbreviations ("EDT", "PST",
# ...) on its own -- confirmed against a real Google Takeout
# watch-history.html export (spec section 4 / AGENTS.md "Needs
# verification against a real Google Takeout export"): every entry there
# is timestamped like "Aug 21, 2026, 11:04:24 PM EDT", and without this
# table dateutil silently drops the "EDT" and returns a NAIVE datetime
# still in local time, which normalize_timestamp would then wrongly stamp
# as if it were already UTC -- a multi-hour error on every single history
# entry from a real export. This table covers common North American/
# European/Australian/Asian abbreviations; anything not listed still falls
# through to the same naive-then-assume-UTC behavior (a remaining
# limitation for less common zones -- see AGENTS.md). Ambiguous
# abbreviations (e.g. "CST" is also China Standard Time) resolve to their
# most common English-locale meaning, since Takeout renders timestamps in
# the browser/account locale that produced the export.
_FIXED_OFFSET_TZINFOS = {
    name: timezone(timedelta(hours=offset_hours))
    for name, offset_hours in {
        "UTC": 0, "GMT": 0,
        "EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6,
        "PST": -8, "PDT": -7, "AKST": -9, "AKDT": -8, "HST": -10,
        "BST": 1, "WET": 0, "WEST": 1, "CET": 1, "CEST": 2, "EET": 2, "EEST": 3,
        "AEST": 10, "AEDT": 11, "ACST": 9.5, "ACDT": 10.5, "AWST": 8,
        "NZST": 12, "NZDT": 13, "IST": 5.5, "JST": 9, "KST": 9,
    }.items()
}


def normalize_timestamp(raw: str | None) -> datetime | None:
    """Parse a Takeout JSON ISO-8601 timestamp, a Takeout HTML formatted
    timestamp ("Jan 15, 2024, 3:14:21 AM PST"), or an API timestamp,
    into a timezone-aware UTC datetime. Returns None (not "now", not
    epoch-0) if unparseable -- the caller must retain the history record
    with watched_at = NULL rather than guess.
    """
    if not raw or not raw.strip():
        return None
    cleaned = raw.replace(" ", " ").strip()
    try:
        dt = dateutil_parser.parse(cleaned, tzinfos=_FIXED_OFFSET_TZINFOS)
    except (ValueError, OverflowError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_dedup_key(source: str, video_id: str | None, video_url_raw: str | None,
                       watched_at: datetime | None, raw_title: str | None,
                       raw_fallback: str | None = None) -> str:
    """Identify "the same history record" across re-imports of the same
    export so re-running `import` on an unchanged Takeout file is a no-op,
    while distinct repeat-watch events (different timestamps) remain
    separate rows.

    If watched_at is missing (unparseable timestamp), fall back to hashing
    the raw source line/object so we don't accidentally collapse distinct
    unparseable-timestamp entries into one.
    """
    parts = [
        source,
        video_id or "",
        video_url_raw or "",
        watched_at.isoformat() if watched_at else "",
        raw_title or "",
    ]
    if watched_at is None and raw_fallback:
        parts.append(raw_fallback)
    digest_input = "|".join(parts).encode("utf-8", errors="ignore")
    return hashlib.sha256(digest_input).hexdigest()
