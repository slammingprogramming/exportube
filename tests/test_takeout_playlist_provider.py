from pathlib import Path

from tune_history.history_import.takeout_playlist_provider import TakeoutPlaylistProvider

FIXTURES = Path(__file__).parent / "fixtures" / "playlists"


def test_parses_all_playlist_csvs():
    provider = TakeoutPlaylistProvider(FIXTURES)
    events = list(provider.fetch())
    # 2 from "Liked videos.csv" + 1 from "My Custom Mix.csv" + 2 from
    # "Watch later-videos.csv" (real Takeout filename pattern). playlists.csv
    # itself contributes 0 membership events (it's the ID/title manifest).
    assert len(events) == 5


def test_non_playlist_csv_ignored():
    provider = TakeoutPlaylistProvider(FIXTURES)
    events = list(provider.fetch())
    assert all(e.video_id != "UC123" for e in events)


def test_playlist_name_derived_from_filename():
    provider = TakeoutPlaylistProvider(FIXTURES)
    events = list(provider.fetch())
    liked = [e for e in events if e.source_playlist_name == "Liked videos"]
    assert len(liked) == 2
    custom = [e for e in events if e.source_playlist_name == "My Custom Mix"]
    assert len(custom) == 1


def test_real_takeout_videos_suffix_stripped_from_playlist_name():
    # Real Takeout names the per-playlist file "<Title>-videos.csv" (e.g.
    # "Watch later-videos.csv"), verified against an actual export -- the
    # "-videos" suffix must not leak into source_playlist_name.
    provider = TakeoutPlaylistProvider(FIXTURES)
    events = list(provider.fetch())
    watch_later = [e for e in events if e.source_playlist_name == "Watch later"]
    assert len(watch_later) == 2
    assert not any(e.source_playlist_name == "Watch later-videos" for e in events)


def test_playlist_id_populated_from_manifest():
    # Real Takeout's playlists.csv manifest carries the actual Playlist ID;
    # the per-playlist membership file never repeats it, so it has to be
    # cross-referenced by playlist title.
    provider = TakeoutPlaylistProvider(FIXTURES)
    events = list(provider.fetch())
    watch_later = [e for e in events if e.source_playlist_name == "Watch later"]
    assert all(e.source_playlist_id == "PL_CvCJf-yhMyAo_YzrF-l65Mwq81PN79j" for e in watch_later)


def test_playlist_id_blank_when_no_manifest_entry():
    provider = TakeoutPlaylistProvider(FIXTURES)
    events = list(provider.fetch())
    custom = [e for e in events if e.source_playlist_name == "My Custom Mix"]
    assert all(e.source_playlist_id is None for e in custom)


def test_watched_at_is_never_set_from_playlist_add_date():
    provider = TakeoutPlaylistProvider(FIXTURES)
    events = list(provider.fetch())
    assert all(e.watched_at is None for e in events)


def test_source_is_takeout_playlist():
    provider = TakeoutPlaylistProvider(FIXTURES)
    events = list(provider.fetch())
    assert all(e.source == "takeout_playlist" for e in events)


def test_zip_archive_supported(tmp_path):
    import zipfile

    zip_path = tmp_path / "takeout.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(FIXTURES / "Watch later-videos.csv",
                  "Takeout/YouTube and YouTube Music/playlists/Watch later-videos.csv")
        zf.write(FIXTURES / "playlists.csv",
                  "Takeout/YouTube and YouTube Music/playlists/playlists.csv")
        zf.write(FIXTURES / "subscriptions.csv",
                  "Takeout/YouTube and YouTube Music/subscriptions.csv")

    provider = TakeoutPlaylistProvider(zip_path)
    events = list(provider.fetch())
    assert len(events) == 2
    assert all(e.source_playlist_name == "Watch later" for e in events)
    assert all(e.source_playlist_id == "PL_CvCJf-yhMyAo_YzrF-l65Mwq81PN79j" for e in events)


def test_no_playlists_directory_yields_nothing(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    provider = TakeoutPlaylistProvider(empty_dir)
    events = list(provider.fetch())
    assert events == []
