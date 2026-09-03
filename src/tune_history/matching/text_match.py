"""Fuzzy text matching for artist/track names using rapidfuzz.

Names arriving from different sources (YouTube uploader strings,
MusicBrainz artist credits, user-facing title text) are rarely
byte-identical ("Beyoncé" vs "Beyonce", "Guns N' Roses" vs "Guns N Roses",
featured-artist phrasing) so exact string equality is far too strict.
"""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

_FEATURING_RE = re.compile(
    r"\s*[\(\[]?\s*(feat\.?|featuring|ft\.?|with)\s+.+?[\)\]]?\s*$", re.IGNORECASE
)
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_text(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _PUNCT_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def strip_featuring(artist: str | None) -> str:
    if not artist:
        return ""
    return _FEATURING_RE.sub("", artist).strip()


def text_similarity(a: str | None, b: str | None) -> float:
    """Token-order-insensitive similarity in [0, 1]."""
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    return fuzz.token_sort_ratio(na, nb) / 100.0


def artist_similarity(a: str | None, b: str | None) -> float:
    """Like text_similarity but tries both the full string and the string
    with a trailing "feat. X" clause stripped, taking the best score --
    featured-artist phrasing is a common source of otherwise-spurious
    mismatches between YouTube titles and MusicBrainz artist credits."""
    direct = text_similarity(a, b)
    stripped = text_similarity(strip_featuring(a), strip_featuring(b))
    return max(direct, stripped)


def is_fuzzy_match(a: str | None, b: str | None, threshold: float = 0.72) -> bool:
    return text_similarity(a, b) >= threshold
