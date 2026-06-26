"""Read-only access to this repo's arxiv.db for newsletter selection.

Opens the source DB in read-only mode and selects the day's new papers for the
configured primary category, dropping withdrawn/placeholder notes so the LLM
only ever sees real abstracts.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# An abstract shorter than this is treated as a stub (withdrawn note, "no
# abstract" placeholder, etc.) rather than a real paper.
MIN_ABSTRACT_LEN = 150

# Phrases that mark a withdrawn / placeholder submission even when long enough.
_WITHDRAWN_MARKERS = (
    "this paper has been withdrawn",
    "this submission has been withdrawn",
    "paper has been withdrawn",
    "withdrawn by the author",
    "no abstract available",
)


@dataclass(frozen=True)
class Paper:
    """A single source paper: just what the map step needs."""

    id: str          # arxiv id, e.g. 2512.02080
    title: str
    abstract: str


def _connect_ro(path: str) -> sqlite3.Connection:
    """Open ``path`` read-only (never creates or writes the file)."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _is_junk(abstract: str | None) -> bool:
    """Return True for withdrawn/placeholder abstracts to skip."""
    text = (abstract or "").strip()
    if len(text) < MIN_ABSTRACT_LEN:
        return True
    low = text.lower()
    return any(marker in low for marker in _WITHDRAWN_MARKERS)


def select_papers(
    arxiv_db: str, category: str, run_date: str,
) -> tuple[list[Paper], int]:
    """Select the day's papers for ``category``, filtering out junk.

    Args:
        arxiv_db: path to the read-only source DB.
        category: ``primary_category`` to match (e.g. ``cs.AI``).
        run_date: ``oai_datestamp`` the issue covers (``YYYY-MM-DD``).

    Returns:
        ``(papers, skipped_count)`` where ``papers`` are kept, ordered by id,
        and ``skipped_count`` is how many junk rows were dropped.
    """
    conn = _connect_ro(arxiv_db)
    try:
        rows = conn.execute(
            "SELECT id, title, abstract FROM papers "
            "WHERE primary_category = ? AND oai_datestamp = ? "
            "ORDER BY id",
            [category, run_date],
        ).fetchall()
    finally:
        conn.close()

    papers: list[Paper] = []
    skipped = 0
    for row in rows:
        if _is_junk(row["abstract"]):
            skipped += 1
            continue
        papers.append(Paper(
            id=row["id"],
            title=(row["title"] or "").strip(),
            abstract=(row["abstract"] or "").strip(),
        ))
    return papers, skipped
