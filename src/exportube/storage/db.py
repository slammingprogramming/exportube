"""SQLite storage layer.

Design goals (see AGENTS.md "Storage & resumability"):
  * Every watch-history entry is retained forever, even duplicates and
    unresolvable ones (history_entries table is append-only per import
    batch; re-importing the same file is idempotent via a content hash).
  * Every pipeline stage persists its output keyed by video_id so a crash
    or interruption loses at most the in-flight video, not the whole run.
    `processing_status` tracks per-stage completion so `scan`/`identify`
    can resume by only selecting rows that are not yet done.
  * User corrections from the review UI/CLI are stored separately from
    automated identifications and always take precedence on export/rerun.
  * Plain sqlite3 (stdlib), no ORM -- schema is small and explicit.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS history_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT,
    video_url_raw TEXT,
    raw_title TEXT,
    raw_channel_name TEXT,
    watched_at TEXT,
    source TEXT NOT NULL,
    source_playlist_name TEXT,
    source_playlist_id TEXT,
    import_batch_id TEXT,
    raw_json TEXT,
    dedup_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(dedup_key)
);
CREATE INDEX IF NOT EXISTS idx_history_video_id ON history_entries(video_id);
CREATE INDEX IF NOT EXISTS idx_history_watched_at ON history_entries(watched_at);

CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    url TEXT,
    title_raw TEXT,
    title_clean TEXT,
    uploader TEXT,
    channel_id TEXT,
    channel_url TEXT,
    duration_seconds REAL,
    upload_date TEXT,
    description TEXT,
    tags TEXT,
    categories TEXT,
    yt_track TEXT,
    yt_artist TEXT,
    yt_album TEXT,
    yt_release_date TEXT,
    yt_release_year TEXT,
    availability TEXT NOT NULL DEFAULT 'unknown',
    metadata_fetched_at TEXT,
    metadata_source TEXT,
    raw_metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS processing_status (
    video_id TEXT PRIMARY KEY REFERENCES videos(video_id),
    metadata_status TEXT NOT NULL DEFAULT 'pending',
    detection_status TEXT NOT NULL DEFAULT 'pending',
    identification_status TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS music_detection (
    video_id TEXT PRIMARY KEY REFERENCES videos(video_id),
    is_music INTEGER NOT NULL,
    category TEXT NOT NULL,
    score REAL NOT NULL,
    youtube_music_status TEXT NOT NULL,
    signals TEXT,
    evaluated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS identifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(video_id),
    -- track_index is NULL for an ordinary single-track video (one
    -- identification group per video). For a multi-track video (dj_mix /
    -- compilation / album_stream / live_or_concert with a parsed
    -- description tracklist), each identified segment gets its own
    -- track_index (0, 1, 2, ...) with its own candidate/rank set and its
    -- own is_selected winner -- see music_identification/identifier.py
    -- identify_multi_track().
    track_index INTEGER,
    track_offset_seconds REAL,
    track_end_offset_seconds REAL,
    rank INTEGER NOT NULL DEFAULT 0,
    is_selected INTEGER NOT NULL DEFAULT 0,
    artist TEXT,
    track TEXT,
    album TEXT,
    release_group TEXT,
    release_type TEXT,
    release_country TEXT,
    release_date TEXT,
    isrc TEXT,
    recording_duration_seconds REAL,
    duration_difference_seconds REAL,
    musicbrainz_recording_id TEXT,
    musicbrainz_release_id TEXT,
    musicbrainz_release_group_id TEXT,
    musicbrainz_artist_id TEXT,
    confidence_score REAL,
    confidence_level TEXT,
    match_method TEXT,
    metadata_sources TEXT,
    evidence TEXT,
    candidate_count INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ident_video_id ON identifications(video_id);
CREATE INDEX IF NOT EXISTS idx_ident_selected ON identifications(video_id, is_selected);
CREATE INDEX IF NOT EXISTS idx_ident_track ON identifications(video_id, track_index);

CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    action TEXT NOT NULL,  -- accept_candidate | manual_edit | mark_unidentified | mark_not_music
    payload TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_corrections_video_id ON corrections(video_id);

CREATE TABLE IF NOT EXISTS cache_entries (
    cache_key TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    response_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_namespace ON cache_entries(namespace);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Sources that represent an actual watch event. Playlist-membership sources
# (takeout_playlist, youtube_api_playlist) record "this video is in a
# playlist you have", not "you watched this" -- they must not inflate
# watch_count or be mistaken for watch dates (spec section 18: don't
# confuse "playlist date added" with watching). They still contribute
# source_playlist_name/id context via watch_stats_for_video's separate
# playlists query below.
WATCH_SOURCES = ("takeout_json", "takeout_html", "youtube_session")


class Database:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ---------------------------------------------------------------- history
    def insert_history_entry(self, entry: dict, dedup_key: str) -> bool:
        """Insert a raw watch-history row. Returns False if it was a
        duplicate of an already-imported row (same dedup_key), which is
        expected and not an error -- Takeout exports and repeat imports
        commonly repeat entries verbatim."""
        try:
            with self.tx() as conn:
                conn.execute(
                    """INSERT INTO history_entries
                    (video_id, video_url_raw, raw_title, raw_channel_name, watched_at,
                     source, source_playlist_name, source_playlist_id, import_batch_id,
                     raw_json, dedup_key, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        entry.get("video_id"),
                        entry.get("video_url_raw"),
                        entry.get("raw_title"),
                        entry.get("raw_channel_name"),
                        entry.get("watched_at"),
                        entry.get("source"),
                        entry.get("source_playlist_name"),
                        entry.get("source_playlist_id"),
                        entry.get("import_batch_id"),
                        entry.get("raw_json"),
                        dedup_key,
                        _now(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def bulk_insert_history_entries(self, items, batch_size: int = 1000) -> tuple[int, int, int]:
        """Like insert_history_entry, but for importing many rows at once
        (`items` is an iterable of (entry_dict, dedup_key) pairs).

        Committing once per row (the naive approach, and what
        insert_history_entry above does when called in a loop) is
        prohibitively slow at the scale spec section 19 calls for --
        confirmed against a real Google Takeout export with ~50,000 watch-
        history entries, where per-row commits made a single `import` run
        take unacceptably long (each SQLite commit forces a disk sync).
        This commits every `batch_size` rows instead, which is still
        crash-safe (an interruption loses at most one partial batch, not
        the whole import -- `import` is idempotent via dedup_key anyway,
        so re-running after a crash just re-skips everything already
        committed) and several orders of magnitude faster in practice.
        A single row's UNIQUE-constraint failure (duplicate) does not
        abort the surrounding transaction in SQLite -- it's caught and
        counted, and the batch continues normally.
        """
        new_count, duplicate_count, new_unresolved_count = 0, 0, 0
        cur = self.conn.cursor()
        pending = 0
        for entry, dedup_key in items:
            try:
                cur.execute(
                    """INSERT INTO history_entries
                    (video_id, video_url_raw, raw_title, raw_channel_name, watched_at,
                     source, source_playlist_name, source_playlist_id, import_batch_id,
                     raw_json, dedup_key, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        entry.get("video_id"),
                        entry.get("video_url_raw"),
                        entry.get("raw_title"),
                        entry.get("raw_channel_name"),
                        entry.get("watched_at"),
                        entry.get("source"),
                        entry.get("source_playlist_name"),
                        entry.get("source_playlist_id"),
                        entry.get("import_batch_id"),
                        entry.get("raw_json"),
                        dedup_key,
                        _now(),
                    ),
                )
                new_count += 1
                if not entry.get("video_id"):
                    new_unresolved_count += 1
            except sqlite3.IntegrityError:
                duplicate_count += 1
            pending += 1
            if pending >= batch_size:
                self.conn.commit()
                pending = 0
        self.conn.commit()
        return new_count, duplicate_count, new_unresolved_count

    def distinct_video_ids(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT video_id FROM history_entries WHERE video_id IS NOT NULL"
        ).fetchall()
        return [r["video_id"] for r in rows]

    def watch_stats_for_video(self, video_id: str) -> dict:
        placeholders = ",".join("?" for _ in WATCH_SOURCES)
        row = self.conn.execute(
            f"""SELECT MIN(watched_at) as first_watched, MAX(watched_at) as latest_watched,
                      COUNT(CASE WHEN source IN ({placeholders}) THEN 1 END) as watch_count
               FROM history_entries WHERE video_id = ?""",
            (*WATCH_SOURCES, video_id),
        ).fetchone()
        playlists = self.conn.execute(
            """SELECT DISTINCT source_playlist_name, source_playlist_id FROM history_entries
               WHERE video_id = ? AND source_playlist_name IS NOT NULL""",
            (video_id,),
        ).fetchall()
        sources = self.conn.execute(
            "SELECT DISTINCT source FROM history_entries WHERE video_id = ?", (video_id,)
        ).fetchall()
        return {
            "first_watched_date": row["first_watched"],
            "latest_watched_date": row["latest_watched"],
            "watch_count": row["watch_count"],
            "source_playlists": [dict(p) for p in playlists],
            "sources": [s["source"] for s in sources],
        }

    def unresolved_history_entries(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM history_entries WHERE video_id IS NULL"
        ).fetchall()

    # ------------------------------------------------------------------ videos
    def upsert_video(self, video: dict) -> None:
        cols = [
            "video_id", "url", "title_raw", "title_clean", "uploader", "channel_id",
            "channel_url", "duration_seconds", "upload_date", "description", "tags",
            "categories", "yt_track", "yt_artist", "yt_album", "yt_release_date",
            "yt_release_year", "availability", "metadata_fetched_at", "metadata_source",
            "raw_metadata_json",
        ]
        values = [video.get(c) for c in cols]
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "video_id")
        with self.tx() as conn:
            conn.execute(
                f"""INSERT INTO videos ({",".join(cols)}) VALUES ({placeholders})
                    ON CONFLICT(video_id) DO UPDATE SET {updates}""",
                values,
            )
            conn.execute(
                """INSERT INTO processing_status (video_id, updated_at) VALUES (?, ?)
                   ON CONFLICT(video_id) DO NOTHING""",
                (video["video_id"], _now()),
            )

    def get_video(self, video_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()

    def all_videos(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM videos").fetchall()

    def videos_pending(self, stage: str) -> list[str]:
        col = f"{stage}_status"
        rows = self.conn.execute(
            f"SELECT video_id FROM processing_status WHERE {col} != 'done'"
        ).fetchall()
        return [r["video_id"] for r in rows]

    def set_stage_status(self, video_id: str, stage: str, status: str, error: str | None = None) -> None:
        col = f"{stage}_status"
        with self.tx() as conn:
            conn.execute(
                f"""INSERT INTO processing_status (video_id, {col}, last_error, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(video_id) DO UPDATE SET {col}=excluded.{col},
                        last_error=excluded.last_error, updated_at=excluded.updated_at""",
                (video_id, status, error, _now()),
            )

    # ------------------------------------------------------------ detection
    def save_music_detection(self, result: dict) -> None:
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO music_detection
                   (video_id, is_music, category, score, youtube_music_status, signals, evaluated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(video_id) DO UPDATE SET
                     is_music=excluded.is_music, category=excluded.category,
                     score=excluded.score, youtube_music_status=excluded.youtube_music_status,
                     signals=excluded.signals, evaluated_at=excluded.evaluated_at""",
                (
                    result["video_id"], int(result["is_music"]), result["category"],
                    result["score"], result["youtube_music_status"],
                    json.dumps(result.get("signals", {})), _now(),
                ),
            )

    def get_music_detection(self, video_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM music_detection WHERE video_id = ?", (video_id,)
        ).fetchone()

    # ------------------------------------------------------------ identification
    def _insert_identification_group(self, conn, video_id: str, candidates: list[dict],
                                      selected_index: int | None, confidence_score: float,
                                      confidence_level: str, match_method: str, notes: list[str],
                                      track_index: int | None = None,
                                      track_offset_seconds: float | None = None,
                                      track_end_offset_seconds: float | None = None) -> None:
        for i, cand in enumerate(candidates):
            conn.execute(
                """INSERT INTO identifications
                (video_id, track_index, track_offset_seconds, track_end_offset_seconds,
                 rank, is_selected, artist, track, album, release_group,
                 release_type, release_country, release_date, isrc,
                 recording_duration_seconds, duration_difference_seconds,
                 musicbrainz_recording_id, musicbrainz_release_id,
                 musicbrainz_release_group_id, musicbrainz_artist_id,
                 confidence_score, confidence_level, match_method, metadata_sources,
                 evidence, candidate_count, notes, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    video_id, track_index, track_offset_seconds, track_end_offset_seconds,
                    i, int(i == selected_index),
                    cand.get("artist"), cand.get("track"), cand.get("album"),
                    cand.get("release_group"), cand.get("release_type"),
                    cand.get("release_country"), cand.get("release_date"), cand.get("isrc"),
                    cand.get("recording_duration_seconds"), cand.get("duration_difference_seconds"),
                    cand.get("musicbrainz_recording_id"), cand.get("musicbrainz_release_id"),
                    cand.get("musicbrainz_release_group_id"), cand.get("musicbrainz_artist_id"),
                    confidence_score if i == selected_index else cand.get("score"),
                    confidence_level if i == selected_index else None,
                    match_method if i == selected_index else None,
                    json.dumps(cand.get("sources", [])),
                    json.dumps(cand.get("evidence", {})),
                    len(candidates),
                    json.dumps(notes),
                    _now(),
                ),
            )

    def save_identification(self, video_id: str, candidates: list[dict], selected_index: int | None,
                             confidence_score: float, confidence_level: str, match_method: str,
                             notes: list[str]) -> None:
        """Single-track identification: one candidate group for the whole
        video (track_index=NULL). This is the common case."""
        with self.tx() as conn:
            conn.execute("DELETE FROM identifications WHERE video_id = ?", (video_id,))
            self._insert_identification_group(
                conn, video_id, candidates, selected_index, confidence_score,
                confidence_level, match_method, notes,
            )

    def save_multi_track_identifications(self, video_id: str, groups: list[dict]) -> None:
        """Multi-track identification: `groups` is a list of dicts, one per
        parsed tracklist segment, each shaped like the keyword args of
        _insert_identification_group (candidates, selected_index,
        confidence_score, confidence_level, match_method, notes,
        track_index, track_offset_seconds, track_end_offset_seconds).
        Replaces ALL existing identification rows for this video in one
        transaction -- see music_identification/identifier.py identify_multi_track().
        """
        with self.tx() as conn:
            conn.execute("DELETE FROM identifications WHERE video_id = ?", (video_id,))
            for group in groups:
                self._insert_identification_group(conn, video_id, **group)

    def get_selected_identification(self, video_id: str) -> sqlite3.Row | None:
        """Single-track convenience accessor: the one selected row for a
        video with no track_index groups. For multi-track videos this may
        return an arbitrary one of several selected segments -- use
        get_selected_identifications (plural) instead."""
        return self.conn.execute(
            "SELECT * FROM identifications WHERE video_id = ? AND is_selected = 1", (video_id,)
        ).fetchone()

    def get_selected_identifications(self, video_id: str) -> list[sqlite3.Row]:
        """All selected rows for a video, ordered by track_index (NULL i.e.
        single-track sorts first). One row for an ordinary video; zero or
        more rows per identified segment for a multi-track video."""
        return self.conn.execute(
            """SELECT * FROM identifications WHERE video_id = ? AND is_selected = 1
               ORDER BY track_index IS NOT NULL, track_index""",
            (video_id,),
        ).fetchall()

    def get_track_groups(self, video_id: str) -> list[int | None]:
        """Distinct track_index values present for a video (for detecting
        whether it was identified as multi-track at all)."""
        rows = self.conn.execute(
            "SELECT DISTINCT track_index FROM identifications WHERE video_id = ? "
            "ORDER BY track_index IS NOT NULL, track_index",
            (video_id,),
        ).fetchall()
        return [r["track_index"] for r in rows]

    def get_candidates(self, video_id: str, track_index: int | None = "__all__") -> list[sqlite3.Row]:
        """All candidates for a video. Pass track_index to scope to one
        segment's group; leave the default to get every group's candidates
        (ordered by track_index then rank)."""
        if track_index == "__all__":
            return self.conn.execute(
                "SELECT * FROM identifications WHERE video_id = ? "
                "ORDER BY track_index IS NOT NULL, track_index, rank",
                (video_id,),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM identifications WHERE video_id = ? AND track_index IS ? ORDER BY rank",
            (video_id, track_index),
        ).fetchall()

    # -------------------------------------------------------------- corrections
    def save_correction(self, video_id: str, action: str, payload: dict) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO corrections (video_id, action, payload, created_at) VALUES (?,?,?,?)",
                (video_id, action, json.dumps(payload), _now()),
            )

    def latest_correction(self, video_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM corrections WHERE video_id = ? ORDER BY created_at DESC LIMIT 1",
            (video_id,),
        ).fetchone()

    def all_latest_corrections(self) -> dict[str, sqlite3.Row]:
        rows = self.conn.execute(
            """SELECT c.* FROM corrections c
               INNER JOIN (
                 SELECT video_id, MAX(created_at) as max_created FROM corrections GROUP BY video_id
               ) latest ON c.video_id = latest.video_id AND c.created_at = latest.max_created"""
        ).fetchall()
        return {r["video_id"]: r for r in rows}

    # -------------------------------------------------------------------- cache
    def cache_get(self, namespace: str, key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT response_json, fetched_at FROM cache_entries WHERE cache_key = ?",
            (f"{namespace}:{key}",),
        ).fetchone()
        if not row:
            return None
        return {"response": json.loads(row["response_json"]), "fetched_at": row["fetched_at"]}

    def cache_set(self, namespace: str, key: str, response: Any) -> None:
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO cache_entries (cache_key, namespace, response_json, fetched_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(cache_key) DO UPDATE SET
                     response_json=excluded.response_json, fetched_at=excluded.fetched_at""",
                (f"{namespace}:{key}", namespace, json.dumps(response), _now()),
            )

    # -------------------------------------------------------------------- stats
    def counts(self) -> dict:
        def scalar(q: str) -> int:
            return self.conn.execute(q).fetchone()[0]

        return {
            "history_entries": scalar("SELECT COUNT(*) FROM history_entries"),
            "distinct_videos": scalar("SELECT COUNT(*) FROM videos"),
            "videos_metadata_fetched": scalar(
                "SELECT COUNT(*) FROM processing_status WHERE metadata_status='done'"),
            "videos_detected_music": scalar(
                "SELECT COUNT(*) FROM music_detection WHERE is_music=1"),
            "videos_identified_high": scalar(
                "SELECT COUNT(*) FROM identifications WHERE is_selected=1 AND confidence_level='high'"),
            "videos_identified_medium": scalar(
                "SELECT COUNT(*) FROM identifications WHERE is_selected=1 AND confidence_level='medium'"),
            "videos_identified_low": scalar(
                "SELECT COUNT(*) FROM identifications WHERE is_selected=1 AND confidence_level='low'"),
            "videos_unidentified": scalar(
                "SELECT COUNT(*) FROM music_detection WHERE is_music=1 AND video_id NOT IN "
                "(SELECT video_id FROM identifications WHERE is_selected=1 AND confidence_level IN ('high','medium','low'))"
            ),
        }
