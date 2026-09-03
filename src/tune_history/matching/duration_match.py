"""Tolerant, weighted duration matching (spec section 8).

Duration is never a hard pass/fail gate. A YouTube video's duration can
differ substantially from the canonical recording (pre-roll, spoken
intros/outros, applause, alternate edits, ...), so this produces a
continuous 0.0-1.0 score using several tolerance "regimes" rather than one
fixed cutoff, and takes whichever of (absolute-seconds difference,
percentage difference) is more forgiving -- a 20s difference is trivial
for a 6-minute live jam but suspicious for a 20-second short.

An explicit ratio gate still forces the score to 0 for pathological cases
(240s recording vs a 7200s video), matching the spec's own example.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DurationToleranceConfig:
    strong_tolerance_seconds: float = 10
    strong_tolerance_pct: float = 0.03
    moderate_tolerance_seconds: float = 30
    moderate_tolerance_pct: float = 0.12
    loose_tolerance_seconds: float = 90
    loose_tolerance_pct: float = 0.35
    max_ratio_before_zero: float = 4.0

    @classmethod
    def from_config(cls, cfg) -> "DurationToleranceConfig":
        return cls(
            strong_tolerance_seconds=cfg.get("matching.duration.strong_tolerance_seconds", 10),
            strong_tolerance_pct=cfg.get("matching.duration.strong_tolerance_pct", 0.03),
            moderate_tolerance_seconds=cfg.get("matching.duration.moderate_tolerance_seconds", 30),
            moderate_tolerance_pct=cfg.get("matching.duration.moderate_tolerance_pct", 0.12),
            loose_tolerance_seconds=cfg.get("matching.duration.loose_tolerance_seconds", 90),
            loose_tolerance_pct=cfg.get("matching.duration.loose_tolerance_pct", 0.35),
            max_ratio_before_zero=cfg.get("matching.duration.max_ratio_before_zero", 4.0),
        )


def _piecewise(x: float, points: list[tuple[float, float]]) -> float:
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y1
            frac = (x - x0) / (x1 - x0)
            return y0 + frac * (y1 - y0)
    return points[-1][1]


def score_duration(video_seconds: float | None, recording_seconds: float | None,
                    cfg: DurationToleranceConfig | None = None) -> tuple[float, float | None]:
    """Returns (score in [0,1], absolute_difference_seconds or None)."""
    cfg = cfg or DurationToleranceConfig()

    if video_seconds is None or recording_seconds is None or recording_seconds <= 0:
        return 0.0, None

    diff = abs(video_seconds - recording_seconds)
    pct = diff / recording_seconds
    ratio = max(video_seconds, recording_seconds) / max(min(video_seconds, recording_seconds), 0.001)

    if ratio >= cfg.max_ratio_before_zero:
        return 0.0, diff

    seconds_points = [
        (0, 1.0),
        (cfg.strong_tolerance_seconds, 1.0),
        (cfg.moderate_tolerance_seconds, 0.8),
        (cfg.loose_tolerance_seconds, 0.4),
        (cfg.loose_tolerance_seconds * 3, 0.0),
    ]
    pct_points = [
        (0, 1.0),
        (cfg.strong_tolerance_pct, 1.0),
        (cfg.moderate_tolerance_pct, 0.8),
        (cfg.loose_tolerance_pct, 0.4),
        (cfg.loose_tolerance_pct * 3, 0.0),
    ]

    score = max(_piecewise(diff, seconds_points), _piecewise(pct, pct_points))
    return round(score, 4), diff
