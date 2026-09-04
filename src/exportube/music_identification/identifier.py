"""Ties candidate_generation + metadata_enrichment + matching + confidence
together into a final per-video Identification (spec section 9/16).

This module is intentionally storage-agnostic (pure function of
video/detection/title_parse -> Identification); pipeline.py is responsible
for persistence, caching wiring, and honoring manual corrections.

Multi-track videos (spec section 11): `identify()` handles the common
single_track case, identifying at most one recording. `identify_multi_track()`
handles dj_mix/compilation/album_stream/live_or_concert videos: it looks
for a timestamped tracklist in the description
(candidate_generation.tracklist_parser), and if one is found with at least
MIN_TRACKLIST_ENTRIES entries, runs `identify()` once per segment -- with
that segment's own artist/track guess and its own (offset-derived)
duration instead of the whole video's -- returning one `Identification`
per segment, each tagged with `track_index`/`track_offset_seconds`/
`track_end_offset_seconds`. If no usable tracklist exists (most DJ
sets/live sets have no per-song timestamps), it falls back to a single
whole-video guess via `identify()`, same as before, with a note explaining
why. Either way this returns a **list** of Identification objects;
pipeline.py's `identify()` stage always deals in that list (length 1 for
ordinary single-track videos) so there is exactly one code path for both
cases -- see `pipeline.Pipeline.identify`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from exportube.candidate_generation.candidates import build_seed_candidates
from exportube.candidate_generation.tracklist_parser import parse_tracklist
from exportube.confidence.engine import ConfidenceEngine
from exportube.storage.models import (
    Candidate, ConfidenceLevel, Identification, MusicCategory, MusicDetectionResult,
)

MULTI_TRACK_CATEGORIES = {
    MusicCategory.DJ_MIX, MusicCategory.COMPILATION, MusicCategory.ALBUM_STREAM,
    MusicCategory.LIVE_OR_CONCERT,
}

MIN_TRACKLIST_ENTRIES = 2


def _text_key(c: Candidate) -> tuple:
    return ((c.artist or "").strip().lower(), (c.track or "").strip().lower())


def identify(video: dict, detection: MusicDetectionResult, title_parse,
             mb_provider, confidence_engine: ConfidenceEngine, max_candidates: int = 8) -> Identification:
    if not detection.is_music or detection.category == MusicCategory.NON_MUSIC:
        return Identification(
            video_id=video["video_id"], selected=None, alternatives=[],
            confidence_score=0.0, confidence_level=ConfidenceLevel.NOT_MUSIC,
            match_method="not_music", candidate_count=0,
            notes=["Video did not pass music detection; treated as not music."],
            created_at=datetime.now(timezone.utc),
        )

    seeds = build_seed_candidates(video, title_parse)

    # Candidates are pooled by MusicBrainz recording ID when one is known
    # (two hits with different recording IDs are genuinely different
    # recordings/versions and must stay distinct, even if same-titled).
    # A candidate with NO recording ID (a plain title-parse seed, a
    # channel-identity guess, or a result from a provider that doesn't use
    # MusicBrainz IDs at all -- e.g. Discogs) instead attaches to whichever
    # already-pooled *identified* candidate shares its (artist, track)
    # text, so it can corroborate that candidate's evidence instead of
    # silently becoming an unmergeable duplicate -- see
    # test_secondary_provider_confidence.py.
    pool: dict[tuple, Candidate] = {}
    text_index: dict[tuple, tuple] = {}

    def _merge_into(existing: Candidate, c: Candidate):
        existing.sources = list(dict.fromkeys(existing.sources + c.sources))
        existing.evidence = {**c.evidence, **existing.evidence}
        for f in ("album", "release_date", "release_group", "release_type",
                  "release_country", "isrc", "musicbrainz_recording_id",
                  "musicbrainz_release_id", "musicbrainz_release_group_id",
                  "musicbrainz_artist_id", "recording_duration_seconds"):
            if not getattr(existing, f) and getattr(c, f):
                setattr(existing, f, getattr(c, f))

    def merge(c: Candidate):
        text_key = _text_key(c)
        if c.musicbrainz_recording_id:
            key = (c.musicbrainz_recording_id, *text_key)
            if key in pool:
                _merge_into(pool[key], c)
            else:
                pool[key] = c
                text_index.setdefault(text_key, key)
            return

        if text_key in text_index:
            _merge_into(pool[text_index[text_key]], c)
            return

        key = ("", *text_key)
        if key in pool:
            _merge_into(pool[key], c)
        else:
            pool[key] = c

    for seed in seeds:
        merge(seed)  # keep the un-enriched seed as its own scoreable fallback
        if mb_provider is None or not seed.track:
            continue
        try:
            mb_results = mb_provider.search_recordings(
                seed.artist, seed.track, seed.album, limit=5
            )
        except Exception:  # noqa: BLE001 - enrichment must never take the pipeline down
            mb_results = []
        for r in mb_results:
            # r["_provider"] is set by metadata_enrichment.multi_provider
            # .FanOutProvider when more than one enrichment source is
            # configured (e.g. Discogs alongside MusicBrainz); a bare
            # single-provider (the common case) has no such tag, so this
            # defaults to "musicbrainz" -- unchanged behavior either way.
            merge(Candidate(
                artist=r.get("artist"), track=r.get("track"), album=r.get("album"),
                release_group=r.get("release_group"), release_type=r.get("release_type"),
                release_country=r.get("release_country"), release_date=r.get("release_date"),
                isrc=r.get("isrc"), recording_duration_seconds=r.get("recording_duration_seconds"),
                musicbrainz_recording_id=r.get("musicbrainz_recording_id"),
                musicbrainz_release_id=r.get("musicbrainz_release_id"),
                musicbrainz_release_group_id=r.get("musicbrainz_release_group_id"),
                musicbrainz_artist_id=r.get("musicbrainz_artist_id"),
                evidence={}, sources=[r.get("_provider", "musicbrainz")] + seed.sources,
            ))

    isrc = detection.signals.get("isrc")
    if isrc and mb_provider is not None:
        try:
            isrc_results = mb_provider.lookup_by_isrc(isrc)
        except Exception:  # noqa: BLE001
            isrc_results = []
        for r in isrc_results:
            merge(Candidate(
                artist=r.get("artist"), track=r.get("track"), album=r.get("album"),
                release_group=r.get("release_group"), release_type=r.get("release_type"),
                release_country=r.get("release_country"), release_date=r.get("release_date"),
                isrc=r.get("isrc") or isrc, recording_duration_seconds=r.get("recording_duration_seconds"),
                musicbrainz_recording_id=r.get("musicbrainz_recording_id"),
                musicbrainz_release_id=r.get("musicbrainz_release_id"),
                musicbrainz_release_group_id=r.get("musicbrainz_release_group_id"),
                musicbrainz_artist_id=r.get("musicbrainz_artist_id"),
                evidence={}, sources=[f"{r.get('_provider', 'musicbrainz')}_isrc", "description"],
            ))

    candidates = list(pool.values())[:max_candidates]

    channel_signals = {
        "topic_channel": detection.signals.get("topic_channel", False),
        "official_artist_channel": detection.signals.get("official_artist_channel", False),
        "vevo_channel": detection.signals.get("vevo_channel", False),
        "provided_to_youtube": detection.signals.get("provided_to_youtube", False),
        "streaming_links": detection.signals.get("streaming_links", False),
    }

    scored = []
    for c in candidates:
        score, level, method = confidence_engine.score_candidate(c, video, title_parse, channel_signals)
        scored.append((score, level, method, c))

    scored.sort(key=lambda t: t[0], reverse=True)

    notes = []
    if not candidates:
        notes.append("No candidate artist/track identity could be derived from any available evidence.")

    if not scored or scored[0][0] < confidence_engine.thresholds["low"]:
        selected_level = ConfidenceLevel.UNIDENTIFIED
        selected_score = scored[0][0] if scored else 0.0
        selected_method = scored[0][2] if scored else "no_candidates"
        alternatives = [c for _, _, _, c in scored]
        return Identification(
            video_id=video["video_id"], selected=None, alternatives=alternatives,
            confidence_score=selected_score, confidence_level=selected_level,
            match_method=selected_method, candidate_count=len(candidates), notes=notes,
            created_at=datetime.now(timezone.utc),
        )

    best_score, best_level, best_method, best_candidate = scored[0]
    alternatives = [c for _, _, _, c in scored[1:]]

    return Identification(
        video_id=video["video_id"], selected=best_candidate, alternatives=alternatives,
        confidence_score=best_score, confidence_level=best_level, match_method=best_method,
        candidate_count=len(candidates), notes=notes, created_at=datetime.now(timezone.utc),
    )


def identify_multi_track(video: dict, detection: MusicDetectionResult, whole_video_title_parse,
                          mb_provider, confidence_engine: ConfidenceEngine,
                          max_candidates: int = 8) -> list[Identification]:
    """Entry point for dj_mix/compilation/album_stream/live_or_concert
    videos. Always returns at least one Identification (a plain list
    wrapping the same result `identify()` would give for the whole video,
    if no tracklist could be split out). See module docstring.
    """
    if detection.category not in MULTI_TRACK_CATEGORIES:
        return [identify(video, detection, whole_video_title_parse, mb_provider, confidence_engine, max_candidates)]

    entries = parse_tracklist(video.get("description"), video.get("duration_seconds"))

    if len(entries) < MIN_TRACKLIST_ENTRIES:
        result = identify(video, detection, whole_video_title_parse, mb_provider, confidence_engine, max_candidates)
        result.notes.append(
            f"music_detection classified this video as '{detection.category.value}' (likely "
            "contains multiple recordings), but no usable timestamped tracklist was found in the "
            "description to split it up. The identification above (if any) reflects only a single "
            "best guess for the whole video and should not be treated as exhaustive."
        )
        return [result]

    results = []
    for entry in entries:
        segment_video = dict(video)
        segment_video["duration_seconds"] = (
            entry.end_offset_seconds - entry.offset_seconds
            if entry.end_offset_seconds is not None else None
        )
        result = identify(segment_video, detection, entry.title_parse, mb_provider, confidence_engine, max_candidates)
        result.track_index = entry.index
        result.track_offset_seconds = entry.offset_seconds
        result.track_end_offset_seconds = entry.end_offset_seconds
        result.notes.append(
            f"Segment {entry.index} of a '{detection.category.value}' video, identified from a "
            f"timestamped tracklist entry at {entry.offset_seconds:.0f}s: \"{entry.raw_line}\"."
        )
        results.append(result)
    return results
