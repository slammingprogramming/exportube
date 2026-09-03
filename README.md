# tune-history

Turn a YouTube watch history into an enriched, auditable **music catalog CSV**.

[![tests](https://github.com/slammingprogramming/tune-history/actions/workflows/tests.yml/badge.svg)](https://github.com/slammingprogramming/tune-history/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Verified against a real Google Takeout export](https://img.shields.io/badge/verified-real%20Google%20Takeout%20export-success)](AGENTS.md#verified-against-a-real-google-takeout-export)
[![Security Policy](https://img.shields.io/badge/security-policy-informational.svg)](SECURITY.md)
[![Privacy: local-first](https://img.shields.io/badge/privacy-local--first-informational.svg)](docs/PRIVACY.md)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Maintained](https://img.shields.io/badge/maintained-yes-brightgreen.svg)](CHANGELOG.md)

Given your YouTube watch history (via Google Takeout or a browser-session
import), tune-history finds the videos that plausibly contain music,
identifies the actual recordings using YouTube's own music metadata plus
[MusicBrainz](https://musicbrainz.org) (and optionally
[Discogs](https://www.discogs.com)), scores its confidence in each
identification, and exports everything to CSV -- including *why* it believes
what it believes, so uncertain rows can be reviewed rather than trusted
blindly. DJ mixes/compilations with a timestamped tracklist in the
description get split into one identified row per song instead of one
guess for the whole mix.

Not just theoretically -- run against a real, live watch history (see
["Verified against a real export"](#verified-against-a-real-export) below):
**0 false positives** across every non-music video tested, and real songs
identified with accurate MusicBrainz artist/track/album/ISRC data, including
correctly merging two different video uploads of the same song into one
canonical recording.

See [`AGENTS.md`](AGENTS.md) for the full architecture reference (this is
the file to read before changing code). See `docs/` for methodology,
privacy, and setup documentation.

## Contents

- [Install](#install)
- [Quickstart (Google Takeout)](#quickstart-google-takeout)
- [Quickstart (YouTube account / browser session)](#quickstart-youtube-account--browser-session)
- [What you get](#what-you-get)
- [Resumability & offline processing](#resumability--offline-processing)
- [CLI reference](#cli-reference)
- [Verified against a real export](#verified-against-a-real-export)
- [Configuration](#configuration)
- [Testing](#testing)
- [Privacy](#privacy)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [Security](#security)
- [License](#license)

## Install

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in whatever you need (MusicBrainz
contact email is the only thing genuinely required; everything else --
YouTube OAuth, browser cookies, a Discogs token -- is optional and adds
capability incrementally without anything else changing. See
`.env.example` for what each variable does and where to get it.)

```bash
cp .env.example .env
```

## Quickstart (Google Takeout)

1. Request a Google Takeout export containing **YouTube and YouTube Music
   > history**, JSON format. See [docs/TAKEOUT_IMPORT.md](docs/TAKEOUT_IMPORT.md).
2. Run the pipeline:

```bash
tune-history import takeout.zip
tune-history scan
tune-history identify
tune-history export output.csv
```

Each step is safe to interrupt and re-run -- see "Resumability" below.

3. Optionally review uncertain matches in the browser:

```bash
tune-history serve
```

then open http://127.0.0.1:5000.

## Quickstart (YouTube account / browser session)

The official YouTube API does not expose watch history (see
[docs/YOUTUBE_AUTH.md](docs/YOUTUBE_AUTH.md) for why). tune-history instead
reads your logged-in browser's cookies to fetch your watch history feed:

```bash
tune-history import youtube --cookies-from-browser chrome
tune-history scan
tune-history identify
tune-history export output.csv
```

This does not expose precise per-video watch timestamps (a YouTube UI
limitation, not ours) -- prefer Takeout when watch dates matter.

## What you get

Two CSV files:

- **`youtube_music_history.csv`** -- one row per YouTube video encountered
  in your history (or one row per identified song for a DJ mix/compilation
  with a tracklist -- see `track_index`/`track_offset_seconds`), with raw +
  cleaned titles, identified artist/track/album, release date vs. video
  upload date (never conflated), watch counts, confidence level/score,
  MusicBrainz IDs, and the evidence that led to each decision.
- **`canonical_music_library.csv`** -- the same recordings deduplicated by
  MusicBrainz recording ID, with aggregated watch counts and every YouTube
  URL that maps to that recording.

See `examples/example_output/` for a sample of both, and
`docs/METHODOLOGY.md` for how identification and confidence scoring work.

## Resumability & offline processing

- `import` is idempotent: re-importing the same export doesn't duplicate rows.
- `scan` and `identify` only process videos not already processed for that
  stage, so an interrupted run picks back up where it left off.
- `scan` (metadata acquisition) and `identify` (matching/scoring) are
  separate stages: once metadata is cached, you can re-run `identify` with
  different confidence weights or thresholds without re-hitting the network.
- All YouTube metadata and MusicBrainz responses are cached in the local
  SQLite database (`data/tune_history.sqlite3` by default).

## CLI reference

```bash
tune-history import <takeout.zip|dir|youtube>   # acquire watch history (+ playlists/*.csv, if present)
tune-history import --no-include-playlists ...   # skip Takeout playlist CSVs
tune-history import-playlists                    # optional: Liked Videos/playlists via OAuth
tune-history scan --limit N                       # fetch metadata + detect music (optionally just N videos)
tune-history identify --limit N                   # MusicBrainz (+ Discogs, if configured) enrichment + scoring
tune-history export [output.csv|dir]              # write both CSVs
tune-history stats                                 # progress + confidence distribution
tune-history serve                                 # local review web UI
```

Set `TUNE_HISTORY_DISCOGS_TOKEN` (see `.env.example`) to have `identify`
automatically also query Discogs as a second, corroborating source --
omit it and `identify` runs on MusicBrainz alone, unchanged.

Run `tune-history <command> --help` for options. `scan`/`identify` accept
`--limit N` so you can try the pipeline against a slice of a large
history (network- and rate-limit-bound; a 50,000-video history could take
hours end to end) before committing to a full run -- unlimited videos
stay pending and a later call without `--limit` (or a higher one) picks
up where you left off.

## Verified against a real export

The Takeout parsing (both watch history and playlists) has been run
against a real, current Google Takeout export (~50,500 watch-history
entries, HTML format, plus a Watch-later playlist), not just synthetic
test fixtures -- that pass found and fixed four real bugs, most notably a
timezone-abbreviation parsing issue that was silently shifting every
HTML-sourced watch timestamp by several hours, and an import performance
bug that made a fresh 50,500-entry import take ~74 minutes before being
fixed to under a minute.

Beyond parsing, the full pipeline was then run end to end against real,
live YouTube (yt-dlp) and MusicBrainz -- not fake providers -- on a
50-video real-world batch (22 confirmed/likely songs spanning mainstream
hits, meme tracks, and independent artists, plus 28 confirmed non-music
videos for a false-positive check). Result: **0/28 false positives**, and
**18/20** real songs (with usable metadata) correctly identified with
accurate MusicBrainz artist/track/album/ISRC -- including correct
multi-artist credits and a duplicate-song-two-videos case the canonical
CSV correctly merged into one recording. That run found and fixed four
more real bugs in identification accuracy (an "Official HD Video" title
pattern that wasn't recognized, a "feat. X" clause breaking MusicBrainz
search, "Track - Artist" (reversed) titles being parsed backwards, and
age-restricted videos not being classified as unavailable).

See AGENTS.md "Verified against a real Google Takeout export" for the
full list, exact numbers, and what's still unverified (mainly:
non-English exports, and JSON-format history specifically, since the
verified export used HTML).

## Configuration

Defaults live in `config/default_config.yaml` (matching tolerances,
confidence weights/thresholds, rate limits, cache TTLs, paths). Copy it to
`config/config.yaml` to override, or set environment variables -- see the
comment at the top of `config/default_config.yaml` and `.env.example`.

## Testing

```bash
pytest
```

144 tests cover URL parsing, title parsing, duration/text matching, music
detection (including explicit false-positive guards), confidence scoring,
Takeout watch-history and playlist-CSV parsing, multi-track tracklist
splitting, the Discogs provider (HTTP mocked), unavailable-video
classification, and full pipeline integration. All run against fakes/mocks
(no network) -- see "Verified against a real export" above for the
separate real-data/real-network verification pass.

## Privacy

This tool is local-first: your watch history never leaves your machine
except as explicit, documented queries to YouTube (video lookups),
MusicBrainz (artist/track searches), and, only if you opt in with a token,
Discogs (artist/track searches). See [docs/PRIVACY.md](docs/PRIVACY.md)
for the full accounting of what goes where.

## Documentation

- [AGENTS.md](AGENTS.md) -- architecture reference for anyone (human or
  agent) working on this codebase
- [docs/METHODOLOGY.md](docs/METHODOLOGY.md) -- how detection/identification/confidence work
- [docs/PRIVACY.md](docs/PRIVACY.md) -- what data goes where
- [docs/YOUTUBE_AUTH.md](docs/YOUTUBE_AUTH.md) -- account-based acquisition, and its real limits
- [docs/TAKEOUT_IMPORT.md](docs/TAKEOUT_IMPORT.md) -- requesting/importing a Takeout export
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) -- common issues
- [CHANGELOG.md](CHANGELOG.md) -- notable changes by version

## Contributing

Contributions are welcome -- see [CONTRIBUTING.md](CONTRIBUTING.md) for
setup, testing expectations, and how the codebase is organized. This
project follows a [Code of Conduct](CODE_OF_CONDUCT.md).

## Security

Found a security or privacy issue? Please don't open a public issue with
details attached -- see [SECURITY.md](SECURITY.md) for the (verified,
private) reporting process.

## License

[MIT](LICENSE) &copy; [slammingprogramming](https://github.com/slammingprogramming)
