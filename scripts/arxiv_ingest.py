#!/usr/bin/env python3
"""CLI: harvest arxiv metadata via OAI-PMH and write to ``data/arxiv/arxiv.db``.

Usage::

    python scripts/arxiv_ingest.py [--from YYYY-MM-DD] [--until YYYY-MM-DD]
                                    [--db PATH] [--cache-dir PATH]
                                    [--from-cache] [--reset]

Default ``--from`` is ``ingest_state.last_harvested_date`` if set, else
``2021-01-01``. ``--from-cache`` re-parses every XML file in the OAI cache
without hitting the network. ``--reset`` deletes all rows from ``papers``,
``authors``, ``paper_authors``, ``ingest_state`` before harvesting (schema
preserved).

The schema is created on connect if absent. Subsequent runs are idempotent:
papers whose ``oai_datestamp`` is unchanged are skipped; papers whose
``oai_datestamp`` advanced have their ``paper_authors`` rows replaced and
new ``authors`` rows are added monotonically (the ``authors`` table is
content-keyed by ``UNIQUE(keyname, forenames, affiliation)``).

Restart uvicorn after this runs so the cached connection in ``api/db.py``
reopens against the new file.
"""

import argparse
import sqlite3
import sys
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import arxiv_oai  # noqa: E402

DEFAULT_FROM = "2021-01-01"
DEFAULT_DB = REPO_ROOT / "data" / "arxiv" / "arxiv.db"
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "arxiv" / "arxiv_oai_cache"
BATCH_SIZE = 1000

# Columns written by the OAI harvest path. html_content / download_status /
# downloaded_at are owned by scripts/arxiv_download.py and intentionally
# omitted here so a re-harvest of metadata doesn't clobber existing HTML.
_PAPER_COLS = (
    "oai_datestamp",
    "title",
    "abstract",
    "categories",
    "primary_category",
    "submitted_date",
    "updated_date",
    "doi",
    "journal_ref",
    "comments",
)


def create_schema(conn: sqlite3.Connection) -> None:
    """Create papers + authors + paper_authors + ingest_state tables. Idempotent."""
    conn.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS papers (
            id               TEXT PRIMARY KEY,
            oai_datestamp    TEXT NOT NULL,
            title            TEXT NOT NULL,
            abstract         TEXT NOT NULL,
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

        CREATE INDEX IF NOT EXISTS idx_papers_submitted   ON papers(submitted_date);
        CREATE INDEX IF NOT EXISTS idx_papers_primary_cat ON papers(primary_category);

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

        CREATE INDEX IF NOT EXISTS idx_paper_authors_author ON paper_authors(author_id);

        CREATE TABLE IF NOT EXISTS ingest_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.commit()


def connect(path: Path) -> sqlite3.Connection:
    """Open the arxiv DB read-write, ensure schema exists, return the connection."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    return conn


def reset_data(conn: sqlite3.Connection) -> None:
    """Delete all rows from papers, authors, paper_authors, ingest_state. Schema preserved.

    Also resets the AUTOINCREMENT counter for ``authors`` so subsequent
    inserts start at id=1 again instead of continuing from whatever the
    counter reached pre-reset. ``sqlite_sequence`` is created lazily by
    SQLite the first time AUTOINCREMENT fires, so we check for its presence
    before issuing the DELETE.
    """
    conn.executescript(
        "DELETE FROM paper_authors;"
        " DELETE FROM authors;"
        " DELETE FROM papers;"
        " DELETE FROM ingest_state;"
    )
    has_sequence = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
    ).fetchone()
    if has_sequence is not None:
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'authors'")
    conn.commit()


def upsert_paper(conn: sqlite3.Connection, record: dict[str, Any]) -> str:
    """Insert / update one paper + its normalized authors.

    Returns one of ``'inserted'`` / ``'updated'`` / ``'skipped'`` depending on
    whether the paper was new, present with an older ``oai_datestamp``, or
    already up to date.
    """
    paper_id = record["id"]

    existing = conn.execute(
        "SELECT oai_datestamp FROM papers WHERE id = ?", (paper_id,)
    ).fetchone()
    if existing is not None and existing["oai_datestamp"] == record["oai_datestamp"]:
        return "skipped"

    values = tuple(record[col] for col in _PAPER_COLS)
    if existing is None:
        cols = ", ".join(("id",) + _PAPER_COLS)
        placeholders = ", ".join("?" * (1 + len(_PAPER_COLS)))
        conn.execute(
            f"INSERT INTO papers ({cols}) VALUES ({placeholders})",
            (paper_id, *values),
        )
        action = "inserted"
    else:
        set_clause = ", ".join(f"{c} = ?" for c in _PAPER_COLS)
        conn.execute(
            f"UPDATE papers SET {set_clause} WHERE id = ?",
            (*values, paper_id),
        )
        action = "updated"

    # Always rebuild paper_authors on an insert OR update. Old authors rows
    # in the `authors` table itself are left in place (they may still be
    # referenced by other papers, or by an earlier version of this paper if
    # the metadata advances again).
    conn.execute("DELETE FROM paper_authors WHERE paper_id = ?", (paper_id,))
    for position, author in enumerate(record["authors"]):
        author_id = _get_or_create_author(conn, author)
        conn.execute(
            "INSERT INTO paper_authors (paper_id, author_id, position) VALUES (?, ?, ?)",
            (paper_id, author_id, position),
        )

    return action


def _get_or_create_author(conn: sqlite3.Connection, author: dict[str, Any]) -> int:
    """Return the ``authors.id`` matching ``author``, inserting a new row if absent.

    Match key is ``(keyname, forenames, affiliation)``. Note that SQLite's
    ``UNIQUE`` constraint does NOT treat ``NULL`` as equal to ``NULL`` (per
    the SQL standard), so two authors with the same name and a NULL
    affiliation would not violate ``UNIQUE`` — we always SELECT first with
    ``IS ?`` (null-safe in SQLite) before INSERTing, to avoid creating
    duplicates in that case.

    On a dedup hit, ``display_name`` is updated if it differs from what's
    stored. Suffixes fold into ``display_name`` but are NOT part of the
    dedup key, so the same author can legitimately arrive with a different
    display_name on a later record (e.g. "Alice Smith" then "Alice Smith
    Jr."). The newer string wins.
    """
    row = conn.execute(
        "SELECT id, display_name FROM authors "
        "WHERE keyname = ? AND forenames = ? AND affiliation IS ?",
        (author["keyname"], author["forenames"], author["affiliation"]),
    ).fetchone()
    if row is not None:
        if row["display_name"] != author["display_name"]:
            conn.execute(
                "UPDATE authors SET display_name = ? WHERE id = ?",
                (author["display_name"], row["id"]),
            )
        return row["id"]
    cur = conn.execute(
        "INSERT INTO authors (keyname, forenames, display_name, affiliation) "
        "VALUES (?, ?, ?, ?)",
        (
            author["keyname"],
            author["forenames"],
            author["display_name"],
            author["affiliation"],
        ),
    )
    return cur.lastrowid


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    """Read ``ingest_state[key]``. Returns None if unset."""
    row = conn.execute(
        "SELECT value FROM ingest_state WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert ``ingest_state[key] = value``. Caller commits."""
    conn.execute(
        "INSERT OR REPLACE INTO ingest_state (key, value) VALUES (?, ?)",
        (key, value),
    )


def ingest_records(
    conn: sqlite3.Connection,
    records: Iterable[dict[str, Any]],
    batch_size: int = BATCH_SIZE,
    progress: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """Apply ``upsert_paper`` to each record, committing every ``batch_size`` rows.

    Args:
        conn: Open writer connection (schema already exists).
        records: Iterable of parsed OAI dicts (e.g. ``arxiv_oai.harvest_records(...)``).
        batch_size: Commit boundary.
        progress: Optional callback invoked with a one-line status string at
            each commit boundary. Useful for the CLI; tests pass None.
    """
    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    for i, record in enumerate(records, 1):
        action = upsert_paper(conn, record)
        stats[action] += 1
        if i % batch_size == 0:
            conn.commit()
            if progress is not None:
                progress(
                    f"  {i} seen / {stats['inserted']} inserted / "
                    f"{stats['updated']} updated / {stats['skipped']} skipped"
                )
    conn.commit()
    return stats


def _print_stderr(line: str) -> None:
    print(line, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from",
        dest="from_date",
        default=None,
        help=(
            "ISO date lower bound. Default: ingest_state.last_harvested_date if "
            f"set, else {DEFAULT_FROM}."
        ),
    )
    parser.add_argument(
        "--until",
        dest="until_date",
        default=None,
        help="ISO date upper bound (inclusive).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Path to arxiv.db (default: {DEFAULT_DB.relative_to(REPO_ROOT)}).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"OAI cache directory (default: {DEFAULT_CACHE_DIR.relative_to(REPO_ROOT)}).",
    )
    parser.add_argument(
        "--from-cache",
        action="store_true",
        help="Re-parse cached XML in --cache-dir; do not hit the network.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete all rows in papers / authors / paper_authors / ingest_state before harvesting.",
    )
    args = parser.parse_args(argv)

    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(args.db)

    if args.reset:
        _print_stderr("Resetting papers / authors / paper_authors / ingest_state...")
        reset_data(conn)

    if args.from_cache:
        if not args.cache_dir.exists():
            _print_stderr(f"Cache dir not found: {args.cache_dir}")
            conn.close()
            return 1
        records: Iterable[dict[str, Any]] = arxiv_oai.iter_cached_records(args.cache_dir)
        source = f"cache ({args.cache_dir})"
    else:
        from_date = (
            args.from_date
            or get_state(conn, "last_harvested_date")
            or DEFAULT_FROM
        )
        suffix = f" until={args.until_date}" if args.until_date else ""
        source = f"OAI-PMH from={from_date}{suffix}"
        records = arxiv_oai.harvest_records(
            from_date=from_date,
            until_date=args.until_date,
            cache_dir=args.cache_dir,
        )

    _print_stderr(f"Ingesting from {source}...")
    stats = ingest_records(conn, records, progress=_print_stderr)

    # Watermark advances only on network harvests — replaying cache should
    # not move the "last harvested" pointer, since the cache may predate any
    # number of incremental edits upstream.
    if not args.from_cache:
        cutoff = args.until_date or datetime.now(timezone.utc).date().isoformat()
        set_state(conn, "last_harvested_date", cutoff)
        conn.commit()

    conn.close()
    _print_stderr(
        f"Done. inserted={stats['inserted']} updated={stats['updated']} "
        f"skipped={stats['skipped']}"
    )
    _print_stderr("(Restart uvicorn so the cached connection picks up the new file.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
