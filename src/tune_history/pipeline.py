"""Wires every stage together and owns resumability.

Each public method here corresponds to one CLI command / pipeline stage:
`import_history` -> `scan` -> `identify` -> `export`. Each is safe to
interrupt and re-run: `scan`/`identify` only process videos whose
`processing_status` for that stage isn't already 'done' (see
storage/db.py), and `import_history` is idempotent per watch-history
record via the dedup_key.

Data acquisition (import_history) is deliberately separable from
enrichment/matching (scan/identify): once history is imported and video
metadata cached, re-running `identify` with new confidence weights or a
newer MusicBrainz result never needs to re-hit yt-dlp/the network for
video metadata (spec section 20, "offline/reproducible processing").
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from tune_history.candidate_generation.title_parser import parse_title
from tune_history.confidence.engine import ConfidenceEngine
from tune_history.confidence.weights import load_thresholds, load_weights
from tune_history.export.csv_export import export_canonical_csv, export_history_csv
from tune_history.history_import.base import HistoryProvider
from tune_history.history_import.normalize import compute_dedup_key
from tune_history.history_import.url_parse import parse_video_url
from tune_history.matching.duration_match import DurationToleranceConfig
from tune_history.music_detection.detector import MusicDetector
from tune_history.music_identification.identifier import identify_multi_track
from tune_history.storage.cache import Cache
from tune_history.storage.db import Database
from tune_history.storage.models import MusicCategory, MusicDetectionResult, YoutubeMusicStatus

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, db: Database, config):
        self.db = db
        self.config = config

    # ------------------------------------------------------------------ import
    def import_history(self, provider: HistoryProvider) -> dict:
        def _entries():
            for event in provider.fetch():
                dedup_key = compute_dedup_key(
                    event.source, event.video_id, event.video_url_raw, event.watched_at,
                    event.raw_title, raw_fallback=event.raw_json,
                )
                entry = {
                    "video_id": event.video_id,
                    "video_url_raw": event.video_url_raw,
                    "raw_title": event.raw_title,
                    "raw_channel_name": event.raw_channel_name,
                    "watched_at": event.watched_at.isoformat() if event.watched_at else None,
                    "source": event.source,
                    "source_playlist_name": event.source_playlist_name,
                    "source_playlist_id": event.source_playlist_id,
                    "import_batch_id": event.import_batch_id,
                    "raw_json": event.raw_json,
                }
                yield entry, dedup_key

        # Batched (not per-row) commits -- see
        # Database.bulk_insert_history_entries docstring. Confirmed against
        # a real ~50,000-entry Google Takeout watch-history.html export
        # that per-row commits make `import` prohibitively slow at that
        # scale (spec section 19).
        new_count, duplicate_count, unresolved_count = self.db.bulk_insert_history_entries(_entries())
        return {"new": new_count, "duplicates": duplicate_count, "unresolved": unresolved_count}

    # -------------------------------------------------------------------- scan
    def scan(self, metadata_provider, api_provider=None, progress_cb=None, limit: int | None = None) -> dict:
        video_ids = self.db.videos_pending("metadata")
        # Include video_ids that only exist in history_entries but never
        # got a `videos` row yet (first run).
        all_known = set(self.db.distinct_video_ids())
        already_have_row = {v["video_id"] for v in self.db.all_videos()}
        video_ids = sorted(set(video_ids) | (all_known - already_have_row))
        if limit is not None:
            # Process only the first `limit` pending videos this call --
            # for trying the pipeline against a slice of a large history
            # before committing to a full (potentially hours-long, given
            # yt-dlp/MusicBrainz rate limits) run. Still resumable: the
            # rest stay `pending` and a later call without --limit (or
            # with a higher one) picks up where this left off.
            video_ids = video_ids[:limit]

        ttl_days = self.config.get("storage.cache_ttl_days.youtube_metadata", 30)
        cache = Cache(self.db, ttl_days)

        detector = MusicDetector(
            min_score_to_consider_music=self.config.get("music_detection.min_score_to_consider_music", 3),
            long_form_duration_seconds=self.config.get("music_detection.long_form_duration_seconds", 1200),
            very_long_duration_seconds=self.config.get("music_detection.very_long_duration_seconds", 2700),
        )

        done, errors = 0, 0
        for vid in video_ids:
            try:
                video = cache.get_or_fetch("youtube_metadata", vid, lambda vid=vid: metadata_provider.fetch_one(vid))
                if api_provider is not None:
                    api_data = cache.get_or_fetch(
                        "youtube_api_metadata", vid, lambda vid=vid: api_provider.fetch_one(vid)
                    )
                    for key in ("category_id", "is_music_category", "topic_categories"):
                        if key in api_data:
                            video[key] = api_data[key]

                title_parse = parse_title(video.get("title"))
                self.db.upsert_video({
                    "video_id": vid,
                    "url": video.get("url") or f"https://www.youtube.com/watch?v={vid}",
                    "title_raw": video.get("title"),
                    "title_clean": title_parse.clean_title,
                    "uploader": video.get("uploader"),
                    "channel_id": video.get("channel_id"),
                    "channel_url": video.get("channel_url"),
                    "duration_seconds": video.get("duration_seconds"),
                    "upload_date": video.get("upload_date"),
                    "description": video.get("description"),
                    "tags": _json(video.get("tags")),
                    "categories": _json(video.get("categories")),
                    "yt_track": video.get("yt_track"),
                    "yt_artist": video.get("yt_artist"),
                    "yt_album": video.get("yt_album"),
                    "yt_release_date": video.get("yt_release_date"),
                    "yt_release_year": video.get("yt_release_year"),
                    "availability": video.get("availability", "unknown"),
                    "metadata_fetched_at": None,
                    "metadata_source": video.get("metadata_source"),
                    "raw_metadata_json": _json(video.get("raw")),
                })
                self.db.set_stage_status(vid, "metadata", "done")

                detection = detector.detect(video)
                self.db.save_music_detection({
                    "video_id": vid, "is_music": detection.is_music,
                    "category": detection.category.value, "score": detection.score,
                    "youtube_music_status": detection.youtube_music_status.value,
                    "signals": detection.signals,
                })
                self.db.set_stage_status(vid, "detection", "done")
                done += 1
            except Exception as e:  # noqa: BLE001
                logger.exception("scan failed for %s", vid)
                self.db.set_stage_status(vid, "metadata", "error", str(e))
                errors += 1
            if progress_cb:
                progress_cb(done + errors, len(video_ids))

        return {"processed": done, "errors": errors, "total": len(video_ids)}

    # --------------------------------------------------------------- identify
    def identify(self, mb_provider, progress_cb=None, limit: int | None = None) -> dict:
        video_ids = self.db.videos_pending("identification")
        if limit is not None:
            video_ids = video_ids[:limit]
        duration_cfg = DurationToleranceConfig.from_config(self.config)
        engine = ConfidenceEngine(
            weights=load_weights(self.config), thresholds=load_thresholds(self.config),
            duration_cfg=duration_cfg,
            fuzzy_threshold=self.config.get("matching.text.fuzzy_match_threshold", 0.72),
        )

        done, errors = 0, 0
        for vid in video_ids:
            try:
                video_row = self.db.get_video(vid)
                detection_row = self.db.get_music_detection(vid)
                if video_row is None or detection_row is None:
                    continue

                video = dict(video_row)
                video["tags"] = _unjson(video.get("tags"))
                video["categories"] = _unjson(video.get("categories"))
                # DB column is title_raw; candidate_generation/confidence
                # expect the metadata-provider key "title" as a fallback.
                video["title"] = video.get("title_raw")

                detection = MusicDetectionResult(
                    video_id=vid, is_music=bool(detection_row["is_music"]),
                    category=MusicCategory(detection_row["category"]),
                    score=detection_row["score"],
                    youtube_music_status=YoutubeMusicStatus(detection_row["youtube_music_status"]),
                    signals=json.loads(detection_row["signals"] or "{}"),
                )

                title_parse = parse_title(video.get("title_raw"))
                # identify_multi_track() dispatches internally: for an
                # ordinary single_track video it returns a length-1 list
                # equivalent to identify_video() directly; for a dj_mix/
                # compilation/album_stream/live_or_concert video with a
                # parseable description tracklist it returns one
                # Identification per segment (see music_identification
                # /identifier.py).
                results = identify_multi_track(video, detection, title_parse, mb_provider, engine)

                groups = []
                for result in results:
                    all_candidates = ([result.selected] if result.selected else []) + result.alternatives
                    candidate_dicts = [asdict(c) for c in all_candidates]
                    selected_index = 0 if result.selected is not None else None
                    groups.append({
                        "candidates": candidate_dicts, "selected_index": selected_index,
                        "confidence_score": result.confidence_score,
                        "confidence_level": result.confidence_level.value,
                        "match_method": result.match_method, "notes": result.notes,
                        "track_index": result.track_index,
                        "track_offset_seconds": result.track_offset_seconds,
                        "track_end_offset_seconds": result.track_end_offset_seconds,
                    })

                self.db.save_multi_track_identifications(vid, groups)
                self.db.set_stage_status(vid, "identification", "done")
                done += 1
            except Exception as e:  # noqa: BLE001
                logger.exception("identify failed for %s", vid)
                self.db.set_stage_status(vid, "identification", "error", str(e))
                errors += 1
            if progress_cb:
                progress_cb(done + errors, len(video_ids))

        return {"processed": done, "errors": errors, "total": len(video_ids)}

    # ----------------------------------------------------------------- export
    def export(self, output_dir: Path) -> dict:
        history_name = self.config.get("export.history_csv_name", "youtube_music_history.csv")
        canonical_name = self.config.get("export.canonical_csv_name", "canonical_music_library.csv")
        history_path = output_dir / history_name
        canonical_path = output_dir / canonical_name

        history_rows = export_history_csv(self.db, history_path)
        canonical_rows = export_canonical_csv(self.db, canonical_path)

        return {
            "history_csv": str(history_path), "history_rows": history_rows,
            "canonical_csv": str(canonical_path), "canonical_rows": canonical_rows,
        }


def _json(value):
    return json.dumps(value) if value is not None else None


def _unjson(value):
    if not value:
        return []
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return []
