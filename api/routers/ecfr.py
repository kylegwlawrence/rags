import sqlite3

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._embedded import embedded_clauses
from api._fts import translate_table_errors
from api.models import EcfrRegulation, EmbedResult, Page
from rag.chunker import chunk_doc
from rag.ecfr import build_doc
from rag.embed_one import embed_doc
from rag.profiles import DENSE as _PROFILE

router = APIRouter(prefix="/ecfr", tags=["ecfr"])


def _row_to_reg(row: sqlite3.Row) -> EcfrRegulation:
    return EcfrRegulation(
        id=row["id"],
        title_num=row["title_num"],
        title_name=row["title_name"],
        chapter=row["chapter"],
        part=row["part"],
        section=row["section"],
        heading=row["heading"],
        content_chars=row["content_chars"],
    )


# Columns selected for list/detail rows — everything but the (large) body.
_META_COLS = (
    "id, title_num, title_name, chapter, part, section, heading, "
    "length(content) AS content_chars"
)


@router.get("/regulations", response_model=Page[EcfrRegulation])
def list_regulations(
    q: str | None = Query(
        None,
        description=(
            "FTS5 full-text search over heading + content. Accepts FTS5 syntax: "
            "`\"phrase\"`, `term*`, `a OR b`, `a NOT b`."
        ),
    ),
    title: int | None = Query(
        None,
        description="Filter to a specific CFR title number (1–50).",
    ),
    part: str | None = Query(
        None,
        description="Substring match on the part label (case-insensitive via LIKE).",
    ),
    embedded: bool | None = Query(
        None,
        description=(
            "Filter by RAG embedding state: true = only sections chunked into "
            "ecfr_rag.db, false = only sections not yet embedded. Omit to list "
            "all (the default)."
        ),
    ),
    sort: str | None = Query(
        None,
        description="Sort order: 'document' (default, reading order) or 'relevance' (requires q).",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.ecfr),
) -> Page[EcfrRegulation]:
    """List eCFR sections with optional full-text search and title/part filters."""
    from_clause = "regulations"
    clauses: list[str] = []
    params: list = []

    if q is not None:
        # regulations.id is the INTEGER PK, i.e. the rowid the FTS index keys on.
        from_clause = "regulations JOIN regulations_fts ON regulations_fts.rowid = regulations.id"
        clauses.append("regulations_fts MATCH ?")
        params.append(q)
    if title is not None:
        clauses.append("regulations.title_num = ?")
        params.append(title)
    if part is not None:
        clauses.append("regulations.part LIKE ?")
        params.append(f"%{part}%")
    if embedded is not None:
        # doc_id in the rag DB is str(regulations.id); the main column is the
        # INTEGER id, so map the stored ids back to int before comparing.
        c, p, empty = embedded_clauses(
            db.ecfr_rag,
            embedded=embedded,
            column="regulations.id",
            id_transform=int,
        )
        if empty:
            return Page[EcfrRegulation](items=[], total=0, limit=limit, offset=offset)
        clauses.extend(c)
        params.extend(p)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    if sort == "relevance" and q is None:
        raise HTTPException(status_code=400, detail="sort=relevance requires q")
    if sort == "relevance":
        order = "bm25(regulations_fts) ASC"
    else:
        # Reading order: the rows were inserted title → part → section.
        order = "regulations.id ASC"

    with translate_table_errors(
        "ecfr",
        "ecfr/ecfr_index_fts.py",
        "data/ecfr/ecfr.db",
    ):
        total = conn.execute(
            f"SELECT COUNT(*) FROM {from_clause} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT regulations.id, regulations.title_num, regulations.title_name, "
            f"       regulations.chapter, regulations.part, regulations.section, "
            f"       regulations.heading, length(regulations.content) AS content_chars "
            f"FROM {from_clause} {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    return Page[EcfrRegulation](
        items=[_row_to_reg(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# Content route must come before the detail route — both share the same prefix.
@router.get("/regulations/{reg_id}/content")
def get_regulation_content(
    reg_id: int,
    conn: sqlite3.Connection = Depends(db.ecfr),
) -> Response:
    """Return the regulation body text for one section as text/plain."""
    row = conn.execute(
        "SELECT content FROM regulations WHERE id = ?", [reg_id]
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"regulation {reg_id} not found")
    if not row["content"]:
        raise HTTPException(status_code=404, detail="regulation has no body text")
    return Response(content=row["content"], media_type="text/plain; charset=utf-8")


@router.post("/regulations/{reg_id}/embed", response_model=EmbedResult)
def embed_regulation(
    reg_id: int,
    conn: sqlite3.Connection = Depends(db.ecfr),
) -> EmbedResult:
    """Embed one eCFR section into ecfr_rag.db on demand (synchronous).

    The body is flat legal prose with no `##` headings, so it chunks with
    `chunk_doc` (not `chunk_markdown`) under the DENSE profile (dense
    regulatory text → smaller chunks for finer retrieval grain). The batch
    indexer (`scripts/ecfr/ecfr_index_rag.py`) uses the same builder.

    Replaces any chunks already stored for this section, becoming searchable
    through `/ecfr/chunks` immediately (the RAG DB runs in WAL mode, so the
    cached read-only connection picks up the new rows without a uvicorn
    restart). Returns `embedded=false` when the section has no body text. A 503
    means Ollama was unreachable; any existing chunks are left untouched.
    """
    row = conn.execute(
        "SELECT id, title_num, section, heading, content "
        "FROM regulations WHERE id = ?",
        [reg_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"regulation {reg_id} not found")

    doc = build_doc(row)
    if doc is None:
        heading = (row["heading"] or "").strip()
        title = heading or f"Title {row['title_num']} § {row['section']}"
        return EmbedResult(
            doc_id=str(reg_id), title=title, chunk_count=0, embedded=False
        )

    rag_conn = db.connect_rag_rw(db.ECFR_RAG_DB)
    try:
        chunk_count = embed_doc(
            rag_conn,
            doc,
            chunk_fn=chunk_doc,
            chunk_size=_PROFILE.chunk_size,
            overlap=_PROFILE.overlap,
            max_chunk_size=_PROFILE.max_chunk_size,
        )
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=503,
            detail=f"embedding service (Ollama) unavailable: {e}",
        ) from e
    finally:
        rag_conn.close()

    return EmbedResult(
        doc_id=doc.doc_id,
        title=doc.title,
        chunk_count=chunk_count,
        embedded=chunk_count > 0,
    )


@router.get("/regulations/{reg_id}", response_model=EcfrRegulation)
def get_regulation(
    reg_id: int,
    conn: sqlite3.Connection = Depends(db.ecfr),
) -> EcfrRegulation:
    """Return metadata for one eCFR section by row id."""
    row = conn.execute(
        f"SELECT {_META_COLS} FROM regulations WHERE id = ?", [reg_id]
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"regulation {reg_id} not found")
    return _row_to_reg(row)


add_chunks_route(
    router,
    opener=db.ecfr_rag,
    source_name="ecfr",
    indexer_script="ecfr (embed-only: POST /ecfr/regulations/{id}/embed)",
)
add_doc_chunks_route(
    router,
    opener=db.ecfr_rag,
    source_name="ecfr",
    indexer_script="ecfr (embed-only: POST /ecfr/regulations/{id}/embed)",
)
