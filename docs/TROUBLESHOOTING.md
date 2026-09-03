# Troubleshooting

## `import` fails with "No watch-history.json or watch-history.html found"

Your Takeout export didn't include the YouTube history data type, or you
pointed tune-history at the wrong directory. Re-check the Takeout request
(see `TAKEOUT_IMPORT.md` step 1) -- **YouTube and YouTube Music > history**
must be selected. If you extracted the zip yourself, point tune-history at
the top-level extracted folder (or the zip itself), not a subfolder --
it searches recursively.

## `import youtube` fails / returns nothing

- Confirm you're actually logged into YouTube in the browser you named
  with `--cookies-from-browser` (the browser doesn't need to be open, but
  its cookie store must exist and be current).
- Some browsers lock their cookie database while running; close the
  browser and retry if extraction fails outright.
- Watch history may be paused in your Google Account's activity
  controls -- check https://myactivity.google.com/activity/youtube.
- Enhanced Safe Browsing / Advanced Protection can block this. Use
  Takeout import instead.
- This path never returns per-video watch timestamps -- that's expected,
  not a bug; see `docs/YOUTUBE_AUTH.md`.

## `import` is slow

Should be well under a minute even for a large history (tested against a
real ~50,500-entry export: ~60s total, including parsing). If it's taking
noticeably longer, something else on the machine is likely competing for
disk I/O with the SQLite writes (each commit requires a disk sync) --
check for other processes writing heavily to disk and try again once
they're done. `import` batches its writes (commits every 1000 rows, not
every row) specifically to avoid this being a bottleneck; if you're
running an older checkout without that fix, update -- a naive per-row-commit
importer measured **~74 minutes** for the same 50,500-row export on the
same machine.

## `scan` is slow / rate-limited

yt-dlp extraction is the bottleneck for large histories. Increase
`rate_limits.ytdlp_concurrent_extractions` in `config/config.yaml` (default
4) if your network can sustain more parallel requests. `scan` is resumable
-- if interrupted, re-running it only processes videos not yet done
(`tune-history stats` shows progress).

## `identify` is slow

MusicBrainz enforces a 1 request/second rate limit for unauthenticated use
(`rate_limits.musicbrainz_requests_per_second`) -- this is a hard external
constraint, not misconfiguration. For a large library, expect `identify` to
take roughly (distinct candidate queries) seconds. Results are cached
(`storage.cache_ttl_days.musicbrainz`, default 90 days), so re-running
`identify` after tuning confidence weights doesn't re-query MusicBrainz for
already-seen candidates.

## Everything is `unidentified` / `not_music`

- Check `tune-history stats` -- if `videos_detected_music` is much lower
  than `distinct_videos`, the issue is likely in music detection, not
  identification. Videos with generic titles and no YouTube Music/channel
  signal are conservatively classified `not_music` (false-positive
  protection is intentional -- see `docs/METHODOLOGY.md` section 1).
- If detection looks right but identification is failing, check that
  MusicBrainz is reachable (`TUNE_HISTORY_MUSICBRAINZ_CONTACT` must be set
  to a real contact per MusicBrainz's usage policy, or requests may be
  throttled/blocked).
- Lower `confidence.thresholds.low` in config if you'd rather see more
  low-confidence guesses than `unidentified` rows (uncertainty is still
  visible in the `music_identification_confidence` column either way).

## A video is confidently identified as the *wrong* song

Open the review UI (`tune-history serve` -> Review Uncertain Matches, or
navigate directly to `/review/<video_id>`) and either accept a different
candidate from the alternatives list or enter a manual correction. Manual
corrections are stored separately from automated identification and always
win on export/re-export -- see `export/csv_export.py _apply_correction`.

## `ModuleNotFoundError` / import errors

Make sure you installed with `pip install -e ".[dev]"` from the repo root
(not just `pip install tune-history` from elsewhere) and that your venv is
activated.

## SQLite "database is locked"

Only run one tune-history command (CLI or `serve`) against the same
`data/tune_history.sqlite3` at a time. The web UI (`serve`) holds a
long-lived connection; stop it before running CLI commands that write
heavily (`scan`/`identify`), or point `--config` at a config with a
different `storage.db_path`.
