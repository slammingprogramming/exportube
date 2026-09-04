# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this
project doesn't yet follow strict semantic versioning (pre-1.0).

## [Unreleased]

### Added
- Takeout playlist-CSV ingestion (Liked videos, Watch later, custom
  playlists) as supplementary `source_playlist_name`/`source_playlist_id`
  context, cross-referenced against Takeout's `playlists.csv` manifest for
  real playlist IDs.
- Multi-track identification: DJ mixes/compilations/live sets with a
  timestamped tracklist in the description are split into one identified
  row per song instead of one guess for the whole video.
- Discogs as a second, optional `metadata_enrichment` provider, fanned out
  alongside MusicBrainz when `EXPORTUBE_DISCOGS_TOKEN` is set.
- `--limit N` on `scan`/`identify` for trying the pipeline against a slice
  of a large history before committing to a full run.
- Community-post URL recognition (`youtube.com/post/...`) so watch-history
  entries for YouTube Community posts get a distinct, accurate export note
  instead of looking like a generic parse failure.

### Fixed
Found via verification against a real Google Takeout export and a real
end-to-end run against live YouTube/MusicBrainz -- see AGENTS.md "Verified
against a real Google Takeout export" for full details and numbers:
- Timezone abbreviations (e.g. "EDT") in Takeout HTML timestamps were
  silently mis-parsed, shifting every HTML-sourced watch timestamp by
  several hours.
- Takeout's real playlist-membership filename (`"<Title>-videos.csv"`)
  didn't match the originally assumed format.
- `import` took ~74 minutes on a real ~50,500-entry history due to
  committing once per row; batched to under a minute.
- "Official HD/4K/... Video" title patterns (with a quality descriptor)
  weren't recognized as an official-video marker.
- A "feat. X" clause baked into a track title broke MusicBrainz search.
- "Track - Artist" (reversed) titles were parsed backwards.
- Age-restricted videos weren't classified as unavailable.

## [0.1.0] - initial build

- Core pipeline: `history_import` -> `youtube_metadata` -> `music_detection`
  -> `candidate_generation` -> `metadata_enrichment` (MusicBrainz) ->
  `matching` -> `confidence` -> `export`.
- Google Takeout import (JSON and HTML watch-history formats) and
  browser-session YouTube watch-history import (the official Data API
  does not expose watch history -- see docs/YOUTUBE_AUTH.md).
- Multi-signal music detection with explicit false-positive guards.
- Weighted, configurable confidence scoring with full evidence provenance.
- Two-CSV export: per-video history and a deduplicated canonical library.
- CLI (`exportube`) and a local Flask review UI for uncertain matches.
- Resumable, cached pipeline stages (SQLite-backed).
