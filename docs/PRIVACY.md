# Privacy

Exportube is designed to run entirely on your machine. This document is
a complete accounting of what data goes where.

## What stays local, always

- Your raw watch history (imported Takeout files or the browser-session
  fetch result) -- stored only in the local SQLite database
  (`data/exportube.sqlite3` by default) and, for Takeout, a preserved
  copy of the exact source file under `data/raw_imports/<batch>/`.
- All caching (YouTube metadata, MusicBrainz responses) -- local SQLite,
  never synced anywhere.
- OAuth tokens (if you use `import-playlists`) -- stored at the path in
  `EXPORTUBE_GOOGLE_TOKEN_STORE` (default `./secrets/token.json`), read
  only by this application.
- Browser cookies used for session-based watch-history import -- read
  in-process by yt-dlp via `--cookies-from-browser`; never written to disk
  by Exportube, never included in any export.
- The exported CSVs -- written only to the path you specify.

**Credentials and cookies are never written into the exported CSV.** The
CSV schema (see `export/csv_export.py HISTORY_FIELDS`) contains no
authentication material by construction.

## What leaves your machine, and to whom

| Service | What is sent | When | Why |
|---|---|---|---|
| YouTube (via yt-dlp) | A video ID / URL, as an anonymous or cookie-authenticated HTTP request | `scan` stage, once per video (cached after) | To retrieve title, duration, uploader, description, and YouTube Music metadata |
| YouTube Data API v3 (optional) | Video IDs (batched), or your OAuth-authenticated request for your own playlists/liked videos | `scan --use-youtube-api` / `import-playlists`, only if you set up OAuth credentials | Supplementary category/topic metadata; playlist context |
| MusicBrainz | An `artist` + `track` (+ `album`) search query, or an ISRC lookup | `identify` stage, once per unique query (cached after) | To resolve the canonical recording |
| Discogs (optional) | An `artist` + `track` (+ `album`) search query, plus your personal access token in the request | `identify` stage, only if `EXPORTUBE_DISCOGS_TOKEN` is set, once per unique query (cached after) | A second, independent source to corroborate or fill in a MusicBrainz miss |

No other third-party service is contacted. In particular: **your watch
history is never sent to any AI/LLM service, and never uploaded in bulk to
any third party.** MusicBrainz and Discogs only ever receive the specific
artist/track/album strings needed for one lookup at a time, not your
history. Neither MusicBrainz nor Discogs ever receives audio -- both
integrations are metadata search only; no video/audio is downloaded from
YouTube and forwarded anywhere (see AGENTS.md "Known limitations" for why
AcoustID-style audio fingerprinting was deliberately not implemented).

## Data you should treat as sensitive

Watch history can reveal a great deal about a person. Treat
`data/exportube.sqlite3`, `data/raw_imports/`, and any exported CSV as
sensitive personal data: don't commit them to a shared/public repository,
and control who else has filesystem access to them. `config/config.yaml`
and `.env` may contain OAuth client secrets/paths -- also keep those out of
version control (see `.gitignore`).

## Telemetry

None. Exportube does not phone home, does not report usage analytics,
and does not check for updates automatically.
