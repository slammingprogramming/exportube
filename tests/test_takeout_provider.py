from pathlib import Path

import pytest

from exportube.history_import.takeout_provider import TakeoutProvider

FIXTURES = Path(__file__).parent / "fixtures"


def test_json_takeout_parses_all_entries(tmp_path):
    provider = TakeoutProvider(FIXTURES / "watch-history.json", tmp_path)
    events = list(provider.fetch())
    assert len(events) == 4  # every entry retained, including the removed one


def test_json_takeout_repeat_watch_preserved_as_two_events(tmp_path):
    provider = TakeoutProvider(FIXTURES / "watch-history.json", tmp_path)
    events = list(provider.fetch())
    same_video = [e for e in events if e.video_id == "FGBhQbmPwH8"]
    assert len(same_video) == 2
    assert same_video[0].watched_at != same_video[1].watched_at


def test_removed_video_retained_with_null_video_id(tmp_path):
    provider = TakeoutProvider(FIXTURES / "watch-history.json", tmp_path)
    events = list(provider.fetch())
    removed = [e for e in events if e.video_url_raw is None]
    assert len(removed) == 1
    assert removed[0].video_id is None


def test_podcast_episode_number_preserved_in_title(tmp_path):
    provider = TakeoutProvider(FIXTURES / "watch-history.json", tmp_path)
    events = list(provider.fetch())
    podcast = [e for e in events if e.video_id == "abcdefghijk"]
    assert len(podcast) == 1
    assert "#1234" in podcast[0].raw_title


def test_raw_source_file_preserved_for_reproducibility(tmp_path):
    provider = TakeoutProvider(FIXTURES / "watch-history.json", tmp_path)
    list(provider.fetch())
    preserved = list((tmp_path / provider.batch_id).glob("*.json"))
    assert len(preserved) == 1


def test_html_takeout_parses_entries(tmp_path):
    provider = TakeoutProvider(FIXTURES / "watch-history.html", tmp_path)
    events = list(provider.fetch())
    assert len(events) == 2
    assert events[0].video_id == "FGBhQbmPwH8"
    assert events[0].watched_at is not None


def test_html_takeout_resolves_youtu_be_url(tmp_path):
    provider = TakeoutProvider(FIXTURES / "watch-history.html", tmp_path)
    events = list(provider.fetch())
    assert events[1].video_id == "abcdefghijk"


def test_locates_watch_history_in_nested_localized_directory(tmp_path):
    nested = tmp_path / "Takeout" / "YouTube et YouTube Music" / "history"
    nested.mkdir(parents=True)
    (nested / "watch-history.json").write_text((FIXTURES / "watch-history.json").read_text(encoding="utf-8"),
                                                 encoding="utf-8")
    provider = TakeoutProvider(tmp_path / "Takeout", tmp_path / "_raw")
    events = list(provider.fetch())
    assert len(events) == 4


def test_missing_watch_history_raises_clear_error(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    provider = TakeoutProvider(empty_dir, tmp_path / "_raw")
    with pytest.raises(FileNotFoundError):
        list(provider.fetch())


def test_zip_archive_supported(tmp_path):
    import zipfile

    zip_path = tmp_path / "takeout.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(FIXTURES / "watch-history.json", "Takeout/YouTube and YouTube Music/history/watch-history.json")

    provider = TakeoutProvider(zip_path, tmp_path / "_raw")
    events = list(provider.fetch())
    assert len(events) == 4
