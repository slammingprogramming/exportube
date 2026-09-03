from tune_history.candidate_generation.title_parser import parse_title


def test_strips_official_video_tag():
    r = parse_title("Daft Punk - One More Time (Official Music Video)")
    assert r.artist_guess == "Daft Punk"
    assert r.track_guess == "One More Time"
    assert "official music video" in [t.lower() for t in r.removed_tags]


def test_strips_lyrics_tag():
    r = parse_title("The Weeknd - Blinding Lights [Official Lyric Video]")
    assert r.track_guess == "Blinding Lights"


def test_preserves_live_marker():
    r = parse_title("Nirvana - Smells Like Teen Spirit (Live)")
    assert "live" in r.version_markers
    assert "Live" in r.clean_title


def test_preserves_remix_marker():
    r = parse_title("Artist - Track (Extended Mix)")
    assert "extended_mix" in r.version_markers
    assert "Extended Mix" in r.clean_title


def test_preserves_remaster_marker():
    r = parse_title("Fleetwood Mac - Dreams (2004 Remaster)")
    assert "remaster" in r.version_markers
    assert "Remaster" in r.clean_title


def test_no_dash_title_has_no_artist_guess():
    r = parse_title("Bohemian Rhapsody")
    assert r.artist_guess is None
    assert r.track_guess == "Bohemian Rhapsody"


def test_raw_title_always_preserved():
    raw = "Artist - Track [HD] (Official Video)"
    r = parse_title(raw)
    assert r.raw_title == raw


def test_trailing_decorative_dash_suffix_stripped():
    r = parse_title("Artist - Track - Official Video")
    assert r.artist_guess == "Artist"
    assert r.track_guess == "Track"


def test_unknown_bracket_content_preserved():
    r = parse_title("Artist - Track (Bonus Interlude)")
    assert "Bonus Interlude" in r.clean_title


def test_empty_title():
    r = parse_title("")
    assert r.clean_title == ""
    assert r.artist_guess is None


def test_none_title():
    r = parse_title(None)
    assert r.clean_title == ""


def test_multi_dash_title_joins_remainder_as_track():
    r = parse_title("Artist - Track - Interlude")
    assert r.artist_guess == "Artist"
    assert r.track_guess == "Track - Interlude"


def test_acoustic_marker_detected():
    r = parse_title("Artist - Track (Acoustic Version)")
    assert "acoustic" in r.version_markers


def test_official_hd_video_tag_stripped():
    # Regression test: confirmed against a real video ("CAKE - The
    # Distance (Official HD Video)") that "(Official HD Video)" wasn't
    # being recognized as decorative and leaked into the clean title.
    r = parse_title("CAKE - The Distance (Official HD Video)")
    assert r.artist_guess == "CAKE"
    assert r.track_guess == "The Distance"
    assert "HD" not in r.clean_title
