# Contributing to Exportube

Thanks for considering a contribution. This is a small, focused project --
keeping changes scoped and well-tested matters more here than volume.

## Before you start

For anything beyond a small fix, please open an issue first to discuss the
approach. In particular:

- Read [AGENTS.md](AGENTS.md) -- it's the architecture reference and
  explains *why* things are built the way they are, including several
  non-obvious decisions found by testing against a real Google Takeout
  export (see "Verified against a real Google Takeout export").
- Read [docs/METHODOLOGY.md](docs/METHODOLOGY.md) if your change touches
  music detection, identification, or confidence scoring.
- Security issues go through [SECURITY.md](SECURITY.md)'s process, not a
  public issue or PR.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env        # fill in EXPORTUBE_MUSICBRAINZ_CONTACT at minimum
pytest
```

## Making a change

1. Fork and branch from `main`.
2. Write or update tests alongside your change -- see AGENTS.md "Testing"
   for how the suite is organized and how to test against fakes rather
   than the network. Every module has a matching `tests/test_*.py`.
3. Run the full suite (`pytest`) before opening a PR; it should stay
   green and fast (no network calls).
4. Keep the change focused. If you find something else worth fixing along
   the way, mention it in the PR description or open a separate issue --
   don't bundle unrelated changes.
5. Update `AGENTS.md`/`docs/` if you change architecture, add a config
   option, or add/replace a provider -- these docs are expected to stay
   accurate, not aspirational.

## Adding a provider

If you're adding a new `metadata_enrichment`, `youtube_metadata`, or
`history_import` source, implement the relevant ABC (`MusicMetadataProvider`,
`VideoMetadataProvider`, `HistoryProvider`) and see AGENTS.md's "Adding a
new metadata_enrichment provider" / "Adding a new history source" sections
for the exact integration points. Document what data it sends externally
in [docs/PRIVACY.md](docs/PRIVACY.md) -- this project is privacy-first by
design, and that document is meant to be a complete accounting.

## Code style

No enforced formatter/linter yet -- match the existing style (see any
neighboring file): descriptive names over abbreviations, comments only
where the *why* isn't obvious from the code, no unnecessary abstraction.

## Reporting bugs / requesting features

Open a GitHub issue. For bugs, include what you expected, what happened,
and (with anything sensitive/personal redacted -- e.g. no real watch
history titles/URLs if you'd rather not share them) enough to reproduce.
