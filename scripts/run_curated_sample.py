"""Real-world verification helper: imports only the video IDs listed in an
allowlist file out of a real Takeout export, then runs the REAL pipeline
(yt-dlp + MusicBrainz, live network calls) end to end. Useful for trying
the pipeline against a hand-picked or sampled slice of a large real watch
history without committing to a full run -- see AGENTS.md "Verified
against a real Google Takeout export" for how this was used during
development.

Not part of the application itself. Writes its (personal-data-derived)
output under the given output directory -- never commit that output or
any real Takeout export data.

Usage:
    python scripts/run_curated_sample.py <takeout_path> <video_id_allowlist_file> <output_dir>

  takeout_path            Path to a Takeout .zip, an extracted directory,
                           or directly to a watch-history.json/.html file.
  video_id_allowlist_file  Text file, one YouTube video ID per line
                           ("#"-prefixed lines and blank lines ignored).
  output_dir               Where to write the working SQLite db and the
                           two exported CSVs.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tune_history.config import load_config
from tune_history.history_import.base import HistoryProvider
from tune_history.history_import.takeout_provider import TakeoutProvider
from tune_history.metadata_enrichment.musicbrainz_provider import MusicBrainzProvider
from tune_history.pipeline import Pipeline
from tune_history.storage.cache import Cache
from tune_history.storage.db import Database
from tune_history.youtube_metadata.ytdlp_provider import YtDlpProvider


class FilteredProvider(HistoryProvider):
    name = "filtered_real_subset"

    def __init__(self, inner: HistoryProvider, allowed_ids: set[str]):
        self.inner = inner
        self.allowed_ids = allowed_ids

    def fetch(self):
        for event in self.inner.fetch():
            if event.video_id in self.allowed_ids:
                yield event

    def describe_capabilities(self):
        return self.inner.describe_capabilities()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("takeout_path", type=Path, help="Takeout .zip, extracted dir, or watch-history file")
    parser.add_argument("allowlist_file", type=Path, help="Text file of video IDs to import, one per line")
    parser.add_argument("output_dir", type=Path, help="Where to write the working db and exported CSVs")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    db_path = args.output_dir / "sample.sqlite3"
    db_path.unlink(missing_ok=True)

    allowed_ids = {
        line.strip() for line in args.allowlist_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    print(f"Allowlist: {len(allowed_ids)} video IDs")

    db = Database(db_path)
    cfg = load_config()
    pipeline = Pipeline(db, cfg)

    real_provider = TakeoutProvider(args.takeout_path, args.output_dir / "_raw_preserved")
    filtered = FilteredProvider(real_provider, allowed_ids)
    import_result = pipeline.import_history(filtered)
    print("import:", import_result)

    metadata_provider = YtDlpProvider(max_workers=4)
    scan_result = pipeline.scan(metadata_provider)
    print("scan:", scan_result)

    cache = Cache(db, cfg.get("storage.cache_ttl_days.musicbrainz", 90))
    app, version, contact = cfg.musicbrainz_user_agent()
    mb_provider = MusicBrainzProvider(
        app, version, contact, cache=cache,
        rate_limit_per_sec=cfg.get("rate_limits.musicbrainz_requests_per_second", 1.0),
    )
    identify_result = pipeline.identify(mb_provider)
    print("identify:", identify_result)

    export_result = pipeline.export(args.output_dir)
    print("export:", export_result)
    db.close()


if __name__ == "__main__":
    main()
