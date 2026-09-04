# Importing a Google Takeout export

This is the recommended acquisition method: it's offline, has precise
per-entry watch timestamps, and needs no ongoing authentication.

## 1. Request the export

1. Go to https://takeout.google.com
2. Click **Deselect all**, then find and select **YouTube and YouTube
   Music**.
3. Click **All YouTube data included** and make sure **history** is
   checked (you can deselect everything else to keep the export smaller).
4. Choose export format: leave the default (it includes both JSON and
   HTML history files; Exportube prefers JSON when both are present and
   falls back to HTML otherwise -- see `AGENTS.md` "Takeout formats").
5. Choose delivery method (a downloadable .zip is simplest) and create the
   export. Google will email you when it's ready -- for a long watch
   history this can take a while.

## 2. Import it

You can point Exportube at the `.zip` directly, or at an already
extracted directory -- both work, and it recursively finds the watch
history file regardless of Google's (locale-dependent) folder naming:

```bash
exportube import takeout.zip
# or
exportube import ./Takeout
```

Exportube looks for a file matching `watch-history*.json` (preferred)
or `watch-history*.html` (fallback) anywhere under the given path, so it
doesn't matter that the containing folder is named "YouTube and YouTube
Music" in English exports and something else in other locales.

## 3. What gets preserved

Whichever watch-history file was actually parsed is copied verbatim into
`data/raw_imports/<batch-id>/` before parsing, and the untouched original
JSON/HTML record for every entry is kept in the database's `raw_json`
column -- so you can always trace an exported CSV row back to exactly what
Google gave you, and re-run identification later without re-importing.

Every entry in the file is retained, including:

- Entries with no resolvable video URL (e.g. "Watched a video that has
  been removed") -- these still appear in the final CSV with blank
  metadata rather than being silently dropped.
- Repeat watches of the same video -- collapsed into one `videos` row with
  `watch_count`/`first_watched_date`/`latest_watched_date`, not discarded.

Re-running `exportube import` on the same file is a no-op (content-based
deduplication); running it on a *newer* Takeout export from the same
account only adds the new entries.

## Playlists (Liked videos, Watch later, custom playlists)

If your Takeout export also includes `playlists/*.csv` (select "playlists"
alongside "history" when requesting the export), `exportube import`
automatically picks these up too and adds `source_playlist_name` context
to any video that's in one -- pass `--no-include-playlists` to skip this.
Playlist membership is **never** treated as a watch: a video that's only
in a playlist (never actually in your watch history) still gets no watch
date and doesn't count toward `watch_count`. This part of the format
hasn't been verified against a real export yet -- see AGENTS.md "Needs
verification against a real Google Takeout export" if something looks off.

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) if `import` reports it
couldn't find a watch history file, or if timestamps look wrong.
