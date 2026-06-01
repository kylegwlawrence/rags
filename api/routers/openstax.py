"""Read-only API for OpenStax textbooks.

`openstax_download.py` loads three tables into `openstax.db`: one row per book
(`books`), per chapter (`chapters`), and per section (`sections`, with the
section body as plain text + inline `$…$` LaTeX). This router lists books, lists
/ full-text-searches sections, and serves each section's body.

`book_id` is the collection slug (e.g. `calculus-volume-1`); a section is
addressed by `{book_id}/{module_id}`. Full-text search (`?q=`) runs over the
section title + learning objectives + body via the `sections_fts` index built
by `scripts/openstax/openstax_index_fts.py`.

Semantic search is served by `/openstax/chunks` over `openstax_rag.db`
(batch indexer `scripts/openstax/openstax_index_rag.py`, or the per-section
embed button). Each chunk's `section` is its "Chapter — Section" label.
"""

import sqlite3

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._embedded import embedded_clauses
from api._fts import translate_table_errors
from api.models import EmbedResult, OpenstaxBook, OpenstaxSection, Page
from rag.embed_one import embed_doc
from rag.openstax import build_doc
from rag.profiles import DEFAULT as _PROFILE

router = APIRouter(prefix="/openstax", tags=["openstax"])

# Section list/detail columns (everything but the large body), joined to the
# book for its title + subject. Qualified with table aliases so the FTS join
# (which also touches sections_fts) stays unambiguous.
_SECTION_COLS = (
    "s.section_id, s.book_id, b.title AS book_title, b.subject AS subject, "
    "s.chapter_number, s.chapter_title, s.module_id, s.title, s.objectives, "
    "length(s.body) AS content_chars"
)


def _row_to_book(row: sqlite3.Row) -> OpenstaxBook:
    return OpenstaxBook(
        book_id=row["book_id"],
        title=row["title"],
        subject=row["subject"],
        repo=row["repo"],
        uuid=row["uuid"],
        license=row["license"],
        num_chapters=row["num_chapters"],
        num_sections=row["num_sections"],
    )


def _row_to_section(row: sqlite3.Row) -> OpenstaxSection:
    return OpenstaxSection(
        section_id=row["section_id"],
        book_id=row["book_id"],
        book_title=row["book_title"],
        subject=row["subject"],
        chapter_number=row["chapter_number"],
        chapter_title=row["chapter_title"],
        module_id=row["module_id"],
        title=row["title"],
        objectives=row["objectives"],
        content_chars=row["content_chars"],
    )


@router.get("/books", response_model=Page[OpenstaxBook])
def list_books(
    q: str | None = Query(None, description="Substring match on the book title."),
    subject: str | None = Query(None, description="Filter to a subject (e.g. 'mathematics')."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.openstax),
) -> Page[OpenstaxBook]:
    """List OpenStax textbooks, newest-shelf-agnostic (subject then title order)."""
    clauses: list[str] = []
    params: list = []
    if q is not None:
        clauses.append("title LIKE ?")
        params.append(f"%{q}%")
    if subject is not None:
        clauses.append("subject = ?")
        params.append(subject)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM books {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT book_id, title, subject, repo, uuid, license, num_chapters, "
        f"num_sections FROM books {where} "
        f"ORDER BY subject, title LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return Page[OpenstaxBook](
        items=[_row_to_book(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/books/{book_id}", response_model=OpenstaxBook)
def get_book(
    book_id: str,
    conn: sqlite3.Connection = Depends(db.openstax),
) -> OpenstaxBook:
    """Return metadata for one textbook."""
    row = conn.execute(
        "SELECT book_id, title, subject, repo, uuid, license, num_chapters, "
        "num_sections FROM books WHERE book_id = ?",
        [book_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"book {book_id!r} not found")
    return _row_to_book(row)


@router.get("/sections", response_model=Page[OpenstaxSection])
def list_sections(
    q: str | None = Query(
        None,
        description=(
            "FTS5 full-text search over section title + learning objectives + "
            "body. Accepts FTS5 syntax: `\"phrase\"`, `term*`, `a OR b`, `a NOT b`."
        ),
    ),
    book_id: str | None = Query(None, description="Filter to one book (collection slug)."),
    subject: str | None = Query(None, description="Filter to a subject (e.g. 'mathematics')."),
    embedded: bool | None = Query(
        None,
        description=(
            "Filter by RAG embedding state: true = only sections chunked into "
            "openstax_rag.db, false = only sections not yet embedded. Omit for all."
        ),
    ),
    sort: str | None = Query(
        None,
        description="Sort order: 'document' (default, reading order) or 'relevance' (requires q).",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.openstax),
) -> Page[OpenstaxSection]:
    """List / full-text-search sections, with book/subject/embedded filters."""
    if sort == "relevance" and q is None:
        raise HTTPException(status_code=400, detail="sort=relevance requires q")

    from_clause = "sections s JOIN books b ON b.book_id = s.book_id"
    clauses: list[str] = []
    params: list = []

    if q is not None:
        # sections.id is the INTEGER PK, i.e. the rowid the FTS index keys on.
        from_clause += " JOIN sections_fts ON sections_fts.rowid = s.id"
        clauses.append("sections_fts MATCH ?")
        params.append(q)
    if book_id is not None:
        clauses.append("s.book_id = ?")
        params.append(book_id)
    if subject is not None:
        clauses.append("b.subject = ?")
        params.append(subject)
    if embedded is not None:
        c, p, empty = embedded_clauses(
            db.openstax_rag, embedded=embedded, column="s.section_id"
        )
        if empty:
            return Page[OpenstaxSection](items=[], total=0, limit=limit, offset=offset)
        clauses.extend(c)
        params.extend(p)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    if sort == "relevance":
        order = "bm25(sections_fts) ASC"
    else:
        order = "s.book_id, s.seq"

    with translate_table_errors(
        "openstax", "openstax/openstax_index_fts.py", "data/openstax/openstax.db"
    ):
        total = conn.execute(
            f"SELECT COUNT(*) FROM {from_clause} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT {_SECTION_COLS} FROM {from_clause} {where} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    return Page[OpenstaxSection](
        items=[_row_to_section(r) for r in rows], total=total, limit=limit, offset=offset
    )


def _lookup_section(
    conn: sqlite3.Connection, book_id: str, module_id: str
) -> sqlite3.Row:
    """Fetch one section's full row (incl. body) by book + module, or 404."""
    row = conn.execute(
        "SELECT s.section_id, s.book_id, b.title AS book_title, b.subject AS subject, "
        "s.chapter_number, s.chapter_title, s.module_id, s.title, s.objectives, "
        "s.body, length(s.body) AS content_chars "
        "FROM sections s JOIN books b ON b.book_id = s.book_id "
        "WHERE s.book_id = ? AND s.module_id = ?",
        [book_id, module_id],
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"section {book_id}/{module_id} not found",
        )
    return row


# Content/embed routes come before the section detail route — same prefix depth.
@router.get("/books/{book_id}/sections/{module_id}/content")
def get_section_content(
    book_id: str,
    module_id: str,
    conn: sqlite3.Connection = Depends(db.openstax),
) -> Response:
    """Return one section's body (plain text with inline `$…$` LaTeX)."""
    row = _lookup_section(conn, book_id, module_id)
    if not row["body"]:
        raise HTTPException(status_code=404, detail="section has no body text")
    return Response(content=row["body"], media_type="text/plain; charset=utf-8")


@router.post("/books/{book_id}/sections/{module_id}/embed", response_model=EmbedResult)
def embed_section(
    book_id: str,
    module_id: str,
    conn: sqlite3.Connection = Depends(db.openstax),
) -> EmbedResult:
    """Embed one section into openstax_rag.db on demand (synchronous).

    Reuses the shared `rag.openstax.build_doc` + flat `chunk_doc` (DEFAULT
    profile) so a button-embedded section chunks identically to a batch-indexed
    one. Replaces any chunks already stored for this section, becoming
    searchable through `/openstax/chunks` immediately (the RAG DB runs in WAL
    mode, so the cached read-only connection sees the new rows without a uvicorn
    restart). A 503 means Ollama was unreachable; existing chunks are untouched.
    """
    row = _lookup_section(conn, book_id, module_id)
    doc = build_doc(row)
    title = (row["title"] or "").strip() or f"{book_id}/{module_id}"
    if doc is None:
        return EmbedResult(
            doc_id=row["section_id"], title=title, chunk_count=0, embedded=False
        )

    rag_conn = db.connect_rag_rw(db.OPENSTAX_RAG_DB)
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


@router.get("/books/{book_id}/sections/{module_id}", response_model=OpenstaxSection)
def get_section(
    book_id: str,
    module_id: str,
    conn: sqlite3.Connection = Depends(db.openstax),
) -> OpenstaxSection:
    """Return metadata + learning objectives for one section (body at /content)."""
    return _row_to_section(_lookup_section(conn, book_id, module_id))


add_chunks_route(
    router,
    opener=db.openstax_rag,
    source_name="openstax",
    indexer_script="openstax/openstax_index_rag.py",
)
add_doc_chunks_route(
    router,
    opener=db.openstax_rag,
    source_name="openstax",
    indexer_script="openstax/openstax_index_rag.py",
)
