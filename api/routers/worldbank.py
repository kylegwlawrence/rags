import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from api import db
from api.models import Page, WBCountry, WBDataPoint, WBIndicator, WBObservation

router = APIRouter(prefix="/worldbank", tags=["worldbank"])


def _get_indicator_row(conn: sqlite3.Connection, indicator_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, name, unit, source_note, source_org FROM indicators WHERE id = ?",
        [indicator_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"indicator {indicator_id!r} not found")
    return row


def _topics_for(conn: sqlite3.Connection, indicator_id: str) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            """SELECT t.name FROM topics t
               JOIN indicator_topics it ON it.topic_id = t.id
               WHERE it.indicator_id = ?
               ORDER BY t.id""",
            [indicator_id],
        ).fetchall()
    ]


def _row_to_indicator(conn: sqlite3.Connection, row: sqlite3.Row) -> WBIndicator:
    return WBIndicator(
        id=row["id"],
        name=row["name"],
        unit=row["unit"],
        source_note=row["source_note"],
        source_org=row["source_org"],
        topics=_topics_for(conn, row["id"]),
    )


@router.get("/indicators", response_model=Page[WBIndicator])
def list_indicators(
    q: str | None = Query(
        None,
        description="Keyword search on indicator name (case-insensitive substring match).",
    ),
    topic: int | None = Query(None, description="Filter by topic ID (1–21)."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.worldbank),
) -> Page[WBIndicator]:
    """List indicators, optionally filtered by topic ID and/or name keyword."""
    clauses: list[str] = []
    params: list = []

    if q is not None:
        clauses.append("i.name LIKE ?")
        params.append(f"%{q}%")
    if topic is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM indicator_topics it"
            " WHERE it.indicator_id = i.id AND it.topic_id = ?)"
        )
        params.append(topic)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM indicators i {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT id, name, unit, source_note, source_org"
        f" FROM indicators i {where} ORDER BY i.id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()

    return Page[WBIndicator](
        items=[_row_to_indicator(conn, r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/indicators/{indicator_id}/values", response_model=Page[WBObservation])
def get_indicator_values(
    indicator_id: str,
    country: str | None = Query(
        None, description="ISO2 country code to restrict results to one economy."
    ),
    year: int | None = Query(None, description="Filter to a specific year."),
    limit: int = Query(200, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.worldbank),
) -> Page[WBObservation]:
    """Return observations for one indicator across all economies and years."""
    _get_indicator_row(conn, indicator_id)

    clauses = ["o.indicator_id = ?"]
    params: list = [indicator_id]

    if country is not None:
        clauses.append("o.country_id = ?")
        params.append(country.upper())
    if year is not None:
        clauses.append("o.year = ?")
        params.append(year)

    where = "WHERE " + " AND ".join(clauses)

    total = conn.execute(
        f"SELECT COUNT(*) FROM observations o {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"""SELECT o.country_id, c.name AS country_name, o.year, o.value
            FROM observations o LEFT JOIN countries c ON c.id = o.country_id
            {where}
            ORDER BY o.country_id, o.year DESC
            LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()

    return Page[WBObservation](
        items=[
            WBObservation(
                country_id=r["country_id"],
                country_name=r["country_name"],
                year=r["year"],
                value=r["value"],
            )
            for r in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/indicators/{indicator_id}", response_model=WBIndicator)
def get_indicator(
    indicator_id: str,
    conn: sqlite3.Connection = Depends(db.worldbank),
) -> WBIndicator:
    """Return metadata for one indicator by its World Bank ID (e.g. NY.GDP.MKTP.CD)."""
    return _row_to_indicator(conn, _get_indicator_row(conn, indicator_id))


@router.get("/countries", response_model=Page[WBCountry])
def list_countries(
    region: str | None = Query(
        None, description="Substring match on region name (case-insensitive)."
    ),
    income_level: str | None = Query(
        None, description="Substring match on income level (case-insensitive)."
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.worldbank),
) -> Page[WBCountry]:
    """List all economies (countries and regional/income aggregates)."""
    clauses: list[str] = []
    params: list = []

    if region is not None:
        clauses.append("region LIKE ?")
        params.append(f"%{region}%")
    if income_level is not None:
        clauses.append("income_level LIKE ?")
        params.append(f"%{income_level}%")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM countries {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT id, name, region, income_level FROM countries {where}"
        f" ORDER BY name LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()

    return Page[WBCountry](
        items=[
            WBCountry(
                id=r["id"],
                name=r["name"],
                region=r["region"],
                income_level=r["income_level"],
            )
            for r in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/countries/{country_id}/data", response_model=Page[WBDataPoint])
def get_country_data(
    country_id: str,
    topic: int | None = Query(None, description="Filter by topic ID (1–21)."),
    year: int | None = Query(None, description="Filter to a specific year."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.worldbank),
) -> Page[WBDataPoint]:
    """Return all indicator observations for one economy, optionally filtered by topic and year."""
    country_id = country_id.upper()
    if conn.execute(
        "SELECT 1 FROM countries WHERE id = ?", [country_id]
    ).fetchone() is None:
        raise HTTPException(status_code=404, detail=f"country {country_id!r} not found")

    clauses = ["o.country_id = ?"]
    params: list = [country_id]

    if topic is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM indicator_topics it"
            " WHERE it.indicator_id = o.indicator_id AND it.topic_id = ?)"
        )
        params.append(topic)
    if year is not None:
        clauses.append("o.year = ?")
        params.append(year)

    where = "WHERE " + " AND ".join(clauses)

    total = conn.execute(
        f"SELECT COUNT(*) FROM observations o {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"""SELECT o.indicator_id, ind.name AS indicator_name, o.year, o.value
            FROM observations o JOIN indicators ind ON ind.id = o.indicator_id
            {where}
            ORDER BY o.indicator_id, o.year DESC
            LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()

    return Page[WBDataPoint](
        items=[
            WBDataPoint(
                indicator_id=r["indicator_id"],
                indicator_name=r["indicator_name"],
                year=r["year"],
                value=r["value"],
            )
            for r in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
