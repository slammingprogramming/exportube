from exportube.music_detection.detector import MusicDetector
from exportube.storage.models import MusicCategory, YoutubeMusicStatus


def make_video(**overrides):
    base = {
        "video_id": "abc12345678",
        "title": "Some Video",
        "uploader": "Some Channel",
        "description": "",
        "duration_seconds": 200,
    }
    base.update(overrides)
    return base


def test_youtube_music_track_field_detected_as_music():
    d = MusicDetector()
    video = make_video(title="Random Title", yt_track="One More Time", yt_artist="Daft Punk")
    r = d.detect(video)
    assert r.is_music
    assert r.youtube_music_status == YoutubeMusicStatus.YOUTUBE_MUSIC


def test_topic_channel_detected_as_music():
    d = MusicDetector()
    video = make_video(title="One More Time", uploader="Daft Punk - Topic")
    r = d.detect(video)
    assert r.is_music
    assert r.category == MusicCategory.SINGLE_TRACK


def test_vevo_channel_detected_as_music():
    d = MusicDetector()
    video = make_video(title="Bad Romance", uploader="LadyGagaVEVO")
    r = d.detect(video)
    assert r.is_music


def test_provided_to_youtube_description_strong_signal():
    d = MusicDetector()
    video = make_video(
        title="Track Name", uploader="Random Uploads 22",
        description="Provided to YouTube by Sony Music Entertainment\n\nTrack Name (c) 2020",
    )
    r = d.detect(video)
    assert r.is_music
    assert r.youtube_music_status == YoutubeMusicStatus.YOUTUBE_MUSIC


def test_review_video_not_treated_as_music():
    # Spec example: "Why Taylor Swift's New Album Is Bad" must not become
    # a Taylor Swift recording.
    d = MusicDetector()
    video = make_video(title="Why Taylor Swift's New Album Is Bad", uploader="Music Critic Channel",
                        duration_seconds=900)
    r = d.detect(video)
    assert not r.is_music
    assert r.category == MusicCategory.NON_MUSIC


def test_top_10_songs_video_not_treated_as_music():
    d = MusicDetector()
    video = make_video(title="Top 10 Songs Used in Movie Trailers", uploader="Movie Channel")
    r = d.detect(video)
    assert not r.is_music


def test_podcast_episode_number_not_treated_as_music():
    d = MusicDetector()
    video = make_video(title="Joe Rogan Experience #1234", uploader="PowerfulJRE",
                        description="talking about music and bands the whole episode",
                        duration_seconds=9000)
    r = d.detect(video)
    assert not r.is_music


def test_dj_mix_title_categorized_as_dj_mix():
    d = MusicDetector()
    video = make_video(title="2 Hour Drum & Bass Mix", uploader="DNB Channel - Topic", duration_seconds=7200)
    r = d.detect(video)
    assert r.is_music
    assert r.category == MusicCategory.DJ_MIX


def test_compilation_title_categorized_as_compilation():
    d = MusicDetector()
    video = make_video(title="80s Rock Mix Compilation", uploader="Rock Channel", yt_track="foo")
    r = d.detect(video)
    assert r.category == MusicCategory.COMPILATION


def test_live_concert_title_categorized_as_live():
    d = MusicDetector()
    video = make_video(title="Nirvana Live at Reading 1992 Full Concert", uploader="Nirvana - Topic",
                        duration_seconds=5400)
    r = d.detect(video)
    assert r.category == MusicCategory.LIVE_OR_CONCERT


def test_album_stream_title_categorized_as_album_stream():
    d = MusicDetector()
    video = make_video(title="Artist - Full Album Stream", uploader="Artist - Topic", duration_seconds=2800)
    r = d.detect(video)
    assert r.category == MusicCategory.ALBUM_STREAM


def test_gaming_video_with_background_music_not_automatically_music():
    d = MusicDetector()
    video = make_video(title="Insane Clutch Play - Ranked Gameplay", uploader="GamerXYZ",
                        description="using copyrighted background music from NCS")
    r = d.detect(video)
    assert not r.is_music


def test_missing_metadata_does_not_force_regular_upload_status():
    d = MusicDetector()
    video = make_video(title="Untitled", uploader="SomeChannel")
    r = d.detect(video)
    assert r.youtube_music_status == YoutubeMusicStatus.UNKNOWN


def test_isrc_in_description_detected():
    d = MusicDetector()
    video = make_video(title="Artist - Track", description="ISRC: USRC17607839")
    r = d.detect(video)
    assert r.signals["isrc"] == "USRC17607839"


def test_official_music_video_with_extras_long_duration():
    d = MusicDetector()
    video = make_video(title="Artist - Track (Official Music Video)", uploader="Artist - Topic",
                        duration_seconds=1300)
    r = d.detect(video)
    assert r.is_music
    assert r.category == MusicCategory.MUSIC_VIDEO_WITH_EXTRAS


def test_official_video_with_quality_descriptor_detected():
    # Regression test: confirmed against a real video ("Skyline Echo -
    # Fading Light (Official HD Video)") that a quality descriptor (HD/4K/HQ)
    # between "Official" and "Video" was not recognized, dropping the
    # video's score below the is_music threshold entirely even though the
    # channel (post-rename, no longer literally "...VEVO") gave no other
    # signal. See docs/ARCHITECTURE.md "Verified against a real Google Takeout export".
    d = MusicDetector()
    video = make_video(title="Skyline Echo - Fading Light (Official HD Video)", uploader="Skyline Echo")
    r = d.detect(video)
    assert r.signals["official_video_title_marker"] is True
    assert r.is_music


def test_official_4k_video_detected():
    d = MusicDetector()
    video = make_video(title="Artist - Track (Official 4K Video)", uploader="Artist")
    r = d.detect(video)
    assert r.signals["official_video_title_marker"] is True
