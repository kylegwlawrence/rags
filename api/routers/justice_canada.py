import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api.models import CanadianLaw, CanadianLawDetail, Page

router = APIRouter(prefix="/justice_canada", tags=["justice_canada"])

# Base SELECT for list rows (no body column — kept out of list queries for speed).
_ACT_COLS = """\
    SELECT
        chapter_number     AS id,
        'act'              AS type,
        COALESCE(NULLIF(short_title,''), long_title, chapter_number) AS title,
        short_title, long_title, running_head,
        bill_origin, bill_type, in_force,
        NULL               AS regulation_type,
        NULL               AS enabling_authority,
        inforce_start_date, last_amended_date, current_date,
        length(body)       AS body_chars
    FROM acts"""

_REG_COLS = """\
    SELECT
        instrument_number  AS id,
        'regulation'       AS type,
        COALESCE(NULLIF(short_title,''), long_title, instrument_number) AS title,
        short_title, long_title,
        NULL               AS running_head,
        NULL               AS bill_origin,
        NULL               AS bill_type,
        NULL               AS in_force,
        regulation_type, enabling_authority,
        inforce_start_date, last_amended_date, current_date,
        length(body)       AS body_chars
    FROM regulations"""

# Same shapes but with body for detail endpoints.
_ACT_COLS_DETAIL = _ACT_COLS.replace("length(body)       AS body_chars", "length(body) AS body_chars, body")
_REG_COLS_DETAIL = _REG_COLS.replace("length(body)       AS body_chars", "length(body) AS body_chars, body")


def _row_to_law(row: sqlite3.Row) -> CanadianLaw:
    return CanadianLaw(
        id=row["id"],
        type=row["type"],
        title=row["title"] or row["id"],
        short_title=row["short_title"] or None,
        long_title=row["long_title"] or None,
        running_head=row["running_head"] or None,
        bill_origin=row["bill_origin"] or None,
        bill_type=row["bill_type"] or None,
        in_force=row["in_force"] or None,
        regulation_type=row["regulation_type"] or None,
        enabling_authority=row["enabling_authority"] or None,
        inforce_start_date=row["inforce_start_date"] or None,
        last_amended_date=row["last_amended_date"] or None,
        current_date=row["current_date"] or None,
        body_chars=row["body_chars"],
    )


@router.get("/laws", response_model=Page[CanadianLaw])
def list_laws(
    type: str | None = Query(None, description="'acts' or 'regulations' (default: both)."),
    in_force: str | None = Query(None, description="Filter acts by in-force status: 'yes'."),
    regulation_type: str | None = Query(None, description="Filter regulations by type: SOR, SI."),
    sort: str | None = Query(None, description="'oldest' or default newest-first."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.justice_canada),
) -> Page[CanadianLaw]:
    """List consolidated Canadian acts and regulations."""
    act_clauses: list[str] = []
    act_params: list = []
    reg_clauses: list[str] = []
    reg_params: list = []

    if in_force is not None:
        act_clauses.append("in_force = ?")
        act_params.append(in_force)
    if regulation_type is not None:
        reg_clauses.append("regulation_type = ?")
        reg_params.append(regulation_type)

    act_where = ("WHERE " + " AND ".join(act_clauses)) if act_clauses else ""
    reg_where = ("WHERE " + " AND ".join(reg_clauses)) if reg_clauses else ""

    include_acts = type in (None, "acts")
    include_regs = type in (None, "regulations")
    order = "last_amended_date ASC, id ASC" if sort == "oldest" else "last_amended_date DESC, id ASC"

    if include_acts and include_regs:
        params = act_params + reg_params
        union = f"{_ACT_COLS} {act_where} UNION ALL {_REG_COLS} {reg_where}"
        count_sql = f"SELECT COUNT(*) FROM ({union})"
        data_sql  = f"SELECT * FROM ({union}) ORDER BY {order} LIMIT ? OFFSET ?"
    elif include_acts:
        params = act_params
        count_sql = f"SELECT COUNT(*) FROM acts {act_where}"
        data_sql  = f"{_ACT_COLS} {act_where} ORDER BY {order} LIMIT ? OFFSET ?"
    else:
        params = reg_params
        count_sql = f"SELECT COUNT(*) FROM regulations {reg_where}"
        data_sql  = f"{_REG_COLS} {reg_where} ORDER BY {order} LIMIT ? OFFSET ?"

    total = conn.execute(count_sql, params).fetchone()[0]
    rows  = conn.execute(data_sql, params + [limit, offset]).fetchall()

    return Page[CanadianLaw](
        items=[_row_to_law(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# /content must be registered before /{law_id:path} or it will never match.
@router.get("/laws/{law_id:path}/content")
def get_law_content(
    law_id: str,
    conn: sqlite3.Connection = Depends(db.justice_canada),
) -> Response:
    """Return the law's body as Markdown plain text."""
    row = (
        conn.execute("SELECT body FROM acts WHERE chapter_number = ?", [law_id]).fetchone()
        or conn.execute("SELECT body FROM regulations WHERE instrument_number = ?", [law_id]).fetchone()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"law {law_id!r} not found")
    body = row["body"] or ""
    if not body:
        raise HTTPException(status_code=404, detail="law has no body text")
    return Response(content=body, media_type="text/plain; charset=utf-8")


@router.get("/laws/{law_id:path}", response_model=CanadianLawDetail)
def get_law(
    law_id: str,
    conn: sqlite3.Connection = Depends(db.justice_canada),
) -> CanadianLawDetail:
    """Return one consolidated act or regulation with full metadata and body."""
    row = (
        conn.execute(f"{_ACT_COLS_DETAIL} WHERE chapter_number = ?", [law_id]).fetchone()
        or conn.execute(f"{_REG_COLS_DETAIL} WHERE instrument_number = ?", [law_id]).fetchone()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"law {law_id!r} not found")
    base = _row_to_law(row)
    return CanadianLawDetail(**base.model_dump(), body=row["body"] or None)
