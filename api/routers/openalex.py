import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from api import db
from api.models import Page, Work

router = APIRouter(prefix="/openalex", tags=["openalex"])

SHORT_ID_RE = re.compile(r"^W\d+$")
OPENALEX_PREFIX = "https://openalex.org/"

SORTS = {
    "cited_by_count_desc": "cited_by_count DESC",
    "year_desc": "year DESC",
    "year_asc": "year ASC",
}


def _row_to_work(row: sqlite3.Row) -> Work:
    """Map a `works` row to its response model, splitting authors and shortening the id."""
    full_id = row["id"]
    short = full_id.rsplit("/", 1)[-1] if full_id else full_id
    authors_raw = row["authors"] or ""
    authors = [a.strip() for a in authors_raw.split(",") if a.strip()]
    return Work(
        id=short,
        openalex_url=full_id,
        title=row["title"],
        abstract=row["abstract"],
        year=row["year"],
        cited_by_count=row["cited_by_count"],
        doi=row["doi"],
        authors=authors,
        venue=row["venue"],
    )


@router.get("/works/{short_id}", response_model=Work)
def get_work(
    short_id: str,
    conn: sqlite3.Connection = Depends(db.openalex),
) -> Work:
    """Return one work by its OpenAlex short id (e.g. `W3038568908`)."""
    if not SHORT_ID_RE.match(short_id):
        raise HTTPException(status_code=400, detail="id must look like W123456")
    full = OPENALEX_PREFIX + short_id
    row = conn.execute(
        "SELECT id, title, abstract, year, cited_by_count, doi, authors, venue "
        "FROM works WHERE id = ?",
        [full],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"work {short_id!r} not found")
    return _row_to_work(row)


@router.get("/works", response_model=Page[Work])
def list_works(
    year: int | None = Query(None),
    cited_by_min: int | None = Query(None, ge=0),
    cited_by_max: int | None = Query(None, ge=0),
    venue: str | None = Query(None, description="Exact venue match"),
    sort: Literal["cited_by_count_desc", "year_desc", "year_asc"] = "cited_by_count_desc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.openalex),
) -> Page[Work]:
    """List works with year / citation-count / venue filters and configurable sort."""
    clauses: list[str] = []
    params: list = []
    if year is not None:
        clauses.append("year = ?")
        params.append(year)
    if cited_by_min is not None:
        clauses.append("cited_by_count >= ?")
        params.append(cited_by_min)
    if cited_by_max is not None:
        clauses.append("cited_by_count <= ?")
        params.append(cited_by_max)
    if venue is not None:
        clauses.append("venue = ?")
        params.append(venue)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = SORTS[sort]

    total = conn.execute(f"SELECT COUNT(*) FROM works {where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT id, title, abstract, year, cited_by_count, doi, authors, venue "
        f"FROM works {where} ORDER BY {order} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return Page[Work](
        items=[_row_to_work(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
