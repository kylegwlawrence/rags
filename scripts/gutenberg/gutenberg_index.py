#!/usr/bin/env python3
"""Build data/gutenberg/gutenberg.db indexing every Project Gutenberg .txt
in data/gutenberg/, joined against the official PG catalog for metadata.

Re-runnable: INSERT OR REPLACE keyed on id.
"""

import csv
import io
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUTENBERG_ROOT = REPO_ROOT / "data" / "gutenberg"
DB_PATH = GUTENBERG_ROOT / "gutenberg.db"
CATALOG_URL = "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv"

# Canonical UTF-8 text file in this mirror: `<root>/<digit-path>/<id>/<id>-0.txt`
# (e.g. data/gutenberg/1/14/14-0.txt for book 14). The same directory may also
# contain old/ retired versions and LICENSE.txt — skip those.
FILENAME_RE = re.compile(r"^(\d+)-0\.txt$")


def fetch_catalog() -> dict[int, dict]:
    print(f"Fetching {CATALOG_URL} ...")
    with urllib.request.urlopen(CATALOG_URL, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(raw))
    out: dict[int, dict] = {}
    for row in reader:
        try:
            book_id = int(row["Text#"])
        except (KeyError, ValueError):
            continue
        out[book_id] = {
            "title": row.get("Title") or None,
            "author": row.get("Authors") or None,
            "language": row.get("Language") or None,
            "release_date": row.get("Issued") or None,
        }
    print(f"  catalog: {len(out)} entries")
    return out


def walk_texts(root: Path):
    """Yield (book_id, relative_path, size_bytes) for each canonical .txt file."""
    for digit in "0123456789":
        top = root / digit
        if not top.is_dir():
            continue
        for path in top.rglob("*-0.txt"):
            if "old" in path.parts:  # skip retired versions
                continue
            m = FILENAME_RE.match(path.name)
            if not m:
                continue
            try:
                book_id = int(m.group(1))
            except ValueError:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            yield book_id, str(path.relative_to(root)), size


def main() -> int:
    if not GUTENBERG_ROOT.is_dir():
        print(f"missing: {GUTENBERG_ROOT}", file=sys.stderr)
        return 1

    catalog = fetch_catalog()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS texts (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            title TEXT,
            author TEXT,
            language TEXT,
            release_date TEXT,
            size_bytes INTEGER
        )
    """)
    con.commit()

    t0 = time.time()
    inserted = 0
    missing_meta = 0
    batch: list[tuple] = []
    for book_id, rel_path, size in walk_texts(GUTENBERG_ROOT):
        meta = catalog.get(book_id)
        if meta is None:
            missing_meta += 1
            meta = {"title": None, "author": None, "language": None, "release_date": None}
        batch.append((
            book_id, rel_path,
            meta["title"], meta["author"], meta["language"], meta["release_date"],
            size,
        ))
        if len(batch) >= 1000:
            cur.executemany(
                "INSERT OR REPLACE INTO texts "
                "(id, path, title, author, language, release_date, size_bytes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            con.commit()
            inserted += len(batch)
            batch.clear()
            print(f"  indexed {inserted} files...", flush=True)
    if batch:
        cur.executemany(
            "INSERT OR REPLACE INTO texts "
            "(id, path, title, author, language, release_date, size_bytes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            batch,
        )
        con.commit()
        inserted += len(batch)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_texts_author ON texts(author)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_texts_title ON texts(title)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_texts_language ON texts(language)")
    con.commit()
    con.close()

    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s. Indexed {inserted} files "
          f"({missing_meta} without catalog metadata).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
