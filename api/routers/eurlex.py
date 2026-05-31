import sqlite3

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._embedded import embedded_clauses
from api._fts import translate_table_errors
from api.models import EmbedResult, EurlexLaw, EurlexLawDetail, Page
from rag.embed_one import embed_doc
from rag.eurlex import build_doc
from rag.profiles import DEFAULT as _PROFILE

router = APIRouter(prefix="/eurlex", tags=["eurlex"])


def _split_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [s for s in (part.strip() for part in raw.split(";")) if s]


def _row_to_law(row: sqlite3.Row) -> EurlexLaw:
    return EurlexLaw(
        celex=row["CELEX"],
        act_name=row["Act_name"] or None,
        act_type=row["Act_type"] or None,
        status=row["Status"] or None,
        eurovoc=_split_list(row["EUROVOC"]),
        subject_matter=_split_list(row["Subject_matter"]),
        treaty=row["Treaty"] or None,
        authors=_split_list(row["Authors"]),
        date_document=row["Date_document"] or None,
        date_publication=row["Date_publication"] or None,
        eurlex_link=row["Eurlex_link"] or None,
        eli_link=row["ELI_link"] or None,
        text_chars=row["text_chars"],
    )


_META_COLS = (
    "CELEX, Act_name, Act_type, Status, EUROVOC, Subject_matter, Treaty, "
    "Authors, Date_document, Date_publication, Eurlex_link, ELI_link, "
    "length(act_raw_text) AS text_chars"
)


@router.get("/laws", response_model=Page[EurlexLaw])
def list_laws(
    q: str | None = Query(
        None,
        description=(
            "FTS5 full-text search over act name and body text. Accepts FTS5 "
            "syntax: `\"phrase\"`, `term*`, `a OR b`, `a NOT b`. "
            "Requires the FTS index (scripts/eurlex/eurlex_index_fts.py)."
        ),
    ),
    act_type: str | None = Query(
        None,
        description="Exact match on Act_type (e.g. Decision, Regulation, Directive).",
    ),
    status: str | None = Query(
        None,
        description="Exact match on Status (e.g. 'In Force', 'Not in Force').",
    ),
    author: str | None = Query(
        None,
        description="Substring match against the Authors field (case-insensitive).",
    ),
    embedded: bool | None = Query(
        None,
        description=(
            "Filter by RAG embedding state: true = only laws chunked into "
            "eurlex_rag.db, false = only laws not yet embedded. Omit to list "
            "all (the default)."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.eurlex),
) -> Page[EurlexLaw]:
    """List EUR-Lex legislative acts with optional full-text search and filters."""
    from_clause = "laws"
    clauses: list[str] = []
    params: list = []

    if q is not None:
        from_clause = "laws JOIN laws_fts ON laws_fts.rowid = laws.rowid"
        clauses.append("laws_fts MATCH ?")
        params.append(q)
    if act_type is not None:
        clauses.append("laws.Act_type = ?")
        params.append(act_type)
    if status is not None:
        clauses.append("laws.Status = ?")
        params.append(status)
    if author is not None:
        clauses.append("laws.Authors LIKE ?")
        params.append(f"%{author}%")
    if embedded is not None:
        # doc_id == CELEX, matches laws.CELEX 1:1.
        c, p, empty = embedded_clauses(
            db.eurlex_rag, embedded=embedded, column="laws.CELEX",
        )
        if empty:
            return Page[EurlexLaw](items=[], total=0, limit=limit, offset=offset)
        clauses.extend(c)
        params.extend(p)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    if q is not None:
        order = "bm25(laws_fts) ASC"
    else:
        order = "laws.Date_publication DESC, laws.CELEX DESC"

    with translate_table_errors(
        "eurlex",
        "eurlex/eurlex_index_fts.py",
        "data/eurlex/eurlex.db",
    ):
        total = conn.execute(
            f"SELECT COUNT(*) FROM {from_clause} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT laws.CELEX, laws.Act_name, laws.Act_type, laws.Status, "
            f"       laws.EUROVOC, laws.Subject_matter, laws.Treaty, laws.Authors, "
            f"       laws.Date_document, laws.Date_publication, laws.Eurlex_link, "
            f"       laws.ELI_link, length(laws.act_raw_text) AS text_chars "
            f"FROM {from_clause} {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    return Page[EurlexLaw](
        items=[_row_to_law(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# /content and /embed must come before /{celex} so FastAPI doesn't treat those
# tokens as CELEX ids.
@router.post("/laws/{celex}/embed", response_model=EmbedResult)
def embed_law(
    celex: str,
    conn: sqlite3.Connection = Depends(db.eurlex),
) -> EmbedResult:
    """Embed one EUR-Lex law into eurlex_rag.db on demand (synchronous).

    Reads the act's `act_raw_text` body and chunks it as flat prose via the
    same `rag.eurlex.build_doc` builder the batch indexer uses. Replaces any
    chunks already stored for the law, becoming searchable through
    `/eurlex/chunks` immediately (the RAG DB runs in WAL mode, so the cached
    read-only connection picks up the new rows without a uvicorn restart).

    Returns `embedded=false` when the law has no body text. A 503 means
    Ollama was unreachable; existing chunks are untouched.
    """
    row = conn.execute(
        "SELECT CELEX, Act_name, act_raw_text FROM laws WHERE CELEX = ?",
        [celex],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"law {celex!r} not found")
    doc = build_doc(row)
    if doc is None:
        return EmbedResult(
            doc_id=celex,
            title=row["Act_name"] or celex,
            chunk_count=0,
            embedded=False,
        )

    rag_conn = db.connect_rag_rw(db.EURLEX_RAG_DB)
    try:
        chunk_count = embed_doc(
            rag_conn,
            doc,
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


@router.get("/laws/{celex}/content")
def get_law_content(
    celex: str,
    conn: sqlite3.Connection = Depends(db.eurlex),
) -> Response:
    """Return the act's raw body text as text/plain."""
    row = conn.execute(
        "SELECT act_raw_text FROM laws WHERE CELEX = ?", [celex]
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"law {celex!r} not found")
    if not row["act_raw_text"]:
        raise HTTPException(status_code=404, detail="law has no body text")
    return Response(content=row["act_raw_text"], media_type="text/plain; charset=utf-8")


@router.get("/laws/{celex}", response_model=EurlexLawDetail)
def get_law(
    celex: str,
    conn: sqlite3.Connection = Depends(db.eurlex),
) -> EurlexLawDetail:
    """Return one EUR-Lex act with full metadata."""
    row = conn.execute(
        f"SELECT {_META_COLS}, Legal_basis_celex, Treaty, "
        f"       Procedure_number, First_entry_into_force, "
        f"       Act_cites, Act_ammends, Proposal_link, Oeil_link "
        f"FROM laws WHERE CELEX = ?",
        [celex],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"law {celex!r} not found")
    base = _row_to_law(row)
    return EurlexLawDetail(
        **base.model_dump(),
        legal_basis_celex=row["Legal_basis_celex"] or None,
        procedure_number=row["Procedure_number"] or None,
        first_entry_into_force=row["First_entry_into_force"] or None,
        act_cites=_split_list(row["Act_cites"]),
        act_amends=_split_list(row["Act_ammends"]),
        proposal_link=row["Proposal_link"] or None,
        oeil_link=row["Oeil_link"] or None,
    )


add_chunks_route(
    router,
    opener=db.eurlex_rag,
    source_name="eurlex",
    indexer_script="eurlex/eurlex_index_rag.py",
)
add_doc_chunks_route(
    router,
    opener=db.eurlex_rag,
    source_name="eurlex",
    indexer_script="eurlex/eurlex_index_rag.py",
)
