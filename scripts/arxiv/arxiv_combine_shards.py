#!/usr/bin/env python3
"""Combine all per-parent shard DBs into a single monolithic arxiv.db.

Author IDs differ across shards so a simple INSERT SELECT * would create
duplicates.  Instead we re-normalise authors via the UNIQUE(keyname,
forenames, affiliation) constraint and re-link paper_authors to the new IDs.

Usage::

    python scripts/arxiv/arxiv_combine_shards.py
    python scripts/arxiv/arxiv_combine_shards.py \\
        --shards-dir data/arxiv/shards \\
        --output /datasets/arxiv/arxiv.db

Pass ``--resume`` to continue an interrupted build: the existing output is
reused, and any shard already fully merged (its papers all present) is
skipped.  The merge is idempotent regardless (every insert is INSERT OR
IGNORE), so --resume only saves the wasted rescan of completed shards.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_SHARDS_DIR = REPO_ROOT / "data" / "arxiv" / "shards"
DEFAULT_OUTPUT = Path("/datasets/arxiv/arxiv.db")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS papers (
    id               TEXT PRIMARY KEY,
    oai_datestamp    TEXT NOT NULL,
    title            TEXT NOT NULL,
    abstract         TEXT NOT NULL,
    authors          TEXT NOT NULL DEFAULT '',
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

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_papers_download_status ON papers(download_status);
CREATE INDEX IF NOT EXISTS idx_papers_primary_cat     ON papers(primary_category);
CREATE INDEX IF NOT EXISTS idx_papers_submitted       ON papers(submitted_date);
CREATE INDEX IF NOT EXISTS idx_paper_authors_author   ON paper_authors(author_id);
"""


def _has_authors_col(conn: sqlite3.Connection) -> bool:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(papers)").fetchall()}
    return "authors" in cols


def _parent_of(primary_category: str) -> str:
    """Home-shard parent of a primary category (legacy no-dot codes stand alone)."""
    return primary_category.split(".")[0] if "." in primary_category else primary_category


def _dst_parent_counts(conn: sqlite3.Connection) -> dict:
    """Count papers already in the output, grouped by home-shard parent."""
    counts: dict = {}
    for pc, n in conn.execute(
        "SELECT primary_category, COUNT(*) FROM papers GROUP BY primary_category"
    ):
        counts[_parent_of(pc)] = counts.get(_parent_of(pc), 0) + n
    return counts


def combine(shards_dir: Path, output: Path, resume: bool = False) -> None:
    """Merge every shard into a single monolith at output."""
    shard_paths = sorted(shards_dir.glob("*.db"))
    if not shard_paths:
        print(f"No shard DBs found in {shards_dir}", file=sys.stderr)
        sys.exit(1)

    verb = "resuming" if resume else "building"
    print(f"Found {len(shard_paths)} shards — {verb} {output}", file=sys.stderr)

    dst = sqlite3.connect(output)
    dst.executescript(SCHEMA)
    dst.commit()

    # On --resume, learn which parents are already fully merged so we can skip
    # their (idempotent but expensive) rescan.
    already = _dst_parent_counts(dst) if resume else {}

    total_papers = 0
    total_authors = 0

    for shard_path in shard_paths:
        parent = shard_path.stem
        src = sqlite3.connect(f"file:{shard_path}?mode=ro", uri=True)
        src.row_factory = sqlite3.Row

        shard_total = src.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        if shard_total == 0:
            src.close()
            print(f"  {parent:12s}: 0 papers — skipped", file=sys.stderr)
            continue

        if resume and already.get(parent, 0) >= shard_total:
            src.close()
            print(
                f"  {parent:12s}: {already[parent]:>10,} already merged — skipped",
                file=sys.stderr,
            )
            continue

        papers = src.execute("SELECT * FROM papers").fetchall()
        has_authors = _has_authors_col(src)

        inserted = 0
        skipped = 0
        new_authors = 0

        for paper in papers:
            p = dict(paper)

            # Copy paper row; INSERT OR IGNORE skips exact-id duplicates.
            authors_val = p.get("authors", "") if has_authors else ""
            res = dst.execute(
                "INSERT OR IGNORE INTO papers "
                "(id, oai_datestamp, title, abstract, authors, categories, "
                "primary_category, submitted_date, updated_date, doi, "
                "journal_ref, comments, html_content, download_status, downloaded_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    p["id"], p["oai_datestamp"], p["title"], p["abstract"],
                    authors_val, p["categories"], p["primary_category"],
                    p["submitted_date"], p.get("updated_date"),
                    p.get("doi"), p.get("journal_ref"), p.get("comments"),
                    p.get("html_content"), p.get("download_status"),
                    p.get("downloaded_at"),
                ),
            )
            if res.rowcount == 0:
                skipped += 1
                continue
            inserted += 1

            # Fetch authors for this paper from the source shard.
            author_rows = src.execute(
                "SELECT a.*, pa.position "
                "FROM authors a "
                "JOIN paper_authors pa ON pa.author_id = a.id "
                "WHERE pa.paper_id = ? ORDER BY pa.position",
                (p["id"],),
            ).fetchall()

            for a in author_rows:
                # Upsert author by unique key; get (or create) the dst ID.
                dst.execute(
                    "INSERT OR IGNORE INTO authors "
                    "(keyname, forenames, display_name, affiliation) "
                    "VALUES (?,?,?,?)",
                    (a["keyname"], a["forenames"], a["display_name"], a["affiliation"]),
                )
                if dst.execute("SELECT changes()").fetchone()[0]:
                    new_authors += 1
                row = dst.execute(
                    "SELECT id FROM authors "
                    "WHERE keyname=? AND forenames=? AND affiliation IS ?",
                    (a["keyname"], a["forenames"], a["affiliation"]),
                ).fetchone()
                dst.execute(
                    "INSERT OR IGNORE INTO paper_authors (paper_id, author_id, position) "
                    "VALUES (?,?,?)",
                    (p["id"], row[0], a["position"]),
                )

        dst.commit()
        src.close()
        total_papers += inserted
        total_authors += new_authors
        print(
            f"  {parent:12s}: +{inserted:>7,} papers  {skipped:>5,} skipped  "
            f"+{new_authors:>7,} new authors",
            file=sys.stderr,
        )

    print(f"\nBuilding indexes...", file=sys.stderr)
    dst.executescript(INDEXES)
    dst.commit()
    # Fold the WAL back into the main DB so the output is a single tidy file.
    dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    dst.close()

    size_gb = output.stat().st_size / 1_073_741_824
    print(
        f"\nDone. {total_papers:,} papers  {total_authors:,} new authors  "
        f"{size_gb:.1f} GB → {output}",
        file=sys.stderr,
    )


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--shards-dir",
        type=Path,
        default=DEFAULT_SHARDS_DIR,
        help=f"Directory of shard DBs (default: {DEFAULT_SHARDS_DIR})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output monolith path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse an existing output and skip shards already fully merged.",
    )
    args = parser.parse_args()

    if not args.shards_dir.is_dir():
        print(f"Shards directory not found: {args.shards_dir}", file=sys.stderr)
        sys.exit(1)

    if args.output.exists() and not args.resume:
        print(
            f"Output already exists: {args.output} — delete it first, "
            f"or pass --resume to continue.",
            file=sys.stderr,
        )
        sys.exit(1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    combine(args.shards_dir, args.output, resume=args.resume)


if __name__ == "__main__":
    main()
