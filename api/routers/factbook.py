import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api import db
from api._chunks import add_chunks_route
from api.models import CountryDetail, CountrySummary, Page

router = APIRouter(prefix="/factbook", tags=["factbook"])


def _flatten(obj: Any) -> Any:
    """Recursively simplify the CIA Factbook {"text": "..."} wrapper pattern.

    - {"text": v} (only key)  → v
    - {"text": v, "note": …}  → {"value": v, "note": …}  (siblings preserved)
    - everything else walks recursively unchanged
    """
    if isinstance(obj, dict):
        if "text" in obj:
            siblings = {k: _flatten(v) for k, v in obj.items() if k != "text"}
            text_val = _flatten(obj["text"])
            if not siblings:
                return text_val
            return {"value": text_val, **siblings}
        return {k: _flatten(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_flatten(v) for v in obj]
    return obj


@router.get("/countries", response_model=Page[CountrySummary])
def list_countries(
    region: str | None = Query(None, description="Exact region match"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.factbook),
) -> Page[CountrySummary]:
    """List countries (slim, no JSON blob). Filter by exact region; paginate."""
    where = "WHERE region = ?" if region is not None else ""
    params: list = [region] if region is not None else []
    total = conn.execute(f"SELECT COUNT(*) FROM countries {where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT id, name, region FROM countries {where} ORDER BY id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    items = [CountrySummary(id=r["id"], name=r["name"], region=r["region"]) for r in rows]
    return Page[CountrySummary](items=items, total=total, limit=limit, offset=offset)


@router.get("/countries/{country_id}", response_model=CountryDetail)
def get_country(
    country_id: str,
    conn: sqlite3.Connection = Depends(db.factbook),
) -> CountryDetail:
    """Return one country including the parsed factbook JSON blob."""
    row = conn.execute(
        "SELECT id, name, region, data FROM countries WHERE id = ?",
        [country_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"country {country_id!r} not found")
    return CountryDetail(
        id=row["id"],
        name=row["name"],
        region=row["region"],
        data=_flatten(json.loads(row["data"])) if row["data"] else None,
    )


add_chunks_route(
    router,
    opener=db.factbook_rag,
    source_name="factbook",
    indexer_script="factbook_index_rag.py",
)
