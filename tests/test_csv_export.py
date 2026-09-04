from exportube.export.csv_export import gather_export_rows
from exportube.history_import.normalize import compute_dedup_key


def _insert_unresolved(db, video_url_raw, raw_title, source="takeout_html"):
    dedup_key = compute_dedup_key(source, None, video_url_raw, None, raw_title, raw_fallback=video_url_raw)
    db.insert_history_entry({
        "video_id": None, "video_url_raw": video_url_raw, "raw_title": raw_title,
        "raw_channel_name": None, "watched_at": None, "source": source,
        "source_playlist_name": None, "source_playlist_id": None,
        "import_batch_id": "test", "raw_json": None,
    }, dedup_key)


def test_community_post_gets_a_distinct_explanatory_note(db):
    _insert_unresolved(db, "https://www.youtube.com/post/Ugkxg4rdjUr8xVPDj1BcZrKdfe6dhFdLWqSJ", "a post")
    rows = gather_export_rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["video_category"] == "community_post"
    assert "Community post" in row["identification_notes"]
    assert "video_id could not be resolved" not in row["identification_notes"]


def test_genuinely_unparseable_url_keeps_generic_note(db):
    _insert_unresolved(db, None, "Watched a video that has been removed")
    rows = gather_export_rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["video_category"] == "unknown"
    assert "video_id could not be resolved" in row["identification_notes"]


def test_community_post_row_has_blank_identification_fields(db):
    _insert_unresolved(db, "https://www.youtube.com/post/UgkxPDxIIhn4cyNE_daNpU3bIoKGXBDg22U3", "Campaign post")
    rows = gather_export_rows(db)
    row = rows[0]
    assert row["artist"] == "" and row["track"] == ""
    assert row["music_identification_confidence"] == "unidentified"
