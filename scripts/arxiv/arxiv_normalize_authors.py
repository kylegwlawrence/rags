#!/usr/bin/env python3
"""One-shot backfill of normalized author tables from the legacy JSON column.

Existing ``data/arxiv/arxiv.db`` rows (copied from ``local_wikipedia``) carry
``papers.authors`` as a JSON array of ``"forenames keyname"`` strings — the
upstream OAI parser collapses ``<keyname>`` / ``<forenames>`` into one string
before encoding. The Phase 3 ingest pipeline writes structured author rows
into ``authors`` / ``paper_authors`` instead; this script populates those
tables from the legacy JSON column so the rewired ``api/routers/arxiv.py``
works against existing data without waiting for the full re-harvest.

Once the full re-harvest runs ``scripts/arxiv/arxiv_ingest.py`` against a fresh
DB, this script becomes obsolete (the new ingest writes structured authors
directly). The heuristic name split here can't recover proper ``<keyname>``
/ ``<forenames>`` separation from a collapsed string — that requires
re-harvest. ``affiliation`` is always NULL in the backfill output (the
legacy JSON didn't carry it).

Idempotent: clears ``paper_authors`` first; ``authors`` grows monotonically
(re-inserts hit the SELECT-first dedup in ``_get_or_create_author``).
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import arxiv_ingest  # noqa: E402

DEFAULT_DB = REPO_ROOT / "data" / "arxiv" / "arxiv.db"


def split_name(name: str) -> tuple[str, str]:
    """Heuristically split ``"Forenames Keyname"`` into ``(keyname, forenames)``.

    The legacy OAI parser stored author names as ``f"{forenames} {keyname}"``,
    so the last whitespace-delimited token is (heuristically) the surname.
    Mononym names ("Plato") become ``(keyname="Plato", forenames="")``. Names
    with multi-word surnames ("van der Berg") get the wrong split here —
    they'll be re-parsed correctly when the full OAI re-harvest runs.

    Whitespace runs are collapsed: ``"  Alice   Smith  "`` -> ``("Smith", "Alice")``.
    """
    tokens = name.split()
    if not tokens:
        return ("", "")
    if len(tokens) == 1:
        return (tokens[0], "")
    return (tokens[-1], " ".join(tokens[:-1]))


def author_dict_from_legacy(name: str) -> dict[str, str | None] | None:
    """Turn one legacy collapsed-name string into a structured author dict.

    Returns the dict that ``arxiv_ingest._get_or_create_author`` expects, or
    ``None`` if the input is empty / whitespace-only. ``display_name`` is
    whitespace-normalised to a single-space form so it matches the value
    produced by ``" ".join((forenames, keyname))`` from the cleaned tokens —
    avoids storing internal double-spaces from messy upstream data.
    """
    keyname, forenames = split_name(name)
    if not keyname and not forenames:
        return None
    display_name = " ".join(name.split())
    return {
        "keyname": keyname,
        "forenames": forenames,
        "affiliation": None,
        "display_name": display_name,
    }


def _has_legacy_authors_column(conn: sqlite3.Connection) -> bool:
    """Re-exported from ``arxiv_ingest`` so the test suite has a single import."""
    return arxiv_ingest._has_legacy_authors_column(conn)


def backfill(
    conn: sqlite3.Connection,
    batch_size: int = 1000,
) -> dict[str, int]:
    """Walk every row in ``papers``, build ``authors`` + ``paper_authors``.

    For each paper, clears any existing ``paper_authors`` rows for that
    paper_id first, then parses ``papers.authors`` JSON and re-populates.
    Re-runnable: existing ``authors`` rows are reused via the dedup path in
    ``arxiv_ingest._get_or_create_author``.

    Returns a stats dict with counts: ``papers``, ``links``, ``empty``,
    ``malformed_json``.
    """
    stats = {"papers": 0, "links": 0, "empty": 0, "malformed_json": 0}
    cursor = conn.execute("SELECT id, authors FROM papers ORDER BY id")
    for paper_id, authors_json in cursor:
        stats["papers"] += 1
        if not authors_json:
            stats["empty"] += 1
            conn.execute(
                "DELETE FROM paper_authors WHERE paper_id = ?", (paper_id,)
            )
            continue
        try:
            names = json.loads(authors_json)
        except json.JSONDecodeError:
            stats["malformed_json"] += 1
            continue
        conn.execute("DELETE FROM paper_authors WHERE paper_id = ?", (paper_id,))
        if not isinstance(names, list):
            # "null", {}, scalars, etc. — treat as no authors.
            continue
        for position, name in enumerate(names):
            if not isinstance(name, str):
                continue
            author = author_dict_from_legacy(name)
            if author is None:
                continue
            author_id = arxiv_ingest._get_or_create_author(conn, author)
            conn.execute(
                "INSERT INTO paper_authors (paper_id, author_id, position) "
                "VALUES (?, ?, ?)",
                (paper_id, author_id, position),
            )
            stats["links"] += 1
        if stats["papers"] % batch_size == 0:
            conn.commit()
    conn.commit()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Path to arxiv.db (default: {DEFAULT_DB.relative_to(REPO_ROOT)}).",
    )
    args = parser.parse_args(argv)

    if not args.db.is_file():
        print(f"missing DB: {args.db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    # Ensure authors + paper_authors exist before the backfill writes to them.
    arxiv_ingest.create_schema(conn)

    if not _has_legacy_authors_column(conn):
        print(
            "No legacy `papers.authors` column found — this DB is already on "
            "the structured-author schema (created by "
            "`scripts/arxiv/arxiv_ingest.py`). Nothing to backfill.",
            file=sys.stderr,
        )
        conn.close()
        return 0

    t0 = time.time()
    stats = backfill(conn)
    elapsed = time.time() - t0

    n_authors = conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
    conn.close()
    print(
        f"Done in {elapsed:.1f}s. "
        f"{stats['papers']} papers -> {stats['links']} paper_authors links "
        f"({n_authors} unique authors). "
        f"empty_authors={stats['empty']} malformed_json={stats['malformed_json']}."
    )
    print("(Restart uvicorn so the cached connection picks up the new tables.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
