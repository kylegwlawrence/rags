import sqlite3

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._embedded import embedded_clauses
from api._fts import translate_table_errors
from api.models import EmbedResult, FederalRegisterDoc, Page
from rag.chunker import chunk_markdown
from rag.embed_one import embed_doc
from rag.federal_register import build_doc
from rag.profiles import DEFAULT as _PROFILE

router = APIRouter(prefix="/federal_register", tags=["federal_register"])


def _row_to_doc(row: sqlite3.Row) -> FederalRegisterDoc:
    return FederalRegisterDoc(
        document_number=row["document_number"],
        title=row["title"],
        abstract=row["abstract"],
        type=row["type"],
        publication_date=row["publication_date"],
        agencies=row["agencies"],
        action=row["action"],
        effective_date=row["effective_date"],
        html_url=row["html_url"],
        pdf_url=row["pdf_url"],
    )


def _lookup(conn: sqlite3.Connection, document_number: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT document_number, title, abstract, type, publication_date, "
        "       agencies, action, effective_date, html_url, pdf_url, excerpts "
        "FROM documents WHERE document_number = ?",
        [document_number],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"document {document_number!r} not found")
    return row


@router.get("/documents", response_model=Page[FederalRegisterDoc])
def list_documents(
    q: str | None = Query(
        None,
        description=(
            "FTS5 full-text search over title + abstract. Accepts FTS5 syntax: "
            "`\"phrase\"`, `term*`, `a OR b`, `a NOT b`."
        ),
    ),
    type: str | None = Query(
        None,
        description="Exact match on document type (e.g. 'Rule', 'Proposed Rule', 'Notice').",
    ),
    agencies: str | None = Query(
        None,
        description="Substring match on the agencies field (case-insensitive via LIKE).",
    ),
    publication_year: int | None = Query(
        None,
        description="Filter to documents published in this year.",
    ),
    embedded: bool | None = Query(
        None,
        description=(
            "Filter by RAG embedding state: true = only documents chunked "
            "into federal_register_rag.db, false = only documents not yet "
            "embedded. Omit to list all (the default)."
        ),
    ),
    sort: str | None = Query(
        None,
        description="Sort order: 'newest' (default), 'oldest', or 'relevance' (requires q).",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.federal_register),
) -> Page[FederalRegisterDoc]:
    """List Federal Register documents with optional full-text, type, agency, and year filters."""
    from_clause = "documents"
    clauses: list[str] = []
    params: list = []

    if q is not None:
        from_clause = (
            "documents JOIN documents_fts ON documents_fts.rowid = documents.rowid"
        )
        clauses.append("documents_fts MATCH ?")
        params.append(q)
    if type is not None:
        clauses.append("documents.type = ?")
        params.append(type)
    if agencies is not None:
        clauses.append("documents.agencies LIKE ?")
        params.append(f"%{agencies}%")
    if publication_year is not None:
        clauses.append("strftime('%Y', documents.publication_date) = ?")
        params.append(str(publication_year))
    if embedded is not None:
        # doc_id == document_number, matches documents.document_number 1:1.
        c, p, empty = embedded_clauses(
            db.federal_register_rag,
            embedded=embedded,
            column="documents.document_number",
        )
        if empty:
            return Page[FederalRegisterDoc](items=[], total=0, limit=limit, offset=offset)
        clauses.extend(c)
        params.extend(p)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    if sort == "relevance" and q is None:
        raise HTTPException(status_code=400, detail="sort=relevance requires q")
    if sort == "oldest":
        order = "documents.publication_date ASC, documents.document_number ASC"
    elif sort == "relevance":
        order = "bm25(documents_fts) ASC"
    else:
        order = "documents.publication_date DESC, documents.document_number DESC"

    with translate_table_errors(
        "federal_register",
        "federal_register/federal_register_index_fts.py",
        "data/federal_register/federal_register.db",
    ):
        total = conn.execute(
            f"SELECT COUNT(*) FROM {from_clause} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT documents.document_number, documents.title, documents.abstract, "
            f"       documents.type, documents.publication_date, documents.agencies, "
            f"       documents.action, documents.effective_date, "
            f"       documents.html_url, documents.pdf_url "
            f"FROM {from_clause} {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    return Page[FederalRegisterDoc](
        items=[_row_to_doc(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# Content route must come before the detail route — both share the same prefix.
@router.get("/documents/{document_number}/content")
def get_document_content(
    document_number: str,
    conn: sqlite3.Connection = Depends(db.federal_register),
) -> Response:
    """Return the abstract (falling back to excerpts) for one document as text/plain."""
    row = _lookup(conn, document_number)
    body = row["abstract"] or row["excerpts"]
    if not body:
        raise HTTPException(status_code=404, detail="document has no text content")
    return Response(content=body, media_type="text/plain; charset=utf-8")


@router.post("/documents/{document_number}/embed", response_model=EmbedResult)
def embed_document(
    document_number: str,
    conn: sqlite3.Connection = Depends(db.federal_register),
) -> EmbedResult:
    """Embed one Federal Register document into federal_register_rag.db (synchronous).

    Renders the document to section-headered markdown via
    `rag.federal_register.build_doc` — the same builder
    `federal_register_index_rag.py` uses, so a button-embedded document
    chunks identically to a batch indexer pass. Replaces any chunks already
    stored for it, becoming searchable through `/federal_register/chunks`
    immediately (the RAG DB runs in WAL mode, so the cached read-only
    connection picks up the new rows without a uvicorn restart).

    Returns `embedded=false` when the document has neither title nor
    abstract. A 503 means Ollama was unreachable; existing chunks are
    untouched.
    """
    row = _lookup(conn, document_number)
    doc = build_doc(row)
    if doc is None:
        return EmbedResult(
            doc_id=document_number,
            title=row["title"] or document_number,
            chunk_count=0,
            embedded=False,
        )

    rag_conn = db.connect_rag_rw(db.FEDERAL_REGISTER_RAG_DB)
    try:
        chunk_count = embed_doc(
            rag_conn,
            doc,
            chunk_fn=chunk_markdown,
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


@router.get("/documents/{document_number}", response_model=FederalRegisterDoc)
def get_document(
    document_number: str,
    conn: sqlite3.Connection = Depends(db.federal_register),
) -> FederalRegisterDoc:
    """Return metadata for one Federal Register document by document number."""
    return _row_to_doc(_lookup(conn, document_number))


add_chunks_route(
    router,
    opener=db.federal_register_rag,
    source_name="federal_register",
    indexer_script="federal_register/federal_register_index_rag.py",
)
add_doc_chunks_route(
    router,
    opener=db.federal_register_rag,
    source_name="federal_register",
    indexer_script="federal_register/federal_register_index_rag.py",
)
