"""Command-line interface.

    tune-history import takeout.zip        # Google Takeout archive or extracted dir
    tune-history import youtube            # browser-session watch history
    tune-history scan                      # fetch YouTube/yt-dlp metadata + detect music
    tune-history identify                  # MusicBrainz enrichment + confidence scoring
    tune-history export output.csv         # write youtube_music_history.csv (+ canonical)
    tune-history stats                     # progress / confidence distribution
    tune-history serve                     # launch the local review web UI

Every command is safe to interrupt (Ctrl-C) and re-run -- see pipeline.py.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
from tqdm import tqdm

from tune_history.config import load_config
from tune_history.history_import.takeout_provider import TakeoutProvider
from tune_history.history_import.takeout_playlist_provider import TakeoutPlaylistProvider
from tune_history.history_import.youtube_provider import YouTubeOAuthClient, YouTubeSessionProvider
from tune_history.metadata_enrichment.musicbrainz_provider import MusicBrainzProvider
from tune_history.metadata_enrichment.discogs_provider import DiscogsProvider
from tune_history.metadata_enrichment.multi_provider import FanOutProvider
from tune_history.pipeline import Pipeline
from tune_history.storage.cache import Cache
from tune_history.storage.db import Database
from tune_history.youtube_metadata.youtube_api_provider import YouTubeAPIProvider
from tune_history.youtube_metadata.ytdlp_provider import YtDlpProvider


def _setup_logging(cfg):
    level_name = cfg.get("logging.level", "INFO")
    log_file = cfg.get("logging.file")
    handlers = [logging.StreamHandler(sys.stderr)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=getattr(logging, level_name, logging.INFO),
                         format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)


def _get_db(cfg) -> Database:
    return Database(cfg.db_path)


@click.group()
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None,
              help="Path to a config.yaml overriding config/default_config.yaml")
@click.pass_context
def cli(ctx, config_path):
    """tune-history: turn YouTube watch history into an enriched music catalog CSV."""
    cfg = load_config(config_path)
    _setup_logging(cfg)
    ctx.obj = {"config": cfg}


# ------------------------------------------------------------------------ import
@cli.command("import")
@click.argument("source")
@click.option("--cookies-from-browser", default=None, help="Browser to read cookies from (chrome, firefox, edge, ...)")
@click.option("--cookies-file", default=None, type=click.Path(exists=True), help="Netscape-format cookies.txt")
@click.option("--include-playlists/--no-include-playlists", default=True,
              help="For Takeout sources, also import playlists/*.csv (Liked videos, Watch later, "
                   "custom playlists) as supplementary source_playlist context. Never counted as watches.")
@click.pass_context
def import_cmd(ctx, source, cookies_from_browser, cookies_file, include_playlists):
    """Import watch history. SOURCE is either a path to a Google Takeout
    .zip/extracted directory, or the literal word "youtube" to pull your
    watch-history feed via a logged-in browser session."""
    cfg = ctx.obj["config"]
    db = _get_db(cfg)

    if source.lower() == "youtube":
        cookies_from_browser = cookies_from_browser or cfg.env("COOKIES_FROM_BROWSER") or None
        cookies_file = cookies_file or cfg.env("COOKIES_FILE") or None
        try:
            provider = YouTubeSessionProvider(cookies_from_browser, cookies_file)
        except ValueError as e:
            raise click.ClickException(str(e))
        caps = provider.describe_capabilities()
        click.echo("YouTube account (browser session) import -- capabilities:")
        for line in caps["can_retrieve"]:
            click.echo(f"  [can]    {line}")
        for line in caps["cannot_retrieve"]:
            click.echo(f"  [cannot] {line}")
        click.echo(f"  note: {caps['notes']}")
    else:
        path = Path(source)
        if not path.exists():
            raise click.ClickException(f"Path not found: {source}")
        provider = TakeoutProvider(path, cfg.data_dir / "raw_imports")
        caps = provider.describe_capabilities()
        click.echo("Google Takeout import -- capabilities:")
        for line in caps["can_retrieve"]:
            click.echo(f"  [can]    {line}")
        for line in caps["cannot_retrieve"]:
            click.echo(f"  [cannot] {line}")

    result = Pipeline(db, cfg).import_history(provider)
    click.echo(f"Imported {result['new']} new history entries "
               f"({result['duplicates']} already imported, "
               f"{result['unresolved']} without a resolvable video ID).")

    if source.lower() != "youtube" and include_playlists:
        playlist_provider = TakeoutPlaylistProvider(path)
        playlist_result = Pipeline(db, cfg).import_history(playlist_provider)
        if playlist_result["new"] or playlist_result["duplicates"]:
            click.echo(f"Imported {playlist_result['new']} new playlist-membership entries "
                       f"({playlist_result['duplicates']} already imported) from playlists/*.csv.")
        else:
            click.echo("No Takeout playlist CSVs found (this is fine if you didn't export "
                       "playlists, or ran --no-include-playlists).")


@cli.command("import-playlists")
@click.pass_context
def import_playlists_cmd(ctx):
    """Supplementary: import the user's YouTube playlists (incl. Liked
    Videos) via OAuth for extra source_playlist context. NOT watch history."""
    cfg = ctx.obj["config"]
    db = _get_db(cfg)
    client_secrets = cfg.env("GOOGLE_CLIENT_SECRETS_FILE", "./secrets/client_secret.json")
    token_store = cfg.env("GOOGLE_TOKEN_STORE", "./secrets/token.json")
    oauth_client = YouTubeOAuthClient(client_secrets, token_store)

    class _PlaylistProvider:
        name = "youtube_api_playlist"

        def describe_capabilities(self):
            return {"can_retrieve": ["Playlist membership (Liked videos + your playlists)"],
                    "cannot_retrieve": ["Watch history"], "notes": ""}

        def fetch(self):
            return oauth_client.fetch_liked_and_playlists()

    result = Pipeline(db, cfg).import_history(_PlaylistProvider())
    click.echo(f"Imported {result['new']} new playlist-derived entries ({result['duplicates']} duplicates).")


# -------------------------------------------------------------------------- scan
@cli.command()
@click.option("--use-youtube-api/--no-youtube-api", default=None,
              help="Also query the official YouTube Data API for category/topic data (requires OAuth setup)")
@click.option("--limit", type=int, default=None,
              help="Only scan the first N pending videos this run (try the pipeline on a slice of a "
                   "large history before committing to a full run). Resumable -- the rest stay pending.")
@click.pass_context
def scan(ctx, use_youtube_api, limit):
    """Fetch YouTube/yt-dlp metadata for every imported video and run
    music detection. Resumable: already-scanned videos are skipped."""
    cfg = ctx.obj["config"]
    db = _get_db(cfg)

    metadata_provider = YtDlpProvider(
        cookies_from_browser=cfg.env("COOKIES_FROM_BROWSER") or None,
        cookies_file=cfg.env("COOKIES_FILE") or None,
        max_workers=cfg.get("rate_limits.ytdlp_concurrent_extractions", 4),
    )

    api_provider = None
    client_secrets = Path(cfg.env("GOOGLE_CLIENT_SECRETS_FILE", "./secrets/client_secret.json"))
    if use_youtube_api or (use_youtube_api is None and client_secrets.exists()):
        try:
            oauth_client = YouTubeOAuthClient(
                str(client_secrets), cfg.env("GOOGLE_TOKEN_STORE", "./secrets/token.json")
            )
            api_provider = YouTubeAPIProvider(oauth_client)
        except Exception as e:  # noqa: BLE001
            click.echo(f"Warning: could not set up YouTube Data API provider ({e}); continuing with yt-dlp only.")

    with tqdm(desc="Scanning videos", unit="video") as bar:
        def progress_cb(done, total):
            bar.total = total
            bar.n = done
            bar.refresh()

        result = Pipeline(db, cfg).scan(metadata_provider, api_provider, progress_cb, limit=limit)

    click.echo(f"Scanned {result['processed']}/{result['total']} videos ({result['errors']} errors).")


# ---------------------------------------------------------------------- identify
@cli.command()
@click.option("--limit", type=int, default=None,
              help="Only identify the first N pending videos this run. Resumable -- the rest stay pending.")
@click.pass_context
def identify(ctx, limit):
    """Run MusicBrainz (+ Discogs, if TUNE_HISTORY_DISCOGS_TOKEN is set)
    enrichment, matching, and confidence scoring for every video that has
    been scanned but not yet identified. Splits dj_mix/compilation/
    album_stream/live_or_concert videos into per-song rows when a
    timestamped tracklist can be parsed from the description."""
    cfg = ctx.obj["config"]
    db = _get_db(cfg)
    cache = Cache(db, cfg.get("storage.cache_ttl_days.musicbrainz", 90))
    app, version, contact = cfg.musicbrainz_user_agent()
    mb_provider = MusicBrainzProvider(
        app, version, contact, cache=cache,
        rate_limit_per_sec=cfg.get("rate_limits.musicbrainz_requests_per_second", 1.0),
    )

    providers = [mb_provider]
    discogs_token = cfg.env("DISCOGS_TOKEN")
    if discogs_token:
        discogs_cache = Cache(db, cfg.get("storage.cache_ttl_days.discogs", 90))
        providers.append(DiscogsProvider(
            discogs_token, user_agent=f"{app}/{version} (+{contact})", cache=discogs_cache,
            requests_per_minute=cfg.get("rate_limits.discogs_requests_per_minute", 55.0),
        ))
        click.echo("Discogs enrichment enabled (TUNE_HISTORY_DISCOGS_TOKEN set).")

    enrichment_provider = providers[0] if len(providers) == 1 else FanOutProvider(providers)

    with tqdm(desc="Identifying music", unit="video") as bar:
        def progress_cb(done, total):
            bar.total = total
            bar.n = done
            bar.refresh()

        result = Pipeline(db, cfg).identify(enrichment_provider, progress_cb, limit=limit)

    click.echo(f"Identified {result['processed']}/{result['total']} videos ({result['errors']} errors).")


# ------------------------------------------------------------------------ export
@cli.command()
@click.argument("output", required=False, default=None)
@click.pass_context
def export(ctx, output):
    """Write youtube_music_history.csv and canonical_music_library.csv.
    OUTPUT may be a directory, or a .csv path (its directory is used and
    its filename overrides the history CSV's default name)."""
    cfg = ctx.obj["config"]
    db = _get_db(cfg)

    output_dir = cfg.output_dir
    history_override = None
    if output:
        out_path = Path(output)
        if out_path.suffix.lower() == ".csv":
            output_dir = out_path.parent if str(out_path.parent) != "." else output_dir
            history_override = out_path.name
        else:
            output_dir = out_path

    if history_override:
        cfg.set("export.history_csv_name", history_override)

    result = Pipeline(db, cfg).export(output_dir)
    click.echo(f"Wrote {result['history_rows']} rows to {result['history_csv']}")
    click.echo(f"Wrote {result['canonical_rows']} rows to {result['canonical_csv']}")


# ------------------------------------------------------------------------- stats
@cli.command()
@click.pass_context
def stats(ctx):
    """Show import/scan/identification progress and confidence distribution."""
    cfg = ctx.obj["config"]
    db = _get_db(cfg)
    counts = db.counts()
    click.echo("tune-history status")
    click.echo(f"  History entries imported : {counts['history_entries']}")
    click.echo(f"  Distinct videos          : {counts['distinct_videos']}")
    click.echo(f"  Metadata fetched         : {counts['videos_metadata_fetched']}")
    click.echo(f"  Detected as music        : {counts['videos_detected_music']}")
    click.echo(f"  Identified -- high       : {counts['videos_identified_high']}")
    click.echo(f"  Identified -- medium     : {counts['videos_identified_medium']}")
    click.echo(f"  Identified -- low        : {counts['videos_identified_low']}")
    click.echo(f"  Unidentified music       : {counts['videos_unidentified']}")


# -------------------------------------------------------------------------- serve
@cli.command()
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=5000, type=int)
@click.pass_context
def serve(ctx, host, port):
    """Launch the local review web UI (dashboard + uncertain-match review)."""
    cfg = ctx.obj["config"]
    from tune_history.web.app import create_app

    app = create_app(cfg)
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    cli()
