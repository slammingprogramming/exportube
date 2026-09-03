from tune_history.candidate_generation.candidates import build_seed_candidates
from tune_history.candidate_generation.title_parser import parse_title


def test_youtube_music_fields_produce_seed_candidate():
    video = {"yt_track": "One More Time", "yt_artist": "Daft Punk", "yt_album": "Discovery", "title": "x"}
    title_parse = parse_title("Daft Punk - One More Time (Official Video)")
    candidates = build_seed_candidates(video, title_parse)
    assert any(c.artist == "Daft Punk" and c.track == "One More Time" for c in candidates)


def test_topic_channel_produces_candidate_with_channel_artist():
    video = {"uploader": "Daft Punk - Topic", "title": "One More Time"}
    title_parse = parse_title("One More Time")
    candidates = build_seed_candidates(video, title_parse)
    assert any(c.artist == "Daft Punk" for c in candidates)
    assert any("channel_identity" in c.sources for c in candidates)


def test_vevo_channel_strips_vevo_suffix():
    video = {"uploader": "LadyGagaVEVO", "title": "Bad Romance"}
    title_parse = parse_title("Bad Romance")
    candidates = build_seed_candidates(video, title_parse)
    assert any(c.artist == "LadyGaga" for c in candidates)


def test_no_evidence_produces_no_candidates():
    video = {"uploader": "Random Channel", "title": ""}
    title_parse = parse_title("")
    candidates = build_seed_candidates(video, title_parse)
    assert candidates == []


def test_duplicate_candidates_are_merged():
    video = {"yt_track": "Track", "yt_artist": "Artist", "title": "Artist - Track"}
    title_parse = parse_title("Artist - Track")
    candidates = build_seed_candidates(video, title_parse)
    keys = [(c.artist, c.track) for c in candidates]
    assert len(keys) == len(set(keys))


def test_swapped_order_candidate_proposed_for_track_artist_titles():
    # Regression test: confirmed against a real video ("New Divide
    # (Official Music Video) [4K Upgrade] - Linkin Park") where the
    # artist trails the track -- the primary "Artist - Track" dash-split
    # gets this backwards, so a swapped-order candidate must also be
    # proposed for MusicBrainz to have a chance at the correct one.
    video = {"uploader": "Linkin Park", "title": "New Divide [4K Upgrade] - Linkin Park"}
    title_parse = parse_title("New Divide [4K Upgrade] - Linkin Park")
    candidates = build_seed_candidates(video, title_parse)
    assert any(c.artist == "Linkin Park" and c.track == "New Divide [4K Upgrade]" for c in candidates)
    # The (wrong) primary reading is still proposed too -- scoring sorts it out.
    assert any(c.artist == "New Divide [4K Upgrade]" and c.track == "Linkin Park" for c in candidates)


def test_featuring_clause_stripped_from_track_candidate():
    # Regression test: confirmed against a real video ("Chamillionaire -
    # Ridin' (Official Music Video) ft. Krayzie Bone") where "ft. Krayzie
    # Bone" was baked into the track guess, causing the MusicBrainz search
    # (for the literal recording title, which doesn't include the
    # featuring clause) to come back empty.
    video = {"uploader": "ChamillionaireVEVO", "title": "Chamillionaire - Ridin' ft. Krayzie Bone"}
    title_parse = parse_title("Chamillionaire - Ridin' ft. Krayzie Bone")
    candidates = build_seed_candidates(video, title_parse)
    assert any(c.artist == "Chamillionaire" and c.track == "Ridin'" for c in candidates)


def test_unrecognized_bracket_text_stripped_for_a_search_candidate():
    # Regression test: confirmed against a real video ("New Divide
    # (Official Music Video) [4K Upgrade] - Linkin Park") where
    # "[4K Upgrade]" survives into the clean/display title (correctly --
    # it's not known-decorative boilerplate) but sinks the MusicBrainz
    # search built from it (MusicBrainz's actual recording is just "New
    # Divide"). A fully-bracket-stripped variant must be proposed too,
    # in both orientations, without removing the original from the pool
    # (that one still matters for display / as a fallback).
    video = {"uploader": "Linkin Park", "title": "New Divide [4K Upgrade] - Linkin Park"}
    title_parse = parse_title("New Divide [4K Upgrade] - Linkin Park")
    candidates = build_seed_candidates(video, title_parse)
    assert any(c.artist == "Linkin Park" and c.track == "New Divide" for c in candidates)
    # The bracket-preserving readings are still proposed too.
    assert any(c.artist == "Linkin Park" and c.track == "New Divide [4K Upgrade]" for c in candidates)


def test_bracket_stripping_is_a_noop_when_nothing_to_strip():
    video = {"uploader": "Artist - Topic", "title": "Artist - Track"}
    title_parse = parse_title("Artist - Track")
    candidates = build_seed_candidates(video, title_parse)
    # No extra bracket-stripped duplicate candidate when there was nothing
    # to strip in the first place.
    keys = [(c.artist, c.track) for c in candidates]
    assert len(keys) == len(set(keys))
