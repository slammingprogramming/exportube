# Methodology

How tune-history decides "is this music" and "which recording is this,"
and how much to trust that decision.

## 1. Music detection (`music_detection/`)

Every scanned video gets a `MusicDetectionResult`: `is_music` (bool),
`category` (see below), `score`, and `youtube_music_status`.

This is a **weighted multi-signal score**, not keyword matching. Signals
(see `music_detection/signals.py`, weights in `music_detection/detector.py
DEFAULT_WEIGHTS`):

| Signal | Default weight | What it means |
|---|---|---|
| YouTube Music track/artist fields present | +5 | YouTube itself attached music metadata to this video |
| `Provided to YouTube by ...` in description | +4 | Auto-generated distributor description -- very strong |
| MusicBrainz-eligible category via Data API (`categoryId=10`) | +3 | Official YouTube categorization |
| Topic channel (`Artist - Topic`) | +3 | YouTube's own auto-generated per-artist channel |
| VEVO channel | +3 | Official music-video distribution channel |
| "Official (Music) Video/Audio/Lyric Video" title marker | +3 | Conventional song-upload title pattern |
| Data API topic categories reference music genres | +2 | Independent official signal |
| Streaming links (Spotify/Apple Music/etc.) in description | +2 | Suggests a real commercial release |
| ISRC present in description | +3 | A real recording has an ISRC |
| Track-listing-shaped description (3+ timestamped lines) | +1 | Suggests album/compilation, still "music" |
| `Artist - Track` title shape | +1 | Weak alone, meaningful combined with anything else |
| Meta-content keywords (review/reaction/podcast/`#1234`/...) | **-6** | Strong negative: guards the false-positive cases in spec section 10 |

A video is `is_music` if the summed score >= `music_detection.min_score_to_consider_music`
(default 3, configurable). **Missing evidence is never treated as evidence
of absence** -- e.g. a video with no YouTube Music fields is not assumed to
be a "regular upload"; `youtube_music_status` is `unknown` unless there's
actual positive or negative evidence.

### Category

Independent of the music/not-music score, every music video gets a
`category`: `single_track`, `live_or_concert`, `compilation`, `dj_mix`,
`album_stream`, `music_video_with_extras`, `non_music`, or `unknown`.
Category comes from title keyword patterns first (mix/DJ/compilation/live/
album-stream phrasing), then duration heuristics (very long videos default
toward `compilation`; long videos with strong per-track evidence like a
Topic channel default toward `music_video_with_extras` rather than
`live_or_concert`), then typical-song-length + per-track-evidence checks
for `single_track`.

`potentially_multi_track` in the exported CSV is true for
`dj_mix`/`compilation`/`album_stream`/`live_or_concert` -- these videos may
contain several songs. See section 3a below for how (and when) they get
split into multiple identified rows.

## 2. Candidate generation (`candidate_generation/`)

`title_parser.py` splits `video_title_raw` into `video_title_clean` +
`artist_guess`/`track_guess`, stripping only purely decorative wrapper text
(`[Official Video]`, `(HD)`, ...) while preserving anything that
distinguishes a *version* of a recording (Live, Remix, Acoustic, Radio Edit,
2024 Remaster, ...) -- both in the clean title and as a separate
`version_markers` list. Unrecognized bracketed text is preserved rather
than guessed at.

`candidates.py` proposes **seed candidates** (artist/track guesses, no
MusicBrainz data yet) from every independent source available:

1. YouTube/YouTube Music structured fields (`yt_track`/`yt_artist`/`yt_album`)
2. The parsed title's artist/track split
3. Channel identity (`Artist - Topic` and `ArtistVEVO` channel names encode
   the canonical artist directly)

Duplicate (artist, track) proposals from different sources are merged, and
that merge itself becomes evidence later (`multiple_candidate_agreement`).

### 2a. Multi-track videos (`candidate_generation/tracklist_parser.py`)

For a `dj_mix`/`compilation`/`album_stream`/`live_or_concert` video,
`music_identification/identifier.py identify_multi_track()` first looks
for a timestamped tracklist in the description (`0:00 Artist - Track`,
`[03:45] Track (Live)`, numbered-list variants). Each matched line's
remainder text is run back through `title_parser.parse_title` -- the exact
same decorative-tag-stripping / version-marker logic used for whole-video
titles applies per line. If at least 2 timestamped entries are found, the
rest of the pipeline (candidate generation through confidence scoring)
runs **once per segment**, using that segment's own artist/track guess and
a duration computed from its own offset window (next entry's timestamp
minus this one's, or the video's total duration for the last entry) --
never the whole mix's duration. If no tracklist can be parsed, it falls
back to a single whole-video guess, honestly labeled as such.

The result: one CSV row per identified (or attempted) segment for a
multi-track video with a tracklist, exactly one row otherwise. See
AGENTS.md "Multi-track identification" for the storage/export mechanics.

## 3. Metadata enrichment: MusicBrainz + optional Discogs (`metadata_enrichment/`)

Each seed candidate (or tracklist segment) is used as a MusicBrainz
`recording`+`artist`(+`release`) search query. If the video's description
contains a detected ISRC, a direct ISRC lookup runs too (ISRC is close to
a guaranteed-correct join key). Every MusicBrainz release the recording
appears on becomes its own enriched candidate (release/album context
varies per release).

All queries and responses are cached (`storage/cache.py`, 90-day default
TTL) and rate-limited to MusicBrainz's documented 1 req/sec.

**Discogs** (`metadata_enrichment/discogs_provider.py`) is a second,
optional, independent source: search-only, same cadence/caching pattern,
active only when `TUNE_HISTORY_DISCOGS_TOKEN` is set. It's fanned out
alongside MusicBrainz via `metadata_enrichment/multi_provider.py
FanOutProvider` -- `identify()` doesn't know or care how many providers
are configured. Because Discogs doesn't expose MusicBrainz recording IDs,
its candidates merge into an existing MusicBrainz-identified candidate by
matching (artist, track) text rather than by ID (see AGENTS.md "Second
metadata_enrichment provider (Discogs) and candidate merging" for why the
merge logic specifically had to account for this), earning
`secondary_metadata_source_match` evidence (+3 default) when they agree.
Discogs' search results don't include per-track duration, so
Discogs-sourced candidates never contribute `duration_match` evidence on
their own -- only identity/text corroboration.

## 4. Duration matching (`matching/duration_match.py`)

Duration is a **weighted signal, never a hard gate** (spec section 8). A
continuous 0.0-1.0 score is computed from both the absolute-seconds
difference and the percentage difference (whichever is more forgiving wins,
since a fixed number of seconds means very different things for a 20-second
short vs. a 6-minute track), through three tolerance "regimes"
(strong/moderate/loose, each configurable). An explicit ratio gate still
forces the score to 0 for pathological cases (a 240s recording vs. a 7200s
video).

## 5. Confidence scoring (`confidence/engine.py`)

Every enriched candidate is scored against the video using a configurable
weighted sum of independent evidence (`config/default_config.yaml
confidence.weights`): YouTube Music field agreement, fuzzy title/artist text
match, duration match, presence of a MusicBrainz match, ISRC match, a
secondary provider (Discogs) independently agreeing, channel identity,
description evidence, release metadata presence, and cross-source
agreement. The raw point total is normalized against the sum of all
configured weights and clamped to `[0, 1]`.

`confidence_level` is then a threshold lookup (defaults: high >= 0.80,
medium >= 0.55, low >= 0.30, else `unidentified`) -- also configurable.
Candidates are ranked by score; the top one (if any reaches at least `low`)
becomes `selected`, the rest are retained as `alternatives` and exported in
`identification_evidence`/`candidate_count` for audit, never discarded.

### Worked example

A video from an `Artist - Topic` channel, titled `Artist - Track`, 320s
long, with YouTube Music `track`/`artist` fields matching a MusicBrainz
recording that also has a matching ISRC and a 0.2s duration difference,
typically lands around 0.80-0.95 (`high`) because youtube_music_track_field
+ youtube_music_artist_field + title/artist text match + duration_match +
musicbrainz_match + isrc_match + topic_channel_identity + release_metadata
+ cross_source_agreement all fire simultaneously. A video where only a
weak title-dash-split candidate exists with no MusicBrainz hit typically
scores under 0.30 and is `unidentified`, not force-assigned a song.

## 6. Not the same thing: `release_date` vs `video_upload_date`

`release_date` comes only from MusicBrainz (or, as weaker evidence,
YouTube's own `release_date`/`release_year` fields when the Music panel
sets them) -- never from when the video was uploaded to YouTube.
`video_upload_date` is tracked separately and is never used as a stand-in
for release date. See `storage/models.VideoRecord` and
`export/csv_export.py`.

## 7. Extending

- **New metadata_enrichment provider** (Cover Art Archive, AcoustID, ...):
  implement `metadata_enrichment.base.MusicMetadataProvider`
  (`search_recordings`, optionally `lookup_by_isrc`), add it to the
  `providers` list in `cli.py`'s `identify` command -- `FanOutProvider`
  handles the rest, including candidates merging correctly even without a
  MusicBrainz recording ID (see how Discogs works, AGENTS.md). Reuses
  `secondary_metadata_source_match`, or add a dedicated weight in
  `config/default_config.yaml confidence.weights`.
- **New history source**: implement `history_import.base.HistoryProvider`
  (`fetch() -> Iterator[WatchEvent]`, `describe_capabilities()`).
- **New video metadata provider**: implement
  `youtube_metadata.base.VideoMetadataProvider`.

None of these require touching storage schema or CSV export.
