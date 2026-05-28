#!/usr/bin/env python3
"""Download Project Gutenberg texts via rsync from the ibiblio mirror.

Fetches the PG catalog CSV, filters by language, generates the expected
mirror paths, then rsyncs only the matching files into data/gutenberg/.

Usage:
    python scripts/gutenberg/gutenberg_download.py
    python scripts/gutenberg/gutenberg_download.py --language en,fr
    python scripts/gutenberg/gutenberg_download.py --language all
"""

import argparse
import csv
import io
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUTENBERG_ROOT = REPO_ROOT / "data" / "gutenberg"
CATALOG_URL = "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv"
RSYNC_SRC = "rsync://ftp.ibiblio.org/gutenberg/"


def _mirror_path(book_id: int) -> str:
    """Return the ibiblio mirror path for a book's canonical UTF-8 .txt file.

    The mirror nests books under a directory built by splitting the digits of
    (book_id // 10).  Examples:
        4    → 0/4/4-0.txt
        80   → 8/80/80-0.txt
        876  → 8/7/876/876-0.txt
        1000 → 1/0/0/1000/1000-0.txt
    """
    parent = "/".join(str(book_id // 10))
    return f"{parent}/{book_id}/{book_id}-0.txt"


def fetch_catalog(languages: set[str] | None) -> list[str]:
    """Fetch the PG catalog CSV and return mirror-relative paths for matching books.

    Args:
        languages: Set of lowercase language codes (e.g. {"en"}), or None for all.

    Returns:
        List of rsync-relative paths such as "8/80/80-0.txt".
    """
    print(f"Fetching catalog from {CATALOG_URL} …")
    with urllib.request.urlopen(CATALOG_URL, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(raw))

    paths: list[str] = []
    total = 0
    for row in reader:
        try:
            book_id = int(row["Text#"])
        except (KeyError, ValueError):
            continue
        total += 1

        if languages is not None:
            # Catalog language field can be comma-separated, e.g. "en, fr"
            book_langs = {lang.strip().lower() for lang in (row.get("Language") or "").split(",")}
            if not book_langs & languages:
                continue

        paths.append(_mirror_path(book_id))

    print(f"  {total} catalog entries, {len(paths)} match language filter")
    return paths


def run_rsync(files_from: Path, dry_run: bool) -> int:
    """Run rsync from the ibiblio mirror using a pre-built files-from list."""
    cmd = [
        "rsync",
        "--archive",
        "--verbose",
        "--progress",
        "--ignore-errors",  # continue if individual files are missing on the mirror
        f"--files-from={files_from}",
    ]
    if dry_run:
        cmd.append("--dry-run")
    cmd += [RSYNC_SRC, f"{GUTENBERG_ROOT}/"]
    print("Running:", " ".join(cmd))
    return subprocess.run(cmd).returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download Project Gutenberg texts via rsync from ibiblio"
    )
    parser.add_argument(
        "--language",
        default="en",
        metavar="CODES",
        help="Comma-separated language codes (e.g. 'en,fr'), or 'all' (default: en)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass --dry-run to rsync; shows what would be downloaded without fetching",
    )
    args = parser.parse_args()

    languages: set[str] | None = None
    if args.language.lower() != "all":
        languages = {code.strip().lower() for code in args.language.split(",")}

    GUTENBERG_ROOT.mkdir(parents=True, exist_ok=True)

    paths = fetch_catalog(languages)
    if not paths:
        print("No matching books found — check your --language value.", file=sys.stderr)
        return 1

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as fh:
        fh.write("\n".join(paths) + "\n")
        tmp_path = Path(fh.name)

    try:
        return run_rsync(tmp_path, dry_run=args.dry_run)
    finally:
        tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
