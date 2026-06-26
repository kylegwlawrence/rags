"""Schema + read/write helpers for the newsletter's own ``newsletter.db``.

The DB is self-sufficient: ``paper_summaries`` copies each title in so an issue
can be rendered without ever touching arxiv.db. There is no separate state
table — the presence of a row is the unit of progress, which makes every step
resumable.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

# Map-step output (one row per paper per run) and reduce-step output (one row
# per day's issue). See docs/plans/arxiv_newsletter_plan.md for the rationale.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_summaries (
    paper_id    TEXT NOT NULL,
    run_date    TEXT NOT NULL,
    title       TEXT NOT NULL,
    summary     TEXT NOT NULL,
    model       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (paper_id, run_date)
);

CREATE TABLE IF NOT EXISTS issues (
    run_date      TEXT PRIMARY KEY,
    generated_at  TEXT NOT NULL,
    paper_count   INTEGER NOT NULL,
    skipped_count INTEGER NOT NULL,
    intro         TEXT NOT NULL,
    body_md       TEXT NOT NULL,
    model         TEXT NOT NULL,
    status        TEXT NOT NULL
);
"""


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string (seconds precision)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect_rw(path: str) -> sqlite3.Connection:
    """Open (creating if needed) the newsletter DB read/write and ensure schema."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def connect_ro(path: str) -> sqlite3.Connection:
    """Open the newsletter DB read-only (raises if the file is missing)."""
    conn = sqlite3.connect(
        f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def existing_summary_ids(conn: sqlite3.Connection, run_date: str) -> set[str]:
    """Paper ids already summarized for ``run_date`` (for resumable maps)."""
    rows = conn.execute(
        "SELECT paper_id FROM paper_summaries WHERE run_date = ?",
        [run_date],
    ).fetchall()
    return {row["paper_id"] for row in rows}


def insert_summary(
    conn: sqlite3.Connection,
    *,
    paper_id: str,
    run_date: str,
    title: str,
    summary: str,
    model: str,
) -> None:
    """Upsert one paper summary; commits immediately for resumability."""
    conn.execute(
        "INSERT OR REPLACE INTO paper_summaries "
        "(paper_id, run_date, title, summary, model, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [paper_id, run_date, title, summary, model, utc_now_iso()],
    )
    conn.commit()


def load_summaries(conn: sqlite3.Connection, run_date: str) -> list[sqlite3.Row]:
    """All summaries for ``run_date``, ordered by paper id."""
    return conn.execute(
        "SELECT paper_id, title, summary FROM paper_summaries "
        "WHERE run_date = ? ORDER BY paper_id",
        [run_date],
    ).fetchall()


def upsert_issue(
    conn: sqlite3.Connection,
    *,
    run_date: str,
    paper_count: int,
    skipped_count: int,
    intro: str,
    body_md: str,
    model: str,
    status: str,
) -> None:
    """Insert or replace the issue row for ``run_date``."""
    conn.execute(
        "INSERT OR REPLACE INTO issues "
        "(run_date, generated_at, paper_count, skipped_count, intro, "
        " body_md, model, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [run_date, utc_now_iso(), paper_count, skipped_count, intro,
         body_md, model, status],
    )
    conn.commit()
