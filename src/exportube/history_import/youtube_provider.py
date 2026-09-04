"""Account/session-based watch-history acquisition ("Method A").

IMPORTANT, and the reason this file looks the way it does: the YouTube
Data API v3 does NOT expose a user's watch history. Google removed
API access to the "Watch History" and "Watch Later" playlists around
2016; today's `activities().list(mine=True)` endpoint returns the
authenticated user's own channel activity (uploads, public playlist
additions, subscriptions) -- not videos they watched. There is no scope,
no endpoint, and no documented workaround that returns watch history
through the official API. We do not pretend otherwise.

What IS technically viable, and what this module implements as the
primary path for Method A:

  1. OAuth (google-auth-oauthlib) against the YouTube Data API v3 for the
     things the API *does* legitimately expose: the "Liked videos"
     playlist, the user's own playlists and their membership, and the
     uploads playlist. Useful for `source_playlist_name`/`source_playlist_id`
     enrichment, not for watch history itself.

  2. Browser-session acquisition: YouTube's own web UI at
     https://www.youtube.com/feed/history renders the watch history page
     for the logged-in user. yt-dlp can extract this page's video listing
     given the user's browser cookies (--cookies-from-browser) or an
     exported Netscape cookies.txt, the same way it extracts any other
     playlist-shaped page the user is authorized to view. This is the
     only technically viable "authenticated, non-Takeout" way to obtain
     watch history, and it is what exportube uses when the user picks
     "YouTube account" instead of "Google Takeout" as the input method.

     Known limitation, documented and surfaced in the UI: the history
     feed page does not expose a reliable per-video "watched at" timestamp
     through yt-dlp's extraction (only day-level section headers in the
     rendered page, which yt-dlp does not structurally attach to each
     entry). Events from this provider are therefore stored with
     watched_at = NULL and rely on relative feed order only; if precise
     watch timestamps matter, Takeout is the accurate source.
"""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Iterator

from exportube.history_import.base import HistoryProvider
from exportube.history_import.url_parse import parse_video_url
from exportube.storage.models import WatchEvent

logger = logging.getLogger(__name__)

HISTORY_FEED_URL = "https://www.youtube.com/feed/history"

OAUTH_SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


class YouTubeSessionProvider(HistoryProvider):
    """Fetches the watch-history feed via yt-dlp + browser cookies."""

    name = "youtube_session"

    def __init__(self, cookies_from_browser: str | None = None, cookies_file: str | None = None):
        if not cookies_from_browser and not cookies_file:
            raise ValueError(
                "YouTubeSessionProvider requires either cookies_from_browser "
                "(e.g. 'chrome') or cookies_file (Netscape cookies.txt path). "
                "Set EXPORTUBE_COOKIES_FROM_BROWSER or EXPORTUBE_COOKIES_FILE, "
                "or use Google Takeout import instead."
            )
        self.cookies_from_browser = cookies_from_browser
        self.cookies_file = cookies_file
        self.batch_id = uuid.uuid4().hex[:12]

    def describe_capabilities(self) -> dict:
        return {
            "can_retrieve": [
                "The list of videos currently shown on your YouTube watch "
                "history page, most-recent-first, using your logged-in browser session",
                "Video titles/channel names as currently displayed (may differ "
                "from what was shown when you actually watched, if since edited)",
            ],
            "cannot_retrieve": [
                "Precise per-video watched timestamps (YouTube's history page "
                "does not expose these in a machine-readable per-item way)",
                "Anything already removed from your account's watch history",
                "Any data if watch history is paused or the account uses "
                "Enhanced Safe Browsing / advanced protection that blocks yt-dlp's session use",
            ],
            "notes": "Requires a real, currently logged-in browser session on this "
                     "machine (via --cookies-from-browser) or a manually exported "
                     "cookies.txt. Credentials/cookies are read locally by yt-dlp and "
                     "never leave this machine; they are not written into the CSV output. "
                     "For accurate watch dates, prefer Google Takeout import.",
        }

    def fetch(self) -> Iterator[WatchEvent]:
        import yt_dlp

        ydl_opts = {
            "extract_flat": "in_playlist",
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
        }
        if self.cookies_from_browser:
            ydl_opts["cookiesfrombrowser"] = (self.cookies_from_browser,)
        if self.cookies_file:
            ydl_opts["cookiefile"] = self.cookies_file

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(HISTORY_FEED_URL, download=False)

        entries = info.get("entries", []) if info else []
        for position, entry in enumerate(entries):
            if entry is None:
                continue
            video_id = entry.get("id")
            url = entry.get("url") or (f"https://www.youtube.com/watch?v={video_id}" if video_id else None)
            parsed = parse_video_url(url)

            yield WatchEvent(
                video_id=parsed.video_id or video_id,
                video_url_raw=url,
                raw_title=entry.get("title"),
                raw_channel_name=entry.get("uploader") or entry.get("channel"),
                watched_at=None,  # see module docstring: not exposed per-item
                source="youtube_session",
                source_playlist_name="Watch History",
                source_playlist_id=None,
                import_batch_id=self.batch_id,
                raw_json=json.dumps({"position": position, **{k: v for k, v in entry.items()
                                                                if isinstance(v, (str, int, float, type(None)))}}),
            )


class YouTubeOAuthClient:
    """Thin OAuth wrapper for the parts of Method A the official Data API
    genuinely supports: enumerating the user's playlists and their
    membership (for source_playlist_name/id enrichment), and looking up
    the Liked Videos playlist. NOT used for watch history (see module docstring).
    """

    def __init__(self, client_secrets_file: str, token_store: str):
        self.client_secrets_file = Path(client_secrets_file)
        self.token_store = Path(token_store)

    def get_credentials(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        creds = None
        if self.token_store.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_store), OAUTH_SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.client_secrets_file.exists():
                    raise FileNotFoundError(
                        f"OAuth client secrets file not found: {self.client_secrets_file}. "
                        "Create an OAuth Desktop App client at "
                        "https://console.cloud.google.com/apis/credentials and download it."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.client_secrets_file), OAUTH_SCOPES
                )
                creds = flow.run_local_server(port=0)
            self.token_store.parent.mkdir(parents=True, exist_ok=True)
            self.token_store.write_text(creds.to_json(), encoding="utf-8")

        return creds

    def build_service(self):
        from googleapiclient.discovery import build

        return build("youtube", "v3", credentials=self.get_credentials())

    def fetch_liked_and_playlists(self) -> Iterator[WatchEvent]:
        """Best-effort supplementary source: videos in the user's playlists
        (including Liked Videos) with real playlist context. This is NOT
        watch history and events are tagged accordingly; the pipeline treats
        it as additional discovery, not a replacement for history import.
        """
        service = self.build_service()
        batch_id = uuid.uuid4().hex[:12]

        playlists = [{"id": "LL", "snippet": {"title": "Liked videos"}}]
        request = service.playlists().list(part="snippet", mine=True, maxResults=50)
        while request is not None:
            response = request.execute()
            playlists.extend(response.get("items", []))
            request = service.playlists().list_next(request, response)

        for playlist in playlists:
            playlist_id = playlist["id"]
            playlist_name = playlist["snippet"]["title"]
            item_request = service.playlistItems().list(
                part="snippet,contentDetails", playlistId=playlist_id, maxResults=50
            )
            while item_request is not None:
                try:
                    response = item_request.execute()
                except Exception as e:
                    logger.warning("Could not list playlist %s (%s): %s", playlist_name, playlist_id, e)
                    break
                for item in response.get("items", []):
                    snippet = item.get("snippet", {})
                    video_id = item.get("contentDetails", {}).get("videoId") or snippet.get("resourceId", {}).get("videoId")
                    if not video_id:
                        continue
                    yield WatchEvent(
                        video_id=video_id,
                        video_url_raw=f"https://www.youtube.com/watch?v={video_id}",
                        raw_title=snippet.get("title"),
                        raw_channel_name=snippet.get("videoOwnerChannelTitle"),
                        watched_at=None,
                        source="youtube_api_playlist",
                        source_playlist_name=playlist_name,
                        source_playlist_id=playlist_id,
                        import_batch_id=batch_id,
                        raw_json=json.dumps(item, ensure_ascii=False, default=str),
                    )
                item_request = service.playlistItems().list_next(item_request, response)
