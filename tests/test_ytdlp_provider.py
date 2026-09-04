"""Unavailable-video classification logic, tested without hitting the
network by monkeypatching yt_dlp.YoutubeDL.extract_info to raise the
exact error messages yt-dlp is known to produce."""
import pytest

from exportube.youtube_metadata.ytdlp_provider import YtDlpProvider


class _FakeYDL:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        import yt_dlp
        raise yt_dlp.utils.DownloadError(self._message)


def _patch_ydl(monkeypatch, message):
    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", lambda opts: _FakeYDL(message))


def test_private_video_classified(monkeypatch):
    _patch_ydl(monkeypatch, "ERROR: Private video. Sign in if you've been granted access.")
    result = YtDlpProvider().fetch_one("abc12345678")
    assert result["availability"] == "private"


def test_deleted_video_classified(monkeypatch):
    _patch_ydl(monkeypatch, "ERROR: This video is no longer available because the uploader has closed their YouTube account.")
    result = YtDlpProvider().fetch_one("abc12345678")
    assert result["availability"] == "deleted"


def test_generic_unavailable_classified(monkeypatch):
    _patch_ydl(monkeypatch, "ERROR: Video unavailable")
    result = YtDlpProvider().fetch_one("abc12345678")
    assert result["availability"] == "unavailable"


def test_age_restricted_classified_as_unavailable(monkeypatch):
    # Regression test: confirmed as a real, recurring case in a 50-video
    # sample from a real watch history -- age-gated content requires a
    # signed-in, age-verified session this pipeline doesn't have.
    _patch_ydl(
        monkeypatch,
        "ERROR: [youtube] abc12345678: Sign in to confirm your age. Use --cookies-from-browser "
        "or --cookies for the authentication.",
    )
    result = YtDlpProvider().fetch_one("abc12345678")
    assert result["availability"] == "unavailable"


def test_unrecognized_error_message_does_not_crash(monkeypatch):
    _patch_ydl(monkeypatch, "ERROR: some completely novel failure mode")
    result = YtDlpProvider(max_retries=1).fetch_one("abc12345678")
    assert result["availability"] == "unknown"
    assert result["video_id"] == "abc12345678"
