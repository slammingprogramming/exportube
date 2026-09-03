# YouTube account-based acquisition (Method A)

## The one thing to understand first

**The YouTube Data API v3 does not expose watch history.** Google removed
API access to the "Watch History" and "Watch Later" system playlists around
2016. `activities().list(mine=True)` returns *your own channel's* activity
(uploads, public playlist additions, subscriptions) -- not videos you
watched. There is no scope, no endpoint, and no documented workaround that
returns watch history through the official API. tune-history does not
pretend otherwise, and if you ask it for "YouTube account" import, it will
never silently fall back to something that isn't actually your watch
history.

## What tune-history does instead

`tune-history import youtube` uses **your logged-in browser session** (via
yt-dlp's `--cookies-from-browser`, or a manually exported Netscape
`cookies.txt`) to fetch `https://www.youtube.com/feed/history` -- the same
page you'd see if you opened your watch history in a browser -- and parses
the video listing from it. This is the same mechanism yt-dlp uses to
download any other playlist-shaped page you're authorized to view; it is
not an API, and it is not guaranteed to remain stable if YouTube changes
that page's structure (see `history_import/youtube_provider.py` for the
implementation, and `AGENTS.md` for what to do if it breaks).

```bash
tune-history import youtube --cookies-from-browser chrome
# or: --cookies-file /path/to/cookies.txt
```

### Known limitation: no per-video watch timestamps

YouTube's history feed page does not expose a machine-readable per-item
"watched at" timestamp (only day-level section headers in the rendered UI,
which are not attached to individual entries by yt-dlp's extraction).
Events from this provider are stored with `watched_at = NULL`; the export's
`first_watched_date`/`latest_watched_date` will be blank for videos
encountered *only* through this path. **If watch dates matter to you, use
Google Takeout import instead** (`docs/TAKEOUT_IMPORT.md`), which does
carry precise per-entry timestamps.

### Requirements

- A real, currently logged-in browser session on the machine running
  tune-history (`--cookies-from-browser chrome`/`firefox`/`edge`/...), or
  a manually exported cookies.txt.
- Watch history must not be paused in your Google Account settings.
- Enhanced Safe Browsing / advanced account protection can block
  yt-dlp's session use; if `import youtube` fails outright, use Takeout.

## Supplementary: OAuth for playlists (`import-playlists`)

The Data API v3 *does* legitimately support enumerating your own playlists
and their membership, including the "Liked videos" system playlist. This is
not watch history, but it's useful additional context (source playlist
name/ID) for videos you've also encountered in your watch history.

1. Create an OAuth client at
   https://console.cloud.google.com/apis/credentials (type: **Desktop
   app**), download the client secret JSON.
2. Set `TUNE_HISTORY_GOOGLE_CLIENT_SECRETS_FILE` in `.env` to its path.
3. Run:

```bash
tune-history import-playlists
```

The first run opens a browser for you to authorize; the resulting token is
cached at `TUNE_HISTORY_GOOGLE_TOKEN_STORE` (default `./secrets/token.json`)
and refreshed automatically afterward. Only the
`https://www.googleapis.com/auth/youtube.readonly` scope is requested.

`scan --use-youtube-api` also uses these same credentials, if present, to
fetch supplementary official category/topic metadata per video (not
required -- yt-dlp alone covers the core pipeline).

## If YouTube changes the history feed page

`history_import/youtube_provider.py`'s `YouTubeSessionProvider` is the only
place this scraping happens, isolated behind the same `HistoryProvider`
interface as Takeout import. If `/feed/history` extraction breaks, yt-dlp
usually ships a fix quickly (it's a widely-used tool for exactly this kind
of page); pin/upgrade the `yt-dlp` dependency first before assuming
tune-history's own code needs changes.
