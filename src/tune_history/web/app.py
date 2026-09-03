"""Local review web UI (spec section 23).

Single-user, local-only Flask app: a dashboard (progress + confidence
distribution) and a review screen for uncertain matches where the user
can accept a candidate, mark a video unidentified/not-music, or manually
edit artist/track/album. Every action is persisted as a `corrections` row
(storage/db.py) and takes precedence over the automated identification at
export time (export/csv_export.py `_apply_correction`) -- corrections are
never lost or asked for twice on a later run.

This app uses a single shared sqlite3 connection (Database is not
thread-safe across connections by default) which is fine for the Flask
dev server's default single-threaded operation; it is intentionally not
built for multi-user/concurrent use.
"""
from __future__ import annotations

import json

from flask import Flask, redirect, render_template, request, url_for

from tune_history.export.csv_export import gather_export_rows
from tune_history.pipeline import Pipeline
from tune_history.storage.db import Database

UNCERTAIN_LEVELS = ("low", "unidentified")
MULTI_TRACK_CATEGORIES = ("dj_mix", "compilation", "album_stream", "live_or_concert")


def create_app(cfg) -> Flask:
    app = Flask(__name__)
    db = Database(cfg.db_path)
    app.config["TUNE_HISTORY_CFG"] = cfg
    app.config["TUNE_HISTORY_DB"] = db

    @app.route("/")
    def dashboard():
        counts = db.counts()
        rows = gather_export_rows(db)
        distribution = {}
        for row in rows:
            level = row.get("music_identification_confidence") or "unknown"
            distribution[level] = distribution.get(level, 0) + 1
        return render_template("dashboard.html", counts=counts, distribution=distribution)

    @app.route("/review")
    def review_list():
        rows = gather_export_rows(db)
        uncertain = [
            r for r in rows
            if r.get("music_identification_confidence") in UNCERTAIN_LEVELS
            or r.get("video_category") in MULTI_TRACK_CATEGORIES
        ]
        uncertain.sort(key=lambda r: r.get("music_identification_score") or 0, reverse=True)
        return render_template("review_list.html", rows=uncertain[:200], total=len(uncertain))

    @app.route("/review/<video_id>")
    def review_detail(video_id):
        video = db.get_video(video_id)
        detection = db.get_music_detection(video_id)
        candidates = db.get_candidates(video_id)  # all groups, ordered by track_index then rank
        # Group candidates by track_index so a multi-track video (dj_mix/
        # compilation/album_stream/live_or_concert with a parsed
        # tracklist) shows one candidate list per identified segment
        # instead of one flat merged list.
        groups: dict = {}
        for c in candidates:
            groups.setdefault(c["track_index"], []).append({
                "track_index_url": "none" if c["track_index"] is None else str(c["track_index"]),
                "rank": c["rank"], "is_selected": bool(c["is_selected"]),
                "artist": c["artist"], "track": c["track"], "album": c["album"],
                "release_date": c["release_date"], "recording_duration_seconds": c["recording_duration_seconds"],
                "duration_difference_seconds": c["duration_difference_seconds"],
                "confidence_score": c["confidence_score"],
                "musicbrainz_recording_id": c["musicbrainz_recording_id"],
                "evidence": json.loads(c["evidence"] or "{}"),
                "track_offset_seconds": c["track_offset_seconds"],
            })
        candidate_groups = [
            {"track_index": k, "offset_seconds": v[0]["track_offset_seconds"], "candidates": v}
            for k, v in sorted(groups.items(), key=lambda kv: (kv[0] is not None, kv[0]))
        ]
        return render_template(
            "review_detail.html", video=video, detection=detection,
            candidate_groups=candidate_groups, video_id=video_id,
        )

    @app.route("/review/<video_id>/accept/<track_index>/<int:rank>", methods=["POST"])
    def accept_candidate(video_id, track_index, rank):
        parsed_track_index = None if track_index == "none" else int(track_index)
        candidates = db.get_candidates(video_id, track_index=parsed_track_index)
        chosen = next((c for c in candidates if c["rank"] == rank), None)
        if chosen is not None:
            payload = {
                "artist": chosen["artist"], "track": chosen["track"], "album": chosen["album"],
                "release_group": chosen["release_group"], "release_type": chosen["release_type"],
                "release_country": chosen["release_country"], "release_date": chosen["release_date"],
                "musicbrainz_recording_id": chosen["musicbrainz_recording_id"],
                "musicbrainz_release_id": chosen["musicbrainz_release_id"],
                "musicbrainz_release_group_id": chosen["musicbrainz_release_group_id"],
                "musicbrainz_artist_id": chosen["musicbrainz_artist_id"],
                "isrc": chosen["isrc"],
            }
            db.save_correction(video_id, "accept_candidate", payload)
        return redirect(url_for("review_list"))

    @app.route("/review/<video_id>/manual", methods=["POST"])
    def manual_edit(video_id):
        payload = {
            "artist": request.form.get("artist") or None,
            "track": request.form.get("track") or None,
            "album": request.form.get("album") or None,
        }
        db.save_correction(video_id, "manual_edit", payload)
        return redirect(url_for("review_list"))

    @app.route("/review/<video_id>/unidentified", methods=["POST"])
    def mark_unidentified(video_id):
        db.save_correction(video_id, "mark_unidentified", {})
        return redirect(url_for("review_list"))

    @app.route("/review/<video_id>/not_music", methods=["POST"])
    def mark_not_music(video_id):
        db.save_correction(video_id, "mark_not_music", {})
        return redirect(url_for("review_list"))

    @app.route("/export", methods=["POST"])
    def trigger_export():
        result = Pipeline(db, cfg).export(cfg.output_dir)
        return render_template("export_done.html", result=result)

    return app
