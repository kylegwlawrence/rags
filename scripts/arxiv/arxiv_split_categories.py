#!/usr/bin/env python3
"""
Split arxiv.db into one SQLite database per parent category under
data/arxiv/categories/{parent}.db.

A paper's *home* shard is the parent of its ``primary_category``:

    math.AG   -> math
    astro-ph.CO -> astro-ph
    alg-geom  -> alg-geom   (legacy codes with no ".'' are their own parent)

So every paper lands in exactly one shard and the shard row counts sum back to
the monolith's total. The full ``categories`` string is kept on each row, so a
paper cross-listed into other parents is still discoverable by category filter.

Each shard is self-contained:
  - papers          : only the rows whose primary-category parent is this shard
  - authors         : only the authors referenced by those papers
  - paper_authors   : only the links for those papers
  - same indexes as the source

FTS (papers_fts) is NOT built here — run scripts/arxiv/arxiv_index_fts.py with
``--db`` against each shard you want searchable.

The source DB is opened read-only and never modified. Existing shards are
skipped unless ``--force`` is given.

Run from the repo root with the venv active::

    python scripts/arxiv/arxiv_split_categories.py
    python scripts/arxiv/arxiv_split_categories.py --parents math,math-ph,physics
    python scripts/arxiv/arxiv_split_categories.py --output-dir /var/arxiv-build
"""

import argparse
import os
import sqlite3

# A shard holds the three content tables plus the monolith's indexes. The
# column lists mirror data/arxiv/arxiv.db exactly so `INSERT ... SELECT *`
# lines up positionally.
SHARD_SCHEMA = """
    CREATE TABLE IF NOT EXISTS papers (
        id               TEXT PRIMARY KEY,
        oai_datestamp    TEXT NOT NULL,
        title            TEXT NOT NULL,
        abstract         TEXT NOT NULL,
        authors          TEXT NOT NULL,
        categories       TEXT NOT NULL,
        primary_category TEXT NOT NULL,
        submitted_date   TEXT NOT NULL,
        updated_date     TEXT,
        doi              TEXT,
        journal_ref      TEXT,
        comments         TEXT,
        html_content     TEXT,
        download_status  TEXT,
        downloaded_at    TEXT
    );
    CREATE TABLE IF NOT EXISTS authors (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        keyname      TEXT NOT NULL,
        forenames    TEXT NOT NULL DEFAULT '',
        display_name TEXT NOT NULL,
        affiliation  TEXT,
        UNIQUE(keyname, forenames, affiliation)
    );
    CREATE TABLE IF NOT EXISTS paper_authors (
        paper_id  TEXT NOT NULL,
        author_id INTEGER NOT NULL,
        position  INTEGER NOT NULL,
        PRIMARY KEY (paper_id, position)
    );
"""

# Built after the bulk inserts — faster than maintaining them during the copy.
SHARD_INDEXES = """
    CREATE INDEX IF NOT EXISTS idx_papers_download_status ON papers(download_status);
    CREATE INDEX IF NOT EXISTS idx_papers_primary_cat     ON papers(primary_category);
    CREATE INDEX IF NOT EXISTS idx_papers_submitted       ON papers(submitted_date);
    CREATE INDEX IF NOT EXISTS idx_paper_authors_author   ON paper_authors(author_id);
"""

# SQL expression for a primary_category's parent: the text before the first
# ".", or the whole code when there is no "." (legacy codes like alg-geom).
PARENT_EXPR = (
    "CASE WHEN instr(primary_category, '.') > 0 "
    "THEN substr(primary_category, 1, instr(primary_category, '.') - 1) "
    "ELSE primary_category END"
)


def list_parents(db_path: str) -> list[str]:
    """Return every distinct parent category in the source, busiest first."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            f"SELECT {PARENT_EXPR} AS parent, COUNT(*) AS n "
            "FROM papers GROUP BY parent ORDER BY n DESC"
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def split_parent(src_path: str, parent: str, out_path: str) -> int:
    """
    Create out_path, attach src_path read-only, and copy this parent's papers
    plus the authors / links they reference. Returns the paper count written.
    """
    dst = sqlite3.connect(out_path)
    try:
        dst.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")
        dst.executescript(SHARD_SCHEMA)

        abs_src = os.path.abspath(src_path)
        dst.execute(f"ATTACH DATABASE 'file:{abs_src}?mode=ro' AS src")

        # 1) Papers whose primary-category parent is this shard.
        dst.execute(
            f"INSERT INTO papers SELECT * FROM src.papers WHERE {PARENT_EXPR} = ?",
            (parent,),
        )
        # 2) Links for those papers (papers are now in the local table).
        dst.execute(
            "INSERT INTO paper_authors "
            "SELECT * FROM src.paper_authors "
            "WHERE paper_id IN (SELECT id FROM papers)"
        )
        # 3) The authors those links point at.
        dst.execute(
            "INSERT INTO authors "
            "SELECT * FROM src.authors "
            "WHERE id IN (SELECT author_id FROM paper_authors)"
        )
        dst.commit()
        dst.execute("DETACH DATABASE src")

        dst.executescript(SHARD_INDEXES)
        dst.commit()

        n: int = dst.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    finally:
        dst.close()
    return n


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Split arxiv.db into per-parent-category SQLite databases"
    )
    parser.add_argument(
        "--db",
        default="data/arxiv/arxiv.db",
        help="Source monolithic DB (default: data/arxiv/arxiv.db)",
    )
    parser.add_argument(
        "--output-dir",
        default="data/arxiv/categories",
        help="Directory for the shard DBs (default: data/arxiv/categories). "
        "Point this at a roomy filesystem to avoid /home pressure during the "
        "build, then move the shards you keep into data/arxiv/categories.",
    )
    parser.add_argument(
        "--parents",
        default=None,
        help="Comma-separated parent codes to build (e.g. math,math-ph,physics). "
        "Default: every parent found in the source.",
    )
    parser.add_argument(
        "--exclude",
        default=None,
        help="Comma-separated parent codes to skip (e.g. math,math-ph,physics to "
        "build only the OTHER categories). Applied after --parents.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing shard DBs instead of skipping them",
    )
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    if args.parents:
        parents = [p.strip() for p in args.parents.split(",") if p.strip()]
    else:
        print(f"Scanning parent categories in {args.db}...")
        parents = list_parents(args.db)

    if args.exclude:
        excluded = {p.strip() for p in args.exclude.split(",") if p.strip()}
        parents = [p for p in parents if p not in excluded]
        print(f"Excluding: {', '.join(sorted(excluded))}")

    print(f"Parents to process: {len(parents)} ({', '.join(parents)})\n")

    for i, parent in enumerate(parents, 1):
        out_path = os.path.join(args.output_dir, f"{parent}.db")
        if os.path.exists(out_path) and not args.force:
            print(f"[{i:3d}/{len(parents)}] {parent:12s} skip (exists)")
            continue

        print(f"[{i:3d}/{len(parents)}] {parent:12s} writing...", end="", flush=True)
        n = split_parent(args.db, parent, out_path)

        if n == 0:
            os.remove(out_path)
            print("  0 papers — removed")
        else:
            size_mb = os.path.getsize(out_path) / 1_048_576
            print(f"  {n:>8,} papers  {size_mb:9.1f} MB")

    print("\nDone. Source DB was not modified.")


if __name__ == "__main__":
    main()
