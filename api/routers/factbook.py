import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api import db
from api.models import Chunk, ChunksResponse, CountryDetail, CountrySummary, Page
from rag import retriever
from rag.retriever import is_operational_error

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
            text_val = obj["text"]
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


@router.get("/chunks", response_model=ChunksResponse)
def search_chunks(
    q: str = Query(..., description="Natural-language query. Empty → 400."),
    top_k: int = Query(20, ge=1, le=100),
    candidate_k: int = Query(50, ge=10, le=200),
    rag_conn: sqlite3.Connection = Depends(db.factbook_rag),
) -> ChunksResponse:
    """Hybrid (FTS5 + sqlite-vec) retrieval over factbook chunks.

    Each country's JSON is rendered as section-tagged markdown so chunks
    carry their section name (Geography, Economy, etc.). `doc_id` is the
    factbook country code (e.g. `us`, `af`).

    Errors: 400 on empty `q`; 503 when `factbook_rag.db` or its `chunks_fts` /
    `chunks_vec` tables are missing (run `scripts/factbook_index_rag.py`).
    """
    if not q.strip():
        raise HTTPException(status_code=400, detail="q must not be empty")
    try:
        result = retriever.retrieve(q, rag_conn, top_k=top_k, candidate_k=candidate_k)
    except sqlite3.OperationalError as e:
        if is_operational_error(e):
            raise HTTPException(
                status_code=503,
                detail=(
                    f"factbook RAG data not ready ({e}). "
                    "Run scripts/factbook_index_rag.py or restore data/factbook/factbook_rag.db."
                ),
            ) from e
        raise HTTPException(status_code=400, detail=f"bad query: {e}") from e

    items = [
        Chunk(
            chunk_id=h.chunk_id,
            doc_id=h.doc_id,
            title=h.title,
            section=h.section,
            chunk_index=h.chunk_index,
            text=h.text,
            text_length=h.text_length,
            score=h.score,
        )
        for h in result.hits
    ]
    return ChunksResponse(
        items=items,
        used_dense=result.used_dense,
        top_k=top_k,
        candidate_k=candidate_k,
    )
