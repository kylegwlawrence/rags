import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route
from api._fts import translate_fts_errors
from api.models import Page, PydocsDoc

router = APIRouter(prefix="/pydocs", tags=["pydocs"])


def _row_to_doc(row: sqlite3.Row) -> PydocsDoc:
    """Map a `docs` row to its response model. Raw `content` lives at /content."""
    return PydocsDoc(
        doc_path=row["doc_path"],
        section=row["section"],
        title=row["title"],
        content_chars=row["content_chars"],
    )


def _lookup(conn: sqlite3.Connection, doc_path: str) -> sqlite3.Row:
    """Fetch a `docs` row by doc_path or raise 404."""
    row = conn.execute(
        "SELECT doc_path, section, title, content, "
        "       length(content) AS content_chars "
        "FROM docs WHERE doc_path = ?",
        [doc_path],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"doc {doc_path!r} not found")
    return row


@router.get("/docs", response_model=Page[PydocsDoc])
def list_docs(
    section: str | None = Query(
        None,
        description="Exact match on the top-level section (e.g. 'library', 'tutorial', 'howto').",
    ),
    title: str | None = Query(
        None,
        description="Substring match on title (case-insensitive via LIKE).",
    ),
    q: str | None = Query(
        None,
        description=(
            "FTS5 full-text search over title + content. Accepts FTS5 syntax: "
            "bare words AND together, `\"phrase\"` for phrases, `term*` for "
            "prefix match, `a OR b`, `a NOT b`."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.pydocs),
) -> Page[PydocsDoc]:
    """List Python documentation pages with section / title / FTS filters."""
    from_clause = "docs"
    clauses: list[str] = []
    params: list = []
    if q is not None:
        from_clause = "docs JOIN docs_fts ON docs_fts.rowid = docs.id"
        clauses.append("docs_fts MATCH ?")
        params.append(q)
    if section is not None:
        clauses.append("section = ?")
        params.append(section)
    if title is not None:
        clauses.append("title LIKE ?")
        params.append(f"%{title}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = "bm25(docs_fts) ASC" if q is not None else "docs.doc_path ASC"

    with translate_fts_errors("pydocs", "python_docs/python_docs_index_fts.py", "data/pydocs/python_docs.db"):
        total = conn.execute(
            f"SELECT COUNT(*) FROM {from_clause} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT docs.doc_path, docs.section, docs.title, "
            f"       length(docs.content) AS content_chars "
            f"FROM {from_clause} {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    return Page[PydocsDoc](
        items=[_row_to_doc(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# Content route comes BEFORE the detail route because both use `{doc_path:path}`,
# which is greedy and would otherwise consume `.../content` as part of doc_path.
@router.get("/docs/{doc_path:path}/content")
def get_doc_content(
    doc_path: str,
    conn: sqlite3.Connection = Depends(db.pydocs),
) -> Response:
    """Return the raw Sphinx-text body for one doc as text/plain.

    No server-side rendering — the text-builder output is the canonical
    representation here. The /pydocs/chunks endpoint already exposes the
    markdown-rendered body in chunked form for retrieval; downstream tools
    that want different formatting can pipe this through their own renderer.
    """
    row = _lookup(conn, doc_path)
    if not row["content"]:
        raise HTTPException(status_code=404, detail="doc has no body")
    return Response(content=row["content"], media_type="text/plain; charset=utf-8")


@router.get("/docs/{doc_path:path}", response_model=PydocsDoc)
def get_doc(
    doc_path: str,
    conn: sqlite3.Connection = Depends(db.pydocs),
) -> PydocsDoc:
    """Return metadata for one doc by its slash-separated path (e.g. `library/os`).

    `{doc_path:path}` so the slash in path-style ids matches cleanly.
    """
    return _row_to_doc(_lookup(conn, doc_path))


add_chunks_route(
    router,
    opener=db.pydocs_rag,
    source_name="pydocs",
    indexer_script="python_docs/python_docs_index_rag.py",
    rag_db_path="data/pydocs/python_docs_rag.db",
)
