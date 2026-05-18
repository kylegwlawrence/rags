#!/usr/bin/env python3
"""Backfill normalized author tables in data/openalex/openalex.db.

Adds two tables:
    authors      (id INTEGER PK, display_name TEXT UNIQUE)
    work_authors (work_id TEXT, author_id INTEGER, position INTEGER)

Populates them by splitting `works.authors` on ", " (the same separator the
downloader used to join names). Re-runnable: clears work_authors first so
each run reflects the current `works.authors` contents.

Known limitation (~0.08% of works at last check — about 220 of 268k):
author names that contain a literal ", " get fragmented. In practice this
is almost exclusively credentialed suffixes — "Smith, Jr.", "Jones, M.D.",
"Doe, PhD", "Foo, III" — where a single OpenAlex display_name becomes two
or three phantom rows in the `authors` table (e.g. "Smith" + "Jr."). The
proper fix is to re-download using OpenAlex's authorship IDs; out of scope
here.
"""

import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "openalex" / "openalex.db"

SEPARATOR = ", "


def main() -> int:
    if not DB_PATH.is_file():
        print(f"missing: {DB_PATH}", file=sys.stderr)
        return 1

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS authors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            display_name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS work_authors (
            work_id TEXT NOT NULL,
            author_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            PRIMARY KEY (work_id, position)
        );
        CREATE INDEX IF NOT EXISTS idx_work_authors_author
            ON work_authors(author_id);
    """)
    con.commit()

    # Wipe work_authors so this run reflects the current works.authors values.
    # Keep the authors table — its unique constraint handles re-adds for free,
    # and we don't want autoincrement IDs to churn.
    cur.execute("DELETE FROM work_authors")
    con.commit()

    t0 = time.time()
    work_count = 0
    link_count = 0
    name_cache: dict[str, int] = {}

    rows = cur.execute("SELECT id, authors FROM works").fetchall()
    for work_id, authors_str in rows:
        work_count += 1
        if not authors_str:
            continue
        names = [n.strip() for n in authors_str.split(SEPARATOR)]
        names = [n for n in names if n]
        for position, name in enumerate(names):
            author_id = name_cache.get(name)
            if author_id is None:
                cur.execute(
                    "INSERT OR IGNORE INTO authors (display_name) VALUES (?)",
                    (name,),
                )
                author_id = cur.execute(
                    "SELECT id FROM authors WHERE display_name = ?",
                    (name,),
                ).fetchone()[0]
                name_cache[name] = author_id
            cur.execute(
                "INSERT INTO work_authors (work_id, author_id, position) "
                "VALUES (?, ?, ?)",
                (work_id, author_id, position),
            )
            link_count += 1
        if work_count % 10000 == 0:
            con.commit()
            print(f"  processed {work_count} works ({link_count} links)...",
                  flush=True)

    con.commit()
    con.close()

    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s. "
          f"{work_count} works -> {link_count} author links "
          f"({len(name_cache)} unique names).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
