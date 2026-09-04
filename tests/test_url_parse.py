from exportube.history_import.url_parse import parse_video_url


def test_standard_watch_url():
    r = parse_video_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert r.video_id == "dQw4w9WgXcQ"
    assert r.url_type == "watch"


def test_watch_url_with_playlist_and_index():
    r = parse_video_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123&index=5&t=42s")
    assert r.video_id == "dQw4w9WgXcQ"
    assert r.playlist_id == "PL123"


def test_youtu_be_short_url():
    r = parse_video_url("https://youtu.be/dQw4w9WgXcQ")
    assert r.video_id == "dQw4w9WgXcQ"
    assert r.url_type == "youtu_be"


def test_youtu_be_with_query_params():
    r = parse_video_url("https://youtu.be/dQw4w9WgXcQ?si=abcdefg&t=10")
    assert r.video_id == "dQw4w9WgXcQ"


def test_shorts_url():
    r = parse_video_url("https://www.youtube.com/shorts/dQw4w9WgXcQ")
    assert r.video_id == "dQw4w9WgXcQ"
    assert r.url_type == "shorts"


def test_embed_url():
    r = parse_video_url("https://www.youtube.com/embed/dQw4w9WgXcQ")
    assert r.video_id == "dQw4w9WgXcQ"
    assert r.url_type == "embed"


def test_mobile_watch_url():
    r = parse_video_url("https://m.youtube.com/watch?v=dQw4w9WgXcQ")
    assert r.video_id == "dQw4w9WgXcQ"


def test_music_youtube_url():
    r = parse_video_url("https://music.youtube.com/watch?v=dQw4w9WgXcQ&feature=share")
    assert r.video_id == "dQw4w9WgXcQ"


def test_bare_video_id():
    r = parse_video_url("dQw4w9WgXcQ")
    assert r.video_id == "dQw4w9WgXcQ"
    assert r.url_type == "bare_id"


def test_missing_url():
    r = parse_video_url(None)
    assert r.video_id is None
    assert r.url_type == "unresolvable"


def test_empty_string():
    r = parse_video_url("")
    assert r.video_id is None


def test_non_youtube_url():
    r = parse_video_url("https://example.com/watch?v=dQw4w9WgXcQ")
    assert r.video_id is None
    assert r.url_type == "unresolvable"


def test_malformed_url():
    r = parse_video_url("not a url at all !!!")
    assert r.video_id is None


def test_watch_url_missing_v_param():
    r = parse_video_url("https://www.youtube.com/watch?list=PL123")
    assert r.video_id is None
    assert r.playlist_id == "PL123"


def test_live_url():
    r = parse_video_url("https://www.youtube.com/live/dQw4w9WgXcQ")
    assert r.video_id == "dQw4w9WgXcQ"
    assert r.url_type == "live"


def test_community_post_url_recognized_distinctly():
    # Confirmed against a real Google Takeout export: watch history can
    # include YouTube Community post views mixed in with video watches.
    r = parse_video_url("https://www.youtube.com/post/Ugkxg4rdjUr8xVPDj1BcZrKdfe6dhFdLWqSJ")
    assert r.video_id is None
    assert r.url_type == "community_post"
