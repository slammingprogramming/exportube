# Architecture

Reference for how Exportube is built and why -- module boundaries,
pipeline stages, key design decisions, and results from testing against
a real Google Takeout export. See
[`docs/METHODOLOGY.md`](METHODOLOGY.md) for *how* detection,
identification, and confidence scoring actually work (weights, formulas,
worked examples): this file covers *where things live and why*, that one
covers *what the numbers mean*.

## Tech stack and why

Python 3.10+. Chosen because every core dependency is Python-native and
best-in-class there: `yt-dlp` (YouTube extraction), `musicbrainzngs`
(MusicBrainz client), `requests` (Discogs REST calls),
`google-api-python-client` + `google-auth-oauthlib` (YouTube Data API
OAuth), `rapidfuzz` (fuzzy text matching), `click` (CLI), `Flask` (local
review UI), stdlib `sqlite3` (storage/cache -- no ORM, schema is small and
explicit enough not to need one).

## Architecture / module map

```
src/exportube/
  config.py                 Config loading: default_config.yaml + config.yaml + env overrides
  cli.py                    click CLI: import, import-playlists, scan, identify, export, stats, serve
  pipeline.py                Orchestrates the stages below; owns resumability

  storage/
    models.py                In-memory dataclasses (WatchEvent, VideoRecord, MusicDetectionResult,
                              Candidate, Identification, enums) shared across all stages
    db.py                    SQLite schema + all persistence/query methods (Database class)
    cache.py                 TTL-aware get_or_fetch() wrapper over the cache_entries table

  history_import/            Stage 1: acquisition -> common WatchEvent representation
    base.py                  HistoryProvider ABC (fetch() -> Iterator[WatchEvent], describe_capabilities())
    url_parse.py              Every YouTube URL shape -> video_id (watch/shorts/embed/youtu.be/live/bare id)
    normalize.py              Timestamp parsing + dedup-key computation (source-of-truth for "same entry")
    takeout_provider.py       Google Takeout watch history: locates watch-history.json/html recursively
                              (any locale), handles zip or extracted dir, preserves raw source, parses both formats
    takeout_playlist_provider.py  Google Takeout playlists/*.csv (Liked videos, Watch later, custom
                              playlists): supplementary source_playlist context, NEVER a watch event
                              (see "Verified against a real Google Takeout export" below, "Still
                              unverified" subsection)
    youtube_provider.py       YouTubeSessionProvider (browser-cookie scrape of /feed/history, since the
                              Data API cannot return watch history -- see docstring) + YouTubeOAuthClient
                              (playlists/liked-videos only, NOT watch history)

  youtube_metadata/          Stage 2: per-video metadata
    base.py                  VideoMetadataProvider ABC
    ytdlp_provider.py         Primary provider. Extracts title/uploader/duration/description/tags/
                              categories + YouTube Music panel fields (track/artist/album/release_date).
                              Classifies unavailable videos (private/deleted/unavailable) instead of raising.
    youtube_api_provider.py   Optional supplementary provider: official categoryId/topicCategories via
                              Data API v3 videos.list (batched 50/request)

  music_detection/           Stage 3: is this music, and what shape (single track / live / compilation / ...)
    signals.py                Independent, unit-testable signal extraction functions (no scoring)
    detector.py                MusicDetector: weighted scoring -> MusicDetectionResult (is_music, category,
                              youtube_music_status, signals). DEFAULT_WEIGHTS here, not in confidence/.

  candidate_generation/      Stage 4: propose artist/track identities (pre-enrichment)
    title_parser.py            raw title -> clean title + artist/track guess + preserved version markers
    candidates.py               Seed candidates from YT Music fields + parsed title + channel identity
    tracklist_parser.py         Timestamped tracklist extraction from a description (multi-track videos) --
                              reuses title_parser per line; see "Multi-track identification" below

  metadata_enrichment/       Stage 5/6: cross-reference authoritative music metadata
    base.py                    MusicMetadataProvider ABC (search_recordings, lookup_by_isrc) -- pluggable
    musicbrainz_provider.py    Primary implementation. Cached, rate-limited (1 req/s default).
    discogs_provider.py         Second, optional implementation (search-only, metadata-only, no audio).
                              Inactive unless EXPORTUBE_DISCOGS_TOKEN is set -- see .env.example.
    multi_provider.py           FanOutProvider: queries several MusicMetadataProviders and concatenates/
                              tags their results so identifier.py never needs to know how many are configured.

  matching/                  Stage 7 (part): scoring primitives, source-agnostic
    duration_match.py          Tolerant multi-regime duration scoring (never a hard gate)
    text_match.py               rapidfuzz-based fuzzy artist/title similarity, feat.-clause-aware

  confidence/                 Stage 8/9: weigh evidence -> score + level
    weights.py                  DEFAULT_WEIGHTS/DEFAULT_THRESHOLDS + config loaders
    engine.py                   ConfidenceEngine.score_candidate(): the actual weighted-sum + normalization

  music_identification/       Ties candidate_generation + metadata_enrichment + matching + confidence
    identifier.py                identify() -- single-track case; identify_multi_track() -- dispatches to
                              identify() for ordinary videos or splits a tracklist into per-segment calls.
                              Both pure functions, storage-agnostic -- pipeline.py handles persistence.

  export/                      Stage 10/11: CSV output
    csv_export.py                gather_export_rows() (joins everything, applies corrections, expands
                              multi-track videos to one row per segment), export_history_csv(),
                              export_canonical_csv() (dedup by MB recording id, falling back to artist+track)

  web/                         Local review UI (Flask)
    app.py                       Dashboard + review list/detail (grouped by track_index for multi-track
                              videos) + accept/manual-edit/mark routes
    templates/                   Server-rendered Jinja2 templates, minimal inline CSS, no JS framework
```

Every stage's interface (`HistoryProvider`, `VideoMetadataProvider`,
`MusicMetadataProvider`) is an ABC with one or more production
implementations today plus, in tests, fakes -- adding a new source/provider
never requires touching the stages around it.

## Pipeline stages and resumability

`pipeline.Pipeline` has four public methods matching the four CLI verbs:

1. **`import_history(provider)`** -- drains a `HistoryProvider`, inserts
   each `WatchEvent` into `history_entries` keyed by a content-derived
   `dedup_key` (source + video_id + url + watched_at + title, or a raw-JSON
   hash fallback when watched_at is unparseable). Re-importing the same
   export is a no-op; a newer export from the same account only adds new
   rows. **Every entry is kept, including ones with no resolvable
   video_id** (deleted videos, malformed URLs) -- these later appear in the
   export as their own rows via `csv_export.gather_export_rows`'s
   `unresolved_history_entries()` pass, since they have no video_id to key
   a `videos` row on. The CLI's `import` command also runs
   `TakeoutPlaylistProvider` automatically for Takeout sources (disable
   with `--no-include-playlists`); its events use `source="takeout_playlist"`
   and `watched_at=None` and are excluded from `watch_count`
   (`storage.db.WATCH_SOURCES`) since playlist membership is not a watch.

2. **`scan(metadata_provider, api_provider=None)`** -- for every distinct
   `video_id` not yet marked `metadata_status='done'` in
   `processing_status`, fetches metadata (cached via `storage.cache.Cache`,
   TTL from `storage.cache_ttl_days.youtube_metadata`), upserts `videos`,
   runs `MusicDetector`, saves `music_detection`, marks both stages done.
   An error on one video doesn't stop the run; it's recorded in
   `processing_status.last_error` and retried on the next `scan` call.

3. **`identify(mb_provider)`** -- for every video not yet
   `identification_status='done'`, calls
   `music_identification.identify_multi_track()` (which internally calls
   `identify()` once for an ordinary video, or once per tracklist segment
   for a dj_mix/compilation/album_stream/live_or_concert video with a
   parseable description tracklist -- see "Multi-track identification"
   below), and persists every group's scored candidates (not just winners)
   via `db.save_multi_track_identifications`. The `mb_provider` argument
   accepts any `MusicMetadataProvider`, including a `FanOutProvider`
   wrapping MusicBrainz + Discogs (cli.py wires this up automatically when
   `EXPORTUBE_DISCOGS_TOKEN` is set).

4. **`export(output_dir)`** -- pure read + CSV write, no state mutation
   beyond the files themselves. Safe to run repeatedly/at any point in the
   pipeline; unscanned/unidentified videos just show up with blank/
   `unidentified` fields rather than being excluded.

`scan`/`identify` are checked via `processing_status.<stage>_status`
columns, so `Ctrl-C` mid-run loses at most the one video in flight.

**Manual corrections** (from the review UI) are stored in a separate
`corrections` table and always override the automated identification at
export time (`csv_export._apply_correction`) -- they are never asked for
twice, and re-running `identify` after a correction does not clobber it
(export always checks `corrections` last). A correction on a multi-track
video collapses all of that video's segment rows back into a single
export row (see "Multi-track identification" below).

## Multi-track identification

Implemented in `candidate_generation/tracklist_parser.py` +
`music_identification/identifier.py identify_multi_track()`.

For an ordinary `single_track` video, `identify_multi_track()` just calls
`identify()` once and returns a length-1 list -- unchanged behavior.

For a `dj_mix`/`compilation`/`album_stream`/`live_or_concert` video,
it first tries to parse a timestamped tracklist out of the video's
description (`0:00 Artist - Track`, `[03:45] Artist - Track (Live)`,
numbered-list variants, etc. -- see `tracklist_parser.parse_tracklist`,
which reuses `title_parser.parse_title` on each line's remainder text so
decorative-tag-stripping and version-marker-preservation apply per-segment
exactly as they do for whole-video titles). If at least
`MIN_TRACKLIST_ENTRIES` (2) timestamped entries are found, it calls
`identify()` once per segment, with that segment's own artist/track guess
and its own offset-derived duration (next entry's offset minus this one's,
or the video's total duration for the last entry) instead of the whole
video's -- so duration matching compares a ~3-minute song against a
~3-minute segment, not against a 2-hour mix. Each resulting
`Identification` is tagged with `track_index`/`track_offset_seconds`/
`track_end_offset_seconds`.

If no usable tracklist exists (most DJ sets/live sets have no per-song
timestamps in their description), it falls back to a single whole-video
guess via `identify()`, with a note explaining why -- same honest
"unidentified rather than fabricated" behavior as before this feature
existed.

**Storage**: `identifications.track_index` (NULL for single-track) lets
multiple segments' candidate groups coexist per `video_id`.
`db.save_multi_track_identifications` replaces all of a video's
identification rows in one transaction; `db.get_candidates(video_id)`
returns every group's rows (ordered by track_index then rank),
`get_candidates(video_id, track_index=N)` scopes to one segment.

**Export**: `csv_export.gather_export_rows` groups a video's candidates by
`track_index` and emits one row per group -- one row per identified (or
attempted-but-unidentified) segment for a multi-track video, exactly one
row for an ordinary video. A segment that was found but didn't clear the
confidence threshold still gets its own row (blank artist/track, its own
`track_index`/offsets, `music_identification_confidence=unidentified`) so
"we found 3 segments, identified 2" stays visibly different from "we
couldn't split this mix at all" -- see spec section 16 ("preserve
uncertainty"). **A manual correction on a multi-track video collapses it
back to one row** (`_apply_correction`'s docstring) -- the review UI/CLI
correction actions are video-scoped, not segment-scoped, in this version;
correcting one wrong segment among several correct ones isn't supported
yet (see "Known limitations").

## Second metadata_enrichment provider (Discogs) and candidate merging

`metadata_enrichment/discogs_provider.py` implements the same
`MusicMetadataProvider` interface as MusicBrainz, search-only
(`database/search` with `artist`/`track`/`release_title` params), metadata
only -- no audio. Inactive unless `EXPORTUBE_DISCOGS_TOKEN` is set (a
free personal access token from
https://www.discogs.com/settings/developers); `cli.py`'s `identify`
command wraps `[MusicBrainzProvider, DiscogsProvider]` in
`metadata_enrichment/multi_provider.py FanOutProvider` when the token is
present, otherwise runs MusicBrainz alone exactly as before -- nothing
else changes when Discogs isn't configured.

**Candidate merge logic** (`music_identification/identifier.py identify()`,
the `merge()` closure) had to change to make this useful: candidates are
pooled by MusicBrainz recording ID when one is known (two different
recording IDs are genuinely different recordings/versions and must stay
distinct even if same-titled). A candidate with **no** recording ID --
a plain title-parse seed, a channel-identity guess, or a Discogs hit
(Discogs doesn't expose MusicBrainz IDs) -- instead attaches to whichever
already-pooled *recording-ID'd* candidate shares its normalized
(artist, track) text, so it corroborates that candidate's evidence
(`confidence.weights.secondary_metadata_source_match`, +3 default) instead
of silently becoming a separate, unmergeable duplicate that never
benefits from the corroboration. See `tests/test_secondary_provider_confidence.py`
for the scenario this fixes (a Discogs hit for the same artist/track a
MusicBrainz search already found must raise, not fragment, that
candidate's confidence).

## Key design decisions (and why)

- **`release_date` vs `video_upload_date` vs `first_watched_date`/
  `latest_watched_date`**: four genuinely different concepts, four
  separate columns, never conflated. `release_date` only ever comes from
  MusicBrainz/Discogs or YouTube's own Music-panel `release_date`/
  `release_year` fields -- never from `upload_date`. See
  `docs/METHODOLOGY.md` section 6.
- **Duration is a weighted signal, never a hard filter** -- see
  `matching/duration_match.py`, three configurable tolerance regimes plus
  a ratio-based zero-out gate for pathological mismatches.
- **False-positive protection is a first-class weight, not an afterthought**
  -- `music_detection.detector.DEFAULT_WEIGHTS["meta_content_keywords"] =
  -6`, specifically to keep "Why Taylor Swift's New Album Is Bad",
  "Top 10 Songs Used in...", and "Joe Rogan Experience #1234" out of the
  music catalog. Covered by `tests/test_music_detection.py`.
- **Missing evidence != negative evidence**. `youtube_music_status` is
  `unknown`, not `regular_upload`, unless there's actual contrary evidence
  (explicit non-music Data API category). Playlist membership
  (`takeout_playlist`/`youtube_api_playlist` sources) is never treated as
  a watch event -- `storage.db.WATCH_SOURCES` explicitly excludes them
  from `watch_count`/first/last-watched calculations. See
  `detector._youtube_music_status`.
- **Every candidate is retained, not just the winner** -- `identifications`
  table stores all scored candidates per video (per `track_index` group:
  `rank`, `is_selected`), exposed in the review UI and via
  `candidate_count`/`identification_evidence` in the CSV. Nothing is
  silently discarded for being uncertain; it's labeled uncertain instead.
- **Provenance is structural, not incidental** -- every export row carries
  `metadata_sources`, `identification_evidence` (JSON evidence-point
  breakdown), `match_method` (which evidence *groups* fired, e.g.
  `youtube_music_metadata+musicbrainz+duration`), and `identification_notes`.
- **Nothing is hard-coded as "always trusted"** -- confidence weights,
  detection weights, duration tolerances, and thresholds are all in
  `config/default_config.yaml`, overridable via `config/config.yaml` or
  `EXPORTUBE_*` env vars (see `config.py` docstring for the override
  syntax).

## Verified against a real Google Takeout export

A real Google Takeout export (default export settings, September 2026,
~50,500 watch-history entries + a Watch-later playlist of 336 videos, HTML
history format -- the account's export did not include watch-history.json,
so this also incidentally confirmed the HTML path is the one that matters
in practice) was used to check the assumptions the parsers were built on.
It lives locally at `private-takeout-for-test/` in this repo (both the
full original zips and a "Trimmed Extracts" subset with only the
history/playlists/subscriptions files) -- **treat this directory as
sensitive personal data, same as `data/`/`output/` (already gitignored);
never commit it or its contents.**

Eight real bugs were found and fixed as a direct result (four in
import/parsing, four in identification/metadata accuracy -- see the
second batch below, found via an end-to-end run against real YouTube +
MusicBrainz), all covered by new regression tests:

1. **Timezone abbreviations were silently mis-parsed** (`history_import/
   normalize.py`). Real HTML timestamps look like
   `"Mar 21, 2025, 11:04:24 PM EDT"`. `dateutil` cannot resolve bare
   abbreviations like "EDT" on its own -- it silently drops the zone and
   returns a *naive* datetime still in local time, which
   `normalize_timestamp` then wrongly stamped as if it were already UTC.
   This was a multi-hour error on **every single watch-history entry**
   parsed from HTML. Fixed with an explicit `tzinfos` table
   (`_FIXED_OFFSET_TZINFOS`) covering common North American/European/
   Australian/Asian abbreviations passed to `dateutil_parser.parse()`.
   Zones not in that table still silently degrade to the old (wrong)
   behavior -- a known remaining gap for less common zones. See
   `tests/test_normalize.py test_us_timezone_abbreviations_resolve_to_correct_utc_offset`.
2. **Takeout's playlist-membership filename doesn't match the playlist
   title exactly** (`history_import/takeout_playlist_provider.py`). Real
   filename is `"Watch later-videos.csv"`, not `"Watch later.csv"` as
   originally assumed -- `_derive_playlist_name` now strips a trailing
   `-videos`/`_videos` suffix. Also added: Takeout ships a separate
   `playlists.csv` manifest with each playlist's real Playlist ID (the
   per-playlist file never repeats it), now cross-referenced by title so
   `source_playlist_id` is actually populated instead of always blank.
   See `tests/test_takeout_playlist_provider.py`.
3. **YouTube Community post views appear in watch history** alongside
   actual video watches (`https://www.youtube.com/post/<id>`, ~0.2% of
   entries in the verified export). These correctly have no video ID (a
   Community post isn't a video), but were previously indistinguishable
   from a generic unparseable-URL failure. `url_parse.py` now recognizes
   `url_type="community_post"` explicitly, and `export/csv_export.py`
   gives these rows a distinct explanatory note and `video_category` value
   instead of the generic "couldn't resolve" message. See
   `tests/test_url_parse.py test_community_post_url_recognized_distinctly`
   and `tests/test_csv_export.py`.
4. **`import` was prohibitively slow at real scale** (`storage/db.py`,
   `pipeline.py`). `insert_history_entry` committed once per row; measured
   directly against the real 50,499-row export, a fresh `import` took
   **4445s (~74 minutes)** -- each commit's disk sync on this machine costs
   roughly 90ms (measured in isolation too), and 50,499 of them dominates
   everything else. Spec section 19 explicitly calls out "tens of
   thousands of history entries" as a requirement this violated. Added
   `Database.bulk_insert_history_entries` (commits every 1000 rows instead
   of every row; a caught per-row UNIQUE-constraint duplicate does not
   roll back the surrounding batch -- verified empirically, see that
   method's docstring) and switched `Pipeline.import_history` to use it.
   Re-measured cleanly (no other process competing for disk I/O) after the
   fix: the same fresh 50,499-row import, including HTML parsing, now
   takes **59.7s** -- roughly a **74x** speedup, and a re-import (all
   duplicates, exercising the dedup path) takes 30s. `insert_history_entry`
   (single-row, commits immediately) is unchanged and still used directly
   by tests/small call sites; only the bulk import path needed to change.

### End-to-end verification against real YouTube + MusicBrainz

Import/parsing correctness doesn't prove *identification* correctness, so
a second pass ran the full pipeline (`import` -> `scan` -> `identify` ->
`export`) against real, live yt-dlp and MusicBrainz -- not fake providers
-- on a hand-picked slice of the real export: 10 videos manually confirmed
to be real songs (spanning famous mainstream tracks, VEVO/Topic channels,
and small/independent artists likely absent from MusicBrainz) plus 8
videos manually confirmed to not be music (tutorials, gameplay, an
animated-show episode, a hardware review) as a false-positive check. See
`scripts/run_curated_sample.py` (reusable -- takes an allowlist file of
real video IDs and an output dir) and the new `Pipeline.scan(...,
limit=N)` / `Pipeline.identify(..., limit=N)` parameter (also exposed as
`exportube scan --limit N` / `identify --limit N`) that made trying a
slice of a 50,000-video history practical instead of an hours-long
all-or-nothing commitment.

Result on the first run: 7/10 real songs correctly identified with
accurate artist/track/album/ISRC, 0/8 non-music videos misidentified (no
false positives). The 3 misses were real bugs, not caution:

5. **"Official HD Video" (with a quality descriptor between "Official"
   and "Video") wasn't recognized**, unlike plain "Official Video" --
   confirmed on "CAKE - The Distance (Official HD Video)", whose channel
   had also been renamed away from "CakeVEVO" at some point (Vevo-branded
   channels have been renamed over the years), leaving no other signal
   and dropping the video below the is-music threshold entirely. Fixed
   `OFFICIAL_VIDEO_TITLE_MARKER_RE` (`music_detection/signals.py`) and
   `title_parser._DECORATIVE_PHRASES` to accept HD/HQ/4K/UHD/8K/1080p/720p
   between "Official" and "Video/Audio".
6. **A "feat./ft. X" clause baked into the raw track title broke the
   MusicBrainz search** -- confirmed on "Chamillionaire - Ridin' (Official
   Music Video) ft. Krayzie Bone": the actual MusicBrainz recording title
   is just "Ridin'", so searching for "Ridin' ft. Krayzie Bone" verbatim
   returned nothing. `candidate_generation/candidates.py` now also
   proposes the track with any trailing featuring clause stripped
   (reusing `matching.text_match.strip_featuring`) as an additional seed
   candidate.
7. **"Track - Artist" titles (artist trailing, not leading) get the
   dash-split backwards** -- confirmed on "New Divide (Official Music
   Video) [4K Upgrade] - Linkin Park". `build_seed_candidates` now also
   proposes the swapped reading as an additional candidate; confidence
   scoring (duration/text match) sorts out which orientation is actually
   correct, so a title in either order gets a fair shot at matching.

Re-running the same 18 videos after these three fixes: **9/10** real
songs correctly identified, still **0/8** false positives. The one
remaining miss (the same Linkin Park video) is a distinct, smaller issue:
`[4K Upgrade]` isn't recognized as decorative (correctly -- it's not pure
video-quality boilerplate, it describes a specific reupload variant, and
the parser is designed to preserve unrecognized bracket text rather than
guess), but that same preserved text becomes noise in the MusicBrainz
*search query* built from it, so the search returns nothing. The system's
behavior here is arguably correct, not broken: it stays honestly
`unidentified` (candidate_count=3, all low-scoring) rather than guessing
wrong. Not fixed further in this pass -- diminishing returns on one
specific title pattern; flagged here rather than chased. A general fix
would separate "text to preserve for display" from "text to use as a
search query" more thoroughly than the current single `clean_title`
serves both purposes today.

**Broader confirmation, 50-video real batch**: expanded to all 22
Topic/VEVO/"Official Video"-pattern candidates found in the real export
(spanning mainstream hits, meme/novelty tracks, and small independent
artists, several in Russian/mixed-language) plus 28 more real non-music
videos sampled for topic diversity (tutorials, interviews, gameplay,
memes). Result: **0/28 false positives** (every non-music video correctly
excluded); of the 20 music candidates that actually had usable metadata
(2 were age-restricted/deleted -- see below), **18/20 (90%) correctly
identified** with accurate artist/track/album/ISRC pulled from
MusicBrainz, including correct multi-artist credits ("Glorb feat. Dankton
& The PUFF"), a Cyrillic-title track, and a duplicate-song-two-videos case
that the canonical CSV correctly merged into one recording crediting both
YouTube URLs. The 2 remaining misses: the same known `[4K Upgrade]`
search-noise case above, and one small independent artist plausibly just
not in MusicBrainz at all (an honest `unidentified`, not a wrong guess).
This run also surfaced one more real, fixed issue:

8. **Age-restricted videos weren't recognized as a distinct unavailable
   reason.** yt-dlp's `"Sign in to confirm your age..."` error didn't
   match any of `youtube_metadata/ytdlp_provider.py`'s marker tuples, so
   it fell through to the generic `"unknown"` availability after 3 retries
   (harmless -- the video is still retained with blank metadata, per spec
   -- but less precise than it could be). Added to `_UNAVAILABLE_MARKERS`.
   See `tests/test_ytdlp_provider.py` (also the first dedicated tests for
   this provider's error-classification logic, added alongside this fix,
   using a monkeypatched `yt_dlp.YoutubeDL` rather than the network).

**Larger confirmation, 300-video real batch**: the same 22 known-music
candidates plus 278 more videos, this time sampled by even stride across
the *entire* ~46,500-distinct-video history (not hand-picked -- a genuine
general/random cross-section: shorts, tutorials, reaction clips, gaming,
vlogs, memes, several dozen private/deleted/age-restricted videos mixed
in). Result: **0/279 false positives** (every non-music video, and every
video with no usable metadata, correctly excluded from being claimed as
music) and **19/21 (90.5%) of detected-music videos correctly identified**
with accurate MusicBrainz artist/track/album/ISRC -- consistent with the
50-video result, now at 6x the scale and with a genuinely unbiased sample
rather than a curated one. The 2 misses were the same two specific,
already-diagnosed cases above (not new failure modes). No pipeline errors
across all 300 (`scan`/`identify` both reported `errors: 0`), including
graceful handling of every real removed/private/age-restricted/ToS-violating
video hit along the way (each correctly retained with blank metadata,
never crashing the run). See `scripts/run_curated_sample.py` to reproduce
with a different sample size or allowlist.

Also confirmed correct as originally built, no changes needed:

- Real HTML entries have **no second `<a>` tag for the channel/uploader**
  (`raw_channel_name` is simply `None` for HTML-sourced imports) --
  `takeout_provider._parse_html` already handled `len(links) == 1`
  gracefully.
- The narrow no-break space (U+202F) real Takeout HTML uses between
  seconds and AM/PM was already being normalized correctly
  (`normalize_timestamp`'s `cleaned = raw.replace(" ", " ")` --
  easy to misread as a no-op in an editor/terminal that renders U+202F
  indistinguishably from a regular space, but it isn't one).
- `watch-history.html`'s empty `mdl-typography--text-right` sibling divs
  (one per real entry, no `<a>` tags, used for a thumbnail image the text
  export doesn't include) are harmlessly filtered by the existing
  `if not links: continue` guard, even though the CSS-class selector
  technically over-matches them.
- `subscriptions.csv`'s real header (`Channel Id,Channel Url,Channel
  Title`) matches what `takeout_playlist_provider.py`'s header-detection
  was already built to exclude.

Still unverified (no real example available):

- **"Liked videos"'s exact filename** -- not present in the account this
  was verified against (no liked videos, or predates the feature). Code
  makes no special case for it; should Just Work the same as any other
  `<Title>-videos.csv`, but hasn't been seen directly.
- **`watch-history.json`** -- the account's real export used HTML, not
  JSON (a Takeout format choice, not something this app controls). If a
  JSON export becomes available, worth a real-file check too, though the
  JSON schema (`title`/`titleUrl`/`subtitles`/`time`/`products`) is far
  better documented publicly than the HTML/CSV shapes above were.
- Whether `is_music_category`/`topic_categories` (from the optional
  YouTube Data API supplementary lookup) actually look like what
  `music_detection/detector.py` assumes for real music vs. non-music
  videos in practice, at scale -- this needs a live Data API credential
  to check, not just Takeout data.
- Non-English-locale export structure (folder names, CSV header language)
  -- the account verified against was English/US.

## Known limitations / intentional scope cuts

- **`YouTubeSessionProvider` never returns per-video watch timestamps** --
  a real limitation of what YouTube's history feed page exposes to
  scraping, not a shortcut we took. Documented prominently in
  `docs/YOUTUBE_AUTH.md` and in the provider's own `describe_capabilities()`.
- **Multi-track corrections are video-scoped, not segment-scoped** -- see
  "Multi-track identification" above. Accepting/editing a candidate for a
  multi-track video collapses all of its segments into one corrected row;
  there's no UI/CLI action yet for "segment 2 of this mix is wrong, leave
  the other two alone."
- **Multi-track splitting requires a timestamped tracklist in the
  description.** Many DJ sets/live recordings have no such tracklist; those
  still get (correctly) a single honest "unidentified" or low-confidence
  whole-video guess rather than a fabricated per-song breakdown. Splitting
  audio directly (silence detection, beat/tempo-change detection) would
  need to actually process audio, which this metadata-only pipeline
  doesn't do by design (see next point).
- **The Flask review UI is single-user, single-connection, local-only** --
  one shared `Database`/sqlite3 connection, fine for the Flask dev server's
  default single-threaded operation, not built for concurrent use. This
  matches the spec's "practical interface," not a production multi-tenant
  web app.
- **AcoustID / Cover Art Archive are not implemented.** Discogs is (see
  above), proving the `MusicMetadataProvider` interface is genuinely
  pluggable. AcoustID specifically requires an audio fingerprint, which
  requires downloading actual audio -- a real privacy/scope/ToS trade-off
  this metadata-only pipeline deliberately avoids (spec section 21: "no
  uploading watch history to a third-party server," and downloading full
  audio streams from YouTube "just to fingerprint" is a materially
  different (and questionable) posture than the read-only metadata queries
  every other provider in this codebase makes). If audio fingerprinting is
  wanted, it should be an explicit, separately-consented-to opt-in, not a
  default -- flag this for the user rather than silently building it.
  Cover Art Archive would add album art, not identification signal, so it
  wasn't prioritized in this round.

## Testing

```bash
pytest              # all 146 tests
pytest -q tests/test_music_detection.py   # one module
```

Test files map roughly 1:1 to modules: `test_url_parse.py` (includes the
Community-post URL type), `test_title_parser.py`, `test_duration_match.py`,
`test_text_match.py`, `test_music_detection.py`, `test_candidates.py`,
`test_confidence_engine.py`, `test_normalize.py` (includes the
timezone-abbreviation and narrow-no-break-space regression tests),
`test_takeout_provider.py` (uses `tests/fixtures/watch-history.{json,html}`),
`test_takeout_playlist_provider.py` (uses `tests/fixtures/playlists/*.csv`,
including real-format fixtures for the "-videos.csv" filename suffix and
the playlists.csv ID manifest), `test_tracklist_parser.py`,
`test_multi_track_identification.py`, `test_discogs_provider.py` (uses
`responses` to mock HTTP), `test_multi_provider.py`,
`test_secondary_provider_confidence.py`, `test_csv_export.py` (the
Community-post export-note distinction), `test_ytdlp_provider.py`
(unavailable-video error classification, via a monkeypatched
`yt_dlp.YoutubeDL` rather than the network), and `test_pipeline_integration.py`
(full import->scan->identify->export flow against fake, non-network
providers, covering repeat watches, deleted videos, DJ mixes with and
without tracklists, non-music false positives, cross-video recording
dedup, and manual-correction override including the multi-track collapse
case).

`tests/conftest.py` provides a `db` fixture (throwaway SQLite per test).
Fake providers (`FakeMetadataProvider`, `FakeMusicBrainzProvider`, etc.)
are defined locally in whichever integration test file needs them,
implementing the real `VideoMetadataProvider`/`MusicMetadataProvider` ABCs
-- if you add a new integration test, follow that pattern rather than
hitting real yt-dlp/MusicBrainz/Discogs. `test_discogs_provider.py` uses
the `responses` library (already a dev dependency) to mock actual HTTP
instead, since it's testing the HTTP-calling code itself.

`scripts/run_curated_sample.py` is a different kind of test: it runs the
REAL pipeline (live yt-dlp + MusicBrainz, no fakes) against a hand-picked
allowlist of real video IDs out of a real Takeout export -- this is how
the identification-accuracy bugs above were found, and it's reusable
(and safe to keep in a public repo -- takes the Takeout path as an
argument, no hardcoded reference to any specific person's export) for
future real-data spot checks: `python scripts/run_curated_sample.py
<takeout_path> <allowlist.txt> <out_dir>`. Not part of the automated
`pytest` suite (needs live network + real personal data) and not
something to run casually -- see `Pipeline.scan(..., limit=N)`/
`identify(..., limit=N)` (also `exportube scan/identify --limit N`)
for trying a slice of a large real history without committing to a full
run.

`scripts/generate_example_output.py` regenerates
`examples/example_output/*.csv` using the same fake-provider pattern (no
network) -- rerun it if the CSV schema changes, so the shipped example
stays accurate. It includes a multi-track (dj_mix) example with one
deliberately-unidentified segment, and a playlist-membership example, so
the shipped CSV demonstrates both.

## Adding a new metadata_enrichment provider

1. Implement `metadata_enrichment.base.MusicMetadataProvider`.
2. In `cli.py`'s `identify` command, add it to the `providers` list (see
   how `DiscogsProvider` is added conditionally on an env var) -- it gets
   wrapped in `FanOutProvider` automatically alongside whatever else is
   configured. `identify_multi_track()`/`identify()` never need to change.
3. If the provider can't supply a MusicBrainz recording ID (like Discogs),
   nothing else is required for it to merge properly with other
   providers' candidates -- see "Second metadata_enrichment provider
   (Discogs) and candidate merging" above for why that works.
4. Add any new evidence weight to `config/default_config.yaml
   confidence.weights` and reference it in `confidence/engine.py
   score_candidate` (or reuse `secondary_metadata_source_match`, which
   already fires generically for any non-MusicBrainz source).
5. Document what data that provider sends externally in `docs/PRIVACY.md`.

## Adding a new history source

Implement `history_import.base.HistoryProvider` (`fetch()`,
`describe_capabilities()`), wire it into `cli.py`'s `import` command (or a
new command). It only needs to yield `storage.models.WatchEvent` objects --
everything downstream is source-agnostic. If the source represents
something other than an actual watch (like `takeout_playlist_provider.py`),
add its `source` value to the exclusion in mind when touching
`storage.db.WATCH_SOURCES` -- watch_count/first-seen/last-seen must only
ever come from real watch events.

## File/data you should never commit

`data/`, `output/`, `secrets/`, `.env`, `config/config.yaml`,
`private-takeout-for-test/` -- all gitignored (see `.gitignore`). They
contain personal watch history, OAuth tokens/Discogs tokens, local
overrides, or (the last one) a real person's actual Google Takeout export
used for verification -- see "Verified against a real Google Takeout
export" above. `examples/example_output/*.csv` is synthetic/
fake-provider-generated and safe to commit.

