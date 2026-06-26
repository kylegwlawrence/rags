"""Self-contained FastAPI router serving newsletter issues.

Own read-only connection to ``newsletter.db``, own Pydantic models, own 503
when the DB is missing. Imports nothing from this repo's ``api/`` package so it
drops straight into a standalone app later.
"""

from __future__ import annotations

import os
import sqlite3

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from newsletter import store
from newsletter.config import Config

router = APIRouter(prefix="/newsletter", tags=["newsletter"])

_CONFIG = Config.from_env()


class IssueSummary(BaseModel):
    """An issue without its full body (list view)."""

    run_date: str
    generated_at: str
    paper_count: int
    skipped_count: int
    intro: str
    model: str
    status: str


class IssueDetail(IssueSummary):
    """A single issue including the full rendered markdown."""

    body_md: str


class IssuePage(BaseModel):
    items: list[IssueSummary]
    total: int
    limit: int
    offset: int


def _conn() -> sqlite3.Connection:
    """Open the newsletter DB read-only, or 503 if it isn't there yet."""
    if not os.path.exists(_CONFIG.newsletter_db):
        raise HTTPException(
            status_code=503,
            detail=("newsletter.db not found — run "
                    "`python -m newsletter.cli` to generate an issue"),
        )
    try:
        return store.connect_ro(_CONFIG.newsletter_db)
    except sqlite3.Error as e:
        raise HTTPException(
            status_code=503, detail=f"newsletter.db unavailable: {e}")


def _row_to_summary(row: sqlite3.Row) -> IssueSummary:
    return IssueSummary(
        run_date=row["run_date"],
        generated_at=row["generated_at"],
        paper_count=row["paper_count"],
        skipped_count=row["skipped_count"],
        intro=row["intro"],
        model=row["model"],
        status=row["status"],
    )


@router.get("/issues", response_model=IssuePage)
def list_issues(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> IssuePage:
    """List issues, newest first, paginated."""
    conn = _conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
        rows = conn.execute(
            "SELECT run_date, generated_at, paper_count, skipped_count, "
            "       intro, model, status "
            "FROM issues ORDER BY run_date DESC LIMIT ? OFFSET ?",
            [limit, offset],
        ).fetchall()
    finally:
        conn.close()
    return IssuePage(
        items=[_row_to_summary(r) for r in rows],
        total=total, limit=limit, offset=offset)


@router.get("/issues/latest", response_model=IssueDetail)
def latest_issue() -> IssueDetail:
    """Return the most recent issue (by run_date)."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT run_date, generated_at, paper_count, skipped_count, "
            "       intro, body_md, model, status "
            "FROM issues ORDER BY run_date DESC LIMIT 1",
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="no issues yet")
    return IssueDetail(body_md=row["body_md"], **_row_to_summary(row).model_dump())


@router.get("/issues/{run_date}", response_model=IssueDetail)
def get_issue(run_date: str) -> IssueDetail:
    """Return one issue by its ``run_date`` (``YYYY-MM-DD``)."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT run_date, generated_at, paper_count, skipped_count, "
            "       intro, body_md, model, status "
            "FROM issues WHERE run_date = ?",
            [run_date],
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"issue {run_date!r} not found")
    return IssueDetail(body_md=row["body_md"], **_row_to_summary(row).model_dump())
