"""Build seed (pre-MusicBrainz) candidate artist/track identities from
every source of evidence a video offers: YouTube/YouTube Music structured
fields, the parsed title, and channel identity (Topic channels encode the
canonical artist name in the channel name itself).

Each seed candidate later gets enriched (metadata_enrichment) and scored
(confidence) -- this module only proposes plausible identities, it does
not judge between them.
"""
from __future__ import annotations

import re

from tune_history.candidate_generation.title_parser import TitleParseResult
from tune_history.matching.text_match import strip_featuring
from tune_history.music_detection.signals import TOPIC_CHANNEL_RE
from tune_history.storage.models import Candidate

_TOPIC_SUFFIX_RE = re.compile(r"\s*-\s*Topic$")
_ANY_BRACKET_RE = re.compile(r"[\[(][^\[\]()]*[\])]")


def _dedup_key(artist: str | None, track: str | None) -> tuple[str, str]:
    return ((artist or "").strip().lower(), (track or "").strip().lower())


def _strip_all_brackets(text: str | None) -> str | None:
    """Remove EVERY bracketed segment, not just recognized-decorative ones
    (title_parser.parse_title deliberately preserves unrecognized bracket
    text for *display*, e.g. "[4K Upgrade]", rather than guessing whether
    it's safe to delete -- see its docstring). That preserved text is
    exactly the kind of noise that can sink a MusicBrainz search query
    though (confirmed on a real example: "Horizon Fade [4K Upgrade]" found
    no match, but MusicBrainz's actual recording is titled "Horizon Fade").
    So this is used only to propose an *additional* search-oriented
    candidate, never to overwrite what's shown to the user.
    """
    if not text:
        return None
    stripped = _ANY_BRACKET_RE.sub("", text)
    stripped = re.sub(r"\s+", " ", stripped).strip(" -–—:|")
    return stripped or None


def build_seed_candidates(video: dict, title_parse: TitleParseResult) -> list[Candidate]:
    candidates: dict[tuple[str, str], Candidate] = {}

    def add(artist, track, album, release_date, evidence_name, source, points=1):
        if not track:
            return
        key = _dedup_key(artist, track)
        if key in candidates:
            c = candidates[key]
            c.evidence[evidence_name] = points
            if source not in c.sources:
                c.sources.append(source)
            if not c.album and album:
                c.album = album
            if not c.release_date and release_date:
                c.release_date = release_date
            return
        candidates[key] = Candidate(
            artist=artist,
            track=track,
            album=album,
            release_date=release_date,
            evidence={evidence_name: points},
            sources=[source],
        )

    # 1. YouTube / YouTube Music structured metadata -- the strongest seed.
    if video.get("yt_track"):
        add(
            video.get("yt_artist"), video.get("yt_track"), video.get("yt_album"),
            video.get("yt_release_date"), "youtube_music_track_field", "youtube_music",
        )

    # 2. Parsed title (Artist - Track pattern, decorative tags stripped).
    if title_parse.track_guess:
        add(
            title_parse.artist_guess, title_parse.track_guess, None, None,
            "title_parse", "title_parse",
        )
        # Also propose title-only-as-track with no artist, in case the dash
        # split mis-attributed a multi-word track as "artist - track".
        if title_parse.artist_guess:
            add(None, title_parse.clean_title, None, None, "title_parse_whole", "title_parse")

            # Titles aren't always "Artist - Track"; some real uploads use
            # "Track - Artist" instead (confirmed against a real example:
            # "Horizon Fade (Official Music Video) [4K Upgrade] - The Night
            # Owls", where the artist trails the track -- the primary
            # dash-split above got this backwards). Propose the swapped
            # reading too so MusicBrainz gets a fair shot either way;
            # confidence scoring (duration/text match) naturally favors
            # whichever orientation is actually correct, and the wrong one
            # just won't find a matching MusicBrainz recording. See
            # AGENTS.md "Verified against a real Google Takeout export".
            add(
                title_parse.track_guess, title_parse.artist_guess, None, None,
                "title_parse_swapped", "title_parse_swapped",
            )

        # Any bracketed text parse_title didn't recognize as decorative
        # survives into whichever field it was in (artist_guess OR
        # track_guess -- e.g. it ends up in artist_guess when the primary
        # dash-split got the "Track - Artist" ordering backwards) for
        # display purposes, but can sink a MusicBrainz search query
        # (confirmed on a real example -- see _strip_all_brackets
        # docstring). Propose bracket-stripped variants of both the
        # primary and swapped (artist, track) pairs purely as extra search
        # candidates -- never removing the bracket-preserving originals.
        for a, t, swapped in ((title_parse.artist_guess, title_parse.track_guess, False),
                              (title_parse.track_guess, title_parse.artist_guess, True)):
            stripped_a, stripped_t = _strip_all_brackets(a), _strip_all_brackets(t)
            if (stripped_a, stripped_t) != (a, t) and stripped_t:
                add(
                    stripped_a, stripped_t, None, None,
                    "title_parse_bracket_stripped", "title_parse_swapped" if swapped else "title_parse",
                )

        # A "feat./ft./featuring X" clause is often baked into the raw
        # track title text rather than cleanly separated out (e.g.
        # "Overdrive ft. MC Skyline", confirmed against a real example) --
        # MusicBrainz's actual recording title is usually just "Overdrive",
        # so also search on the track with that clause removed.
        stripped_track = strip_featuring(title_parse.track_guess)
        if stripped_track and stripped_track != title_parse.track_guess:
            add(
                title_parse.artist_guess, stripped_track, None, None,
                "title_parse_no_feature", "title_parse",
            )

    # 3. Channel identity: "<Artist> - Topic" and VEVO channels encode the
    # canonical artist name directly; combine with whatever track guess we have.
    uploader = video.get("uploader")
    if uploader and TOPIC_CHANNEL_RE.search(uploader):
        channel_artist = _TOPIC_SUFFIX_RE.sub("", uploader).strip()
        track_guess = title_parse.track_guess or title_parse.clean_title or video.get("title")
        add(channel_artist, track_guess, None, None, "topic_channel_identity", "channel_identity")

    if uploader and "vevo" in uploader.lower():
        # VEVO channel names are usually "ArtistVEVO" or "Artist VEVO".
        channel_artist = re.sub(r"vevo$", "", uploader, flags=re.IGNORECASE).strip()
        if channel_artist:
            track_guess = title_parse.track_guess or title_parse.clean_title or video.get("title")
            add(channel_artist, track_guess, None, None, "vevo_channel_identity", "channel_identity")

    return list(candidates.values())
