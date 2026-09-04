from exportube.candidate_generation.tracklist_parser import parse_tracklist


SAMPLE_DESCRIPTION = """Tracklist:
0:00 Artist One - Track One
3:45 Artist Two - Track Two (Live)
08:12 Track Three
1:02:30 Artist Four - Track Four [Remix]

Thanks for watching!
"""


def test_parses_all_timestamped_lines():
    entries = parse_tracklist(SAMPLE_DESCRIPTION)
    assert len(entries) == 4


def test_offsets_converted_correctly():
    entries = parse_tracklist(SAMPLE_DESCRIPTION)
    assert entries[0].offset_seconds == 0
    assert entries[1].offset_seconds == 3 * 60 + 45
    assert entries[2].offset_seconds == 8 * 60 + 12
    assert entries[3].offset_seconds == 3600 + 2 * 60 + 30


def test_entries_sorted_chronologically_even_if_listed_out_of_order():
    desc = "3:45 Second Track\n0:00 First Track\n"
    entries = parse_tracklist(desc)
    assert entries[0].offset_seconds == 0
    assert entries[1].offset_seconds == 225


def test_artist_track_split_applied_per_entry():
    entries = parse_tracklist(SAMPLE_DESCRIPTION)
    assert entries[0].title_parse.artist_guess == "Artist One"
    assert entries[0].title_parse.track_guess == "Track One"


def test_version_marker_preserved_in_entry():
    entries = parse_tracklist(SAMPLE_DESCRIPTION)
    assert "live" in entries[1].title_parse.version_markers


def test_end_offset_is_next_entrys_start():
    entries = parse_tracklist(SAMPLE_DESCRIPTION)
    assert entries[0].end_offset_seconds == entries[1].offset_seconds


def test_last_entry_end_offset_uses_video_duration():
    entries = parse_tracklist(SAMPLE_DESCRIPTION, video_duration_seconds=4000)
    assert entries[-1].end_offset_seconds == 4000


def test_last_entry_end_offset_none_without_video_duration():
    entries = parse_tracklist(SAMPLE_DESCRIPTION)
    assert entries[-1].end_offset_seconds is None


def test_no_timestamps_returns_empty():
    assert parse_tracklist("Just a normal description with no tracklist.") == []


def test_empty_description_returns_empty():
    assert parse_tracklist(None) == []
    assert parse_tracklist("") == []


def test_duplicate_timestamp_lines_deduplicated():
    desc = "0:00 Track A\n0:00 Track A\n1:00 Track B\n2:00 Track C\n"
    entries = parse_tracklist(desc)
    assert len(entries) == 3


def test_numbered_list_prefix_handled():
    desc = "1. 0:00 Artist - Track One\n2. 3:00 Artist - Track Two\n3. 6:00 Artist - Track Three\n"
    entries = parse_tracklist(desc)
    assert len(entries) == 3
    assert entries[0].title_parse.track_guess == "Track One"


def test_bracketed_timestamp_handled():
    desc = "[00:00] Track One\n[03:00] Track Two\n[06:00] Track Three\n"
    entries = parse_tracklist(desc)
    assert len(entries) == 3
    assert entries[0].offset_seconds == 0
