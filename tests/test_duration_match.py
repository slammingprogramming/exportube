from tune_history.matching.duration_match import DurationToleranceConfig, score_duration


def test_near_exact_duration_scores_high():
    score, diff = score_duration(240, 238)
    assert score >= 0.95
    assert diff == 2


def test_moderate_difference_still_strong_match():
    # Spec's own example: 240s recording vs a 264s video (intro/outro) should
    # "still be considered a potentially strong match".
    score, diff = score_duration(264, 240)
    assert score >= 0.6
    assert diff == 24


def test_wildly_different_duration_scores_near_zero():
    # Spec's own example: 240s recording vs 7200s video should NOT match.
    score, diff = score_duration(7200, 240)
    assert score == 0.0


def test_missing_recording_duration_scores_zero():
    score, diff = score_duration(240, None)
    assert score == 0.0
    assert diff is None


def test_missing_video_duration_scores_zero():
    score, diff = score_duration(None, 240)
    assert score == 0.0


def test_short_track_small_absolute_diff_still_scores_well():
    # A 30s track with a 5s difference is a large percentage but a small
    # absolute difference -- should still score reasonably via the
    # absolute-seconds regime, not be crushed by percentage alone.
    score, diff = score_duration(35, 30)
    assert score > 0.3


def test_extremely_long_mix_vs_short_track_scores_zero():
    score, diff = score_duration(3600, 200)
    assert score == 0.0


def test_custom_tolerance_config():
    cfg = DurationToleranceConfig(strong_tolerance_seconds=5, strong_tolerance_pct=0.01,
                                   moderate_tolerance_seconds=15, moderate_tolerance_pct=0.05,
                                   loose_tolerance_seconds=30, loose_tolerance_pct=0.15,
                                   max_ratio_before_zero=3.0)
    score, diff = score_duration(220, 200, cfg)
    assert 0.0 < score < 1.0
