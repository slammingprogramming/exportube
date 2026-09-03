"""Sync the public-safe subset of this repository into a separate working
tree (the public/GitHub repo's checkout). This project is maintained as
TWO separate git repositories -- a private one (everything) and a public
one (a curated subset) -- specifically so nothing internal-only
(AGENTS.md, any local memory/notes, real personal data used for testing)
can ever end up in the public repo's history, even by accident: the
public repo's git history only ever contains what this script explicitly
copied into it.

This script is intentionally an ALLOWLIST, not a denylist: it enumerates
exactly what's safe to publish rather than trying to remember every
future sensitive file to exclude. Nothing outside ALLOWED_PATHS is ever
copied, full stop.

Usage:
    python scripts/sync_public_repo.py <path_to_public_repo_checkout>

This only copies files -- it never runs `git add`/`commit`/`push`. Review
`git status` / `git diff` in the destination yourself before committing,
every time. See AGENTS.md "Dual-repo workflow (private/Keybase + public/
GitHub)" for the full process.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Top-level paths (relative to ROOT) that are safe to publish. Directories
# are copied recursively; files are copied as-is. Nothing else is ever
# touched in the destination except to remove stale copies of exactly
# these same entries (see _sync_entry).
ALLOWED_PATHS = [
    "src",
    "tests",
    "docs",
    "examples",
    "scripts",
    ".github",
    "config/default_config.yaml",
    "README.md",
    "LICENSE",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "CHANGELOG.md",
    ".gitignore",
    ".gitattributes",
    "pyproject.toml",
    "requirements.txt",
    ".env.example",
]

# Never copy these even if somehow present under an allowed path (belt and
# suspenders -- e.g. a stray __pycache__ or local db file inside tests/).
EXCLUDE_NAMES = {
    "__pycache__", ".pytest_cache", ".pytest_tmp", "*.pyc",
    "*.sqlite3", "*.sqlite3-wal", "*.sqlite3-shm",
    ".DS_Store", "Thumbs.db",
    # Build/install artifacts (e.g. `src/tune_history.egg-info/` from
    # `pip install -e .`) -- confirmed as a real gap: an earlier sync run
    # picked up 6 stray egg-info files because this list didn't mirror
    # .gitignore's `*.egg-info/` entry.
    "*.egg-info",
}

# Files/directories that must NEVER appear in the public repo. Not used to
# filter the copy (the allowlist above already guarantees that) -- used
# only as a post-copy sanity check that fails loudly if something's wrong.
FORBIDDEN_NAMES = {"AGENTS.md", "MEMORY.md", ".claude", "private-takeout-for-test", ".env", "secrets"}

# Crude secret-shaped-content scan over copied text files -- a safety net,
# not a substitute for reviewing `git diff` yourself before committing.
SUSPICIOUS_PATTERNS = [
    re.compile(r"BEGIN (OPENSSH|RSA|EC|DSA) PRIVATE KEY"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id shape
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),  # generic "sk-..." API key shape
    re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),  # GitHub personal access token shape
]

TEXT_SUFFIXES = {".py", ".md", ".txt", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".html", ".csv"}


def _should_skip(name: str) -> bool:
    return any(
        name == pattern or (pattern.startswith("*") and name.endswith(pattern[1:]))
        for pattern in EXCLUDE_NAMES
    )


def _copy_tree(src: Path, dst: Path) -> int:
    count = 0
    if dst.exists():
        shutil.rmtree(dst)
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        if any(_should_skip(part) for part in rel.parts):
            continue
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            count += 1
    return count


def _sync_entry(rel_path: str, dest_root: Path) -> int:
    src = ROOT / rel_path
    dst = dest_root / rel_path
    if not src.exists():
        print(f"  (skip, not found) {rel_path}")
        return 0
    if src.is_dir():
        return _copy_tree(src, dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return 1


def _sanity_check(dest_root: Path) -> list[str]:
    problems = []
    for forbidden in FORBIDDEN_NAMES:
        if list(dest_root.rglob(forbidden)):
            problems.append(f"Forbidden path present after sync: {forbidden}")
    for path in dest_root.rglob("*"):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for pattern in SUSPICIOUS_PATTERNS:
                if pattern.search(text):
                    problems.append(f"Suspicious secret-shaped content in {path.relative_to(dest_root)}")
    return problems


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    dest_root = Path(sys.argv[1]).resolve()
    if dest_root == ROOT or ROOT in dest_root.parents or dest_root in ROOT.parents:
        print(f"Refusing to sync: destination {dest_root} overlaps the private repo root {ROOT}.")
        sys.exit(1)

    dest_root.mkdir(parents=True, exist_ok=True)
    print(f"Syncing public-safe files from {ROOT} to {dest_root}\n")

    total = 0
    for rel_path in ALLOWED_PATHS:
        n = _sync_entry(rel_path, dest_root)
        total += n
        print(f"  {rel_path} -> {n} file(s)")

    print(f"\nCopied {total} files total.")

    problems = _sanity_check(dest_root)
    if problems:
        print("\n!!! SANITY CHECK FAILED -- DO NOT COMMIT/PUSH until resolved:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(2)

    print("\nSanity check passed (no forbidden paths, no obvious secret-shaped content).")
    print("This is NOT a substitute for reviewing `git status`/`git diff` yourself before committing.")
    print(f"\nNext: cd {dest_root} && git status")


if __name__ == "__main__":
    main()
