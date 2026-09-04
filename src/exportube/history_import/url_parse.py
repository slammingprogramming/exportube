"""Extract and normalize YouTube video IDs from every URL shape we're
likely to encounter in Takeout exports, browser history, or the API.

Supported forms:
  https://www.youtube.com/watch?v=ID[&list=...&index=...&t=...&...]
  https://m.youtube.com/watch?v=ID
  https://music.youtube.com/watch?v=ID
  https://youtu.be/ID[?t=...&si=...]
  https://www.youtube.com/shorts/ID
  https://www.youtube.com/embed/ID
  https://www.youtube.com/v/ID
  https://www.youtube.com/live/ID
  A bare 11-character video ID with no surrounding URL.

A valid YouTube video ID is 11 characters from [A-Za-z0-9_-]. We validate
against that shape to avoid false positives from unrelated query params.

Also recognized-but-intentionally-unresolvable (confirmed against a real
Google Takeout watch-history.html export -- see docs/ARCHITECTURE.md "Needs
verification against a real Google Takeout export"): YouTube Community
posts, `https://www.youtube.com/post/<post_id>`. A real account's watch
history can include entries for viewing a Community post (text/image,
not a video) alongside actual video watches -- roughly 0.2% of entries in
the export this was verified against. These correctly have no video ID
(there isn't one), but are tagged `url_type="community_post"` rather than
generic "unresolvable" so the export can say *why* precisely instead of
looking like a parse failure -- see export/csv_export.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_PATH_ID_PATTERNS = [
    re.compile(r"^/shorts/([A-Za-z0-9_-]{11})"),
    re.compile(r"^/embed/([A-Za-z0-9_-]{11})"),
    re.compile(r"^/v/([A-Za-z0-9_-]{11})"),
    re.compile(r"^/live/([A-Za-z0-9_-]{11})"),
]

_COMMUNITY_POST_RE = re.compile(r"^/post/")

YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "www.youtube-nocookie.com", "youtube-nocookie.com",
}


@dataclass
class ParsedVideoUrl:
    video_id: str | None
    canonical_url: str | None
    url_type: str  # "watch" | "shorts" | "youtu_be" | "embed" | "v" | "live" | "bare_id" | "unresolvable"
    playlist_id: str | None = None


def parse_video_url(raw: str | None) -> ParsedVideoUrl:
    if not raw or not raw.strip():
        return ParsedVideoUrl(None, None, "unresolvable")

    raw = raw.strip()

    if VIDEO_ID_RE.match(raw):
        return ParsedVideoUrl(raw, f"https://www.youtube.com/watch?v={raw}", "bare_id")

    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    except ValueError:
        return ParsedVideoUrl(None, None, "unresolvable")

    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host_bare = host
    else:
        host_bare = host

    if host_bare == "youtu.be" or host_bare == "www.youtu.be":
        vid = parsed.path.lstrip("/").split("/")[0]
        if VIDEO_ID_RE.match(vid):
            qs = parse_qs(parsed.query)
            playlist = qs.get("list", [None])[0]
            return ParsedVideoUrl(vid, f"https://www.youtube.com/watch?v={vid}", "youtu_be", playlist)
        return ParsedVideoUrl(None, None, "unresolvable")

    if host_bare not in YOUTUBE_HOSTS:
        return ParsedVideoUrl(None, None, "unresolvable")

    if _COMMUNITY_POST_RE.match(parsed.path):
        return ParsedVideoUrl(None, None, "community_post")

    qs = parse_qs(parsed.query)
    playlist = qs.get("list", [None])[0]

    if parsed.path in ("/watch", "/watch/"):
        vid = qs.get("v", [None])[0]
        if vid and VIDEO_ID_RE.match(vid):
            return ParsedVideoUrl(vid, f"https://www.youtube.com/watch?v={vid}", "watch", playlist)
        return ParsedVideoUrl(None, None, "unresolvable", playlist)

    for pattern, url_type in zip(
        _PATH_ID_PATTERNS, ("shorts", "embed", "v", "live")
    ):
        m = pattern.match(parsed.path)
        if m:
            vid = m.group(1)
            canonical = f"https://www.youtube.com/watch?v={vid}"
            if url_type == "shorts":
                canonical = f"https://www.youtube.com/shorts/{vid}"
            return ParsedVideoUrl(vid, canonical, url_type, playlist)

    # Fallback: some malformed/legacy URLs put the id in ?v= regardless of path
    vid = qs.get("v", [None])[0]
    if vid and VIDEO_ID_RE.match(vid):
        return ParsedVideoUrl(vid, f"https://www.youtube.com/watch?v={vid}", "watch", playlist)

    return ParsedVideoUrl(None, None, "unresolvable", playlist)
