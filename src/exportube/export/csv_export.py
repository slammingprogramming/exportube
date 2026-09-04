"""CSV export (spec sections 15-21, 31).

Produces two files:

  youtube_music_history.csv  -- one row per identified/candidate YouTube
    occurrence. For an ordinary single-track video that's one row per
    video_id encountered in watch history. For a multi-track video (dj_mix
    /compilation/album_stream/live_or_concert) that had a parseable
    timestamped tracklist in its description, it's one row PER IDENTIFIED
    SEGMENT of that video -- same video_url/video_id/watch stats repeated
    across those rows, distinguished by `track_index`/
    `track_offset_seconds`/`track_end_offset_seconds` (spec section 11:
    "one video -> zero, one, or many identified recordings"). This is the
    rich, auditable "bridge" dataset: every field the spec calls for,
    including provenance/evidence, so uncertain rows can be reviewed
    rather than silently dropped.

  canonical_music_library.csv -- deduplicated by MusicBrainz recording ID
    (falling back to normalized artist+track when no MB match exists),
    aggregating watch stats across every YouTube video (and, for
    multi-track videos, every identified segment) that maps to the same
    recording.

Row construction always prefers a saved user correction over the automated
identification -- see _apply_correction. A correction on a multi-track
video collapses all of its segment rows back into a single row (the user
is asserting "this whole video is actually just X", overriding the
automated multi-track split -- see _apply_correction's docstring).  Every
row is still written even when identification failed (confidence_level =
unidentified / not_music), per spec section 16 ("preserve uncertainty" /
don't discard).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from exportube.history_import.url_parse import parse_video_url
from exportube.storage.db import Database

HISTORY_FIELDS = [
    "video_url", "video_id", "video_title_raw", "video_title_clean",
    "uploader", "channel_id", "availability",
    "artist", "track", "album", "release_group", "release_type", "release_country",
    "release_date", "video_upload_date", "first_watched_date", "latest_watched_date",
    "watch_count",
    "source", "source_playlist_name", "source_playlist_id", "youtube_music_status",
    "video_category", "potentially_multi_track", "track_index",
    "track_offset_seconds", "track_end_offset_seconds", "version_markers",
    "video_duration_seconds", "recording_duration_seconds", "duration_difference_seconds",
    "music_identification_confidence", "music_identification_score", "match_method",
    "musicbrainz_recording_id", "musicbrainz_release_id", "musicbrainz_release_group_id",
    "musicbrainz_artist_id", "isrc",
    "metadata_sources", "identification_evidence", "candidate_count",
    "manual_override", "identification_notes",
]

CANONICAL_FIELDS = [
    "musicbrainz_recording_id", "artist", "track", "album", "release_group",
    "release_type", "release_country", "release_date", "isrc",
    "musicbrainz_release_id", "musicbrainz_release_group_id", "musicbrainz_artist_id",
    "confidence_level", "confidence_score",
    "first_watched_date", "latest_watched_date", "watch_count",
    "youtube_video_count", "youtube_urls", "metadata_sources",
]

MULTI_TRACK_CATEGORIES = {"dj_mix", "compilation", "album_stream", "live_or_concert"}


def _apply_correction(base_row: dict, correction) -> dict:
    """A manual correction always collapses a video down to exactly one
    export row, even if it was automatically split into multiple
    identified segments -- accepting/editing a candidate for a multi-track
    video is the user asserting "this whole video is actually just X",
    which supersedes the automated split, not one segment of it (the
    review UI/CLI correction actions are video-scoped, not segment-scoped,
    in this version -- see AGENTS.md 'Known limitations')."""
    row = dict(base_row)
    action = correction["action"]
    payload = json.loads(correction["payload"]) if correction["payload"] else {}
    row["manual_override"] = True
    row["track_index"] = ""
    row["track_offset_seconds"] = ""
    row["track_end_offset_seconds"] = ""

    if action == "mark_not_music":
        row.update({
            "artist": "", "track": "", "album": "",
            "video_category": "non_music", "music_identification_confidence": "not_music",
            "music_identification_score": 0, "match_method": "manual_correction",
        })
    elif action == "mark_unidentified":
        row.update({
            "artist": "", "track": "", "album": "",
            "music_identification_confidence": "unidentified", "music_identification_score": 0,
            "match_method": "manual_correction",
        })
    elif action in ("accept_candidate", "manual_edit"):
        for field in ("artist", "track", "album", "release_group", "release_type",
                       "release_country", "release_date", "musicbrainz_recording_id",
                       "musicbrainz_release_id", "musicbrainz_release_group_id",
                       "musicbrainz_artist_id", "isrc"):
            if payload.get(field) is not None:
                row[field] = payload[field]
        row["music_identification_confidence"] = "high"
        row["music_identification_score"] = 1.0
        row["match_method"] = "manual_correction"

    row["identification_notes"] = (row.get("identification_notes") or "") + \
        f" [manually corrected: {action}]"
    return row


def _base_video_fields(video, stats, detection) -> dict:
    signals = json.loads(detection["signals"]) if detection and detection["signals"] else {}
    version_markers = signals.get("version_markers", [])
    return {
        "video_url": video["url"],
        "video_id": video["video_id"],
        "video_title_raw": video["title_raw"],
        "video_title_clean": video["title_clean"],
        "uploader": video["uploader"],
        "channel_id": video["channel_id"],
        "availability": video["availability"],
        "video_upload_date": video["upload_date"],
        "first_watched_date": stats["first_watched_date"],
        "latest_watched_date": stats["latest_watched_date"],
        "watch_count": stats["watch_count"],
        "source": ",".join(stats["sources"]),
        "source_playlist_name": ";".join(
            p["source_playlist_name"] for p in stats["source_playlists"] if p["source_playlist_name"]
        ) or "",
        "source_playlist_id": ";".join(
            (p["source_playlist_id"] or "") for p in stats["source_playlists"]
        ) or "",
        "youtube_music_status": detection["youtube_music_status"] if detection else "unknown",
        "video_category": detection["category"] if detection else "unknown",
        "potentially_multi_track": (detection["category"] in MULTI_TRACK_CATEGORIES) if detection else False,
        "version_markers": ",".join(version_markers),
        "video_duration_seconds": video["duration_seconds"],
        "manual_override": False,
    }


def _empty_identification_fields() -> dict:
    return {
        "artist": "", "track": "", "album": "", "release_group": "", "release_type": "",
        "release_country": "", "release_date": "",
        "track_index": "", "track_offset_seconds": "", "track_end_offset_seconds": "",
        "recording_duration_seconds": "", "duration_difference_seconds": "",
        "music_identification_confidence": "unidentified", "music_identification_score": 0,
        "match_method": "", "musicbrainz_recording_id": "", "musicbrainz_release_id": "",
        "musicbrainz_release_group_id": "", "musicbrainz_artist_id": "", "isrc": "",
        "metadata_sources": "", "identification_evidence": "{}", "candidate_count": 0,
        "identification_notes": "",
    }


def _identification_fields_from_row(sel_row, candidate_count: int, base_metadata_source: str | None) -> dict:
    metadata_sources = {base_metadata_source} if base_metadata_source else set()
    for src in json.loads(sel_row["metadata_sources"] or "[]"):
        metadata_sources.add(src)
    return {
        "artist": sel_row["artist"] or "",
        "track": sel_row["track"] or "",
        "album": sel_row["album"] or "",
        "release_group": sel_row["release_group"] or "",
        "release_type": sel_row["release_type"] or "",
        "release_country": sel_row["release_country"] or "",
        "release_date": sel_row["release_date"] or "",
        "track_index": sel_row["track_index"] if sel_row["track_index"] is not None else "",
        "track_offset_seconds": sel_row["track_offset_seconds"] if sel_row["track_offset_seconds"] is not None else "",
        "track_end_offset_seconds": sel_row["track_end_offset_seconds"] if sel_row["track_end_offset_seconds"] is not None else "",
        "recording_duration_seconds": sel_row["recording_duration_seconds"] or "",
        "duration_difference_seconds": sel_row["duration_difference_seconds"] or "",
        "music_identification_confidence": sel_row["confidence_level"] or "unidentified",
        "music_identification_score": sel_row["confidence_score"] or 0,
        "match_method": sel_row["match_method"] or "",
        "musicbrainz_recording_id": sel_row["musicbrainz_recording_id"] or "",
        "musicbrainz_release_id": sel_row["musicbrainz_release_id"] or "",
        "musicbrainz_release_group_id": sel_row["musicbrainz_release_group_id"] or "",
        "musicbrainz_artist_id": sel_row["musicbrainz_artist_id"] or "",
        "isrc": sel_row["isrc"] or "",
        "metadata_sources": ",".join(sorted(metadata_sources)),
        "identification_evidence": sel_row["evidence"] or "{}",
        "candidate_count": candidate_count,
        "identification_notes": "; ".join(json.loads(sel_row["notes"])) if sel_row["notes"] else "",
    }


def gather_export_rows(db: Database) -> list[dict]:
    rows = []
    corrections = db.all_latest_corrections()

    for video in db.all_videos():
        video_id = video["video_id"]
        stats = db.watch_stats_for_video(video_id)
        detection = db.get_music_detection(video_id)
        base = _base_video_fields(video, stats, detection)

        correction = corrections.get(video_id)
        if correction:
            # A correction always yields exactly one row -- see
            # _apply_correction's docstring for why this collapses any
            # automated multi-track split.
            selected_rows = db.get_selected_identifications(video_id)
            candidate_count = len(db.get_candidates(video_id))
            if selected_rows:
                ident_fields = _identification_fields_from_row(
                    selected_rows[0], candidate_count, video["metadata_source"])
            else:
                ident_fields = _empty_identification_fields()
                if detection and not detection["is_music"]:
                    ident_fields["music_identification_confidence"] = "not_music"
            rows.append(_apply_correction({**base, **ident_fields}, correction))
            continue

        all_candidates = db.get_candidates(video_id)
        if not all_candidates:
            # Either identification hasn't run yet, or it ran and found
            # zero candidates at all (including the NOT_MUSIC early-exit,
            # which never inserts rows) -- one blank/not_music row.
            ident_fields = _empty_identification_fields()
            if detection and not detection["is_music"]:
                ident_fields["music_identification_confidence"] = "not_music"
            rows.append({**base, **ident_fields})
            continue

        # Group by track_index: one row per group. A group with a
        # confident winner uses it; a group that exists (segment was
        # found and had candidates) but nothing cleared the confidence
        # threshold still gets its own row -- unidentified, not silently
        # merged away -- so "we found N segments in this mix, M of them
        # identified" stays visible rather than looking identical to
        # "we couldn't split this mix at all" (spec section 16).
        groups: dict = {}
        for c in all_candidates:
            groups.setdefault(c["track_index"], []).append(c)

        for track_index, group_candidates in sorted(groups.items(), key=lambda kv: (kv[0] is not None, kv[0])):
            selected = next((c for c in group_candidates if c["is_selected"]), None)
            if selected is not None:
                ident_fields = _identification_fields_from_row(
                    selected, len(group_candidates), video["metadata_source"])
            else:
                ident_fields = _empty_identification_fields()
                ident_fields["candidate_count"] = len(group_candidates)
                ident_fields["track_index"] = track_index if track_index is not None else ""
                ident_fields["track_offset_seconds"] = group_candidates[0]["track_offset_seconds"] \
                    if group_candidates[0]["track_offset_seconds"] is not None else ""
                ident_fields["track_end_offset_seconds"] = group_candidates[0]["track_end_offset_seconds"] \
                    if group_candidates[0]["track_end_offset_seconds"] is not None else ""
            rows.append({**base, **ident_fields})

    # Watch-history entries whose URL/title could never be resolved to a
    # video_id (deleted-before-metadata-existed, malformed URL, etc.) are
    # never discarded -- spec section 3. They have no video_id to key a
    # `videos` row on, so each unresolved entry becomes its own row here
    # rather than being silently dropped from the export.
    for entry in db.unresolved_history_entries():
        # Re-derive *why* this entry has no video_id -- e.g. a real Google
        # Takeout export can include YouTube Community post views
        # (https://www.youtube.com/post/...) mixed into watch history
        # alongside actual video watches (verified against a real export,
        # ~0.2% of entries there -- see AGENTS.md). url_type isn't
        # persisted on history_entries, so this re-parses the same raw URL
        # rather than adding a schema column for a display-only distinction.
        parsed_url = parse_video_url(entry["video_url_raw"])
        if parsed_url.url_type == "community_post":
            note = "This watch-history entry is a YouTube Community post (text/image), " \
                   "not a video -- there is no video ID to look up."
        else:
            note = "video_id could not be resolved from the recorded URL/title; " \
                   "metadata retrieval was not possible."

        rows.append({
            "video_url": entry["video_url_raw"] or "",
            "video_id": "",
            "video_title_raw": entry["raw_title"] or "",
            "video_title_clean": "",
            "uploader": entry["raw_channel_name"] or "",
            "channel_id": "",
            "availability": "unknown",
            "video_upload_date": "",
            "first_watched_date": entry["watched_at"],
            "latest_watched_date": entry["watched_at"],
            "watch_count": 1 if entry["source"] in ("takeout_json", "takeout_html", "youtube_session") else 0,
            "source": entry["source"] or "",
            "source_playlist_name": entry["source_playlist_name"] or "",
            "source_playlist_id": entry["source_playlist_id"] or "",
            "youtube_music_status": "unknown",
            "video_category": "community_post" if parsed_url.url_type == "community_post" else "unknown",
            "potentially_multi_track": False,
            "version_markers": "",
            "video_duration_seconds": "",
            "manual_override": False,
            **_empty_identification_fields(),
            "identification_notes": note,
        })

    return rows


def export_history_csv(db: Database, output_path: Path) -> int:
    rows = gather_export_rows(db)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)


def export_canonical_csv(db: Database, output_path: Path) -> int:
    rows = gather_export_rows(db)
    groups: dict[str, dict] = {}

    for row in rows:
        if not row.get("track"):
            continue  # unidentified / not-music rows have nothing canonical to aggregate
        key = row.get("musicbrainz_recording_id") or \
            f"noid::{(row.get('artist') or '').strip().lower()}::{row['track'].strip().lower()}"

        if key not in groups:
            groups[key] = {
                "musicbrainz_recording_id": row.get("musicbrainz_recording_id") or "",
                "artist": row.get("artist") or "",
                "track": row.get("track") or "",
                "album": row.get("album") or "",
                "release_group": row.get("release_group") or "",
                "release_type": row.get("release_type") or "",
                "release_country": row.get("release_country") or "",
                "release_date": row.get("release_date") or "",
                "isrc": row.get("isrc") or "",
                "musicbrainz_release_id": row.get("musicbrainz_release_id") or "",
                "musicbrainz_release_group_id": row.get("musicbrainz_release_group_id") or "",
                "musicbrainz_artist_id": row.get("musicbrainz_artist_id") or "",
                "confidence_level": row.get("music_identification_confidence") or "",
                "confidence_score": row.get("music_identification_score") or 0,
                "first_watched_date": row.get("first_watched_date"),
                "latest_watched_date": row.get("latest_watched_date"),
                "watch_count": 0,
                "youtube_urls": set(),
                "metadata_sources": set(),
            }

        g = groups[key]
        g["watch_count"] += row.get("watch_count") or 0
        if row.get("video_url"):
            g["youtube_urls"].add(row["video_url"])
        for src in (row.get("metadata_sources") or "").split(","):
            if src:
                g["metadata_sources"].add(src)

        for date_field, better in (("first_watched_date", min), ("latest_watched_date", max)):
            existing = g[date_field]
            candidate_val = row.get(date_field)
            if candidate_val and (not existing or better(existing, candidate_val) == candidate_val):
                g[date_field] = candidate_val

        try:
            if float(row.get("music_identification_score") or 0) > float(g["confidence_score"] or 0):
                g["confidence_score"] = row["music_identification_score"]
                g["confidence_level"] = row["music_identification_confidence"]
        except (TypeError, ValueError):
            pass

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CANONICAL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for g in groups.values():
            urls = sorted(g["youtube_urls"])
            g["youtube_video_count"] = len(urls)
            g["youtube_urls"] = ";".join(urls)
            g["metadata_sources"] = ",".join(sorted(g["metadata_sources"]))
            writer.writerow(g)

    return len(groups)
