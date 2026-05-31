"""Read-only API for locally ingested PDFs.

The `pdfs_ingest.py` script stores one metadata row per PDF (plus per-page text)
in `pdfs.db`, leaving the original files in the `incoming/` drop folder. This
router lists/serves that metadata and streams the original PDF bytes from
`incoming/` so the frontend can render the document in an in-browser viewer.

`doc_id` is the source filename stem. The list endpoint supports full-text
search (`?q=`) over the page text via the `pages_fts` index built by
`scripts/pdfs/pdfs_index_fts.py`.

Semantic search is served by `/pdfs/chunks` over `pdfs_rag.db`
(`scripts/pdfs/pdfs_index_rag.py`). PDFs are chunked page by page, so each
chunk's `section` is its page label (`"p. 42"`) — the frontend reads that to
deep-link the in-browser viewer to the matching page.
"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._fts import translate_table_errors
from api.models import Page, PdfDocument

router = APIRouter(prefix="/pdfs", tags=["pdfs"])

# Metadata columns surfaced by the list/detail endpoints (everything except the
# on-disk bookkeeping fields source_path / sha256, which callers don't need).
_COLUMNS = (
    "doc_id, title, author, subject, keywords, creator, producer, "
    "creation_date, mod_date, num_pages, file_size, ingested_at"
)

# Same columns qualified with the `documents.` table alias, for the `?q=` path
# where the query joins `documents` to `pages`/`pages_fts` and bare `doc_id`
# would be ambiguous.
_COLUMNS_QUALIFIED = ", ".join(
    f"documents.{c.strip()}" for c in _COLUMNS.split(",")
)


def _row_to_doc(row: sqlite3.Row) -> PdfDocument:
    """Map a `documents` row to its response model."""
    return PdfDocument(
        doc_id=row["doc_id"],
        title=row["title"],
        author=row["author"],
        subject=row["subject"],
        keywords=row["keywords"],
        creator=row["creator"],
        producer=row["producer"],
        creation_date=row["creation_date"],
        mod_date=row["mod_date"],
        num_pages=row["num_pages"],
        file_size=row["file_size"],
        ingested_at=row["ingested_at"],
    )


@router.get("/documents", response_model=Page[PdfDocument])
def list_documents(
    q: str | None = Query(
        None,
        description=(
            "FTS5 full-text search over the PDF page text. Page hits are rolled "
            "up to their parent document, so results are whole PDFs. Accepts "
            "FTS5 syntax: `\"phrase\"`, `term*`, `a OR b`, `a NOT b`. Requires "
            "the pages_fts index (scripts/pdfs/pdfs_index_fts.py)."
        ),
    ),
    title: str | None = Query(None, description="Substring match on PDF title metadata"),
    author: str | None = Query(None, description="Substring match on PDF author metadata"),
    sort: str | None = Query(
        None,
        description=(
            "Sort order: 'relevance' (BM25; requires q) or 'recent' "
            "(newest-first). Default is relevance when q is given, else recent."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.pdfs),
) -> Page[PdfDocument]:
    """List ingested PDFs. `q` runs a full-text search over the page text and
    returns whole documents (de-duplicated across matching pages); title/author
    are substring filters. Defaults to newest-first, or BM25 relevance when `q`
    is given."""
    if sort == "relevance" and q is None:
        raise HTTPException(status_code=400, detail="sort=relevance requires q")

    if q is None:
        # No search: plain document listing with optional substring filters.
        clauses: list[str] = []
        params: list = []
        if title is not None:
            clauses.append("title LIKE ?")
            params.append(f"%{title}%")
        if author is not None:
            clauses.append("author LIKE ?")
            params.append(f"%{author}%")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        total = conn.execute(
            f"SELECT COUNT(*) FROM documents {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM documents {where} "
            f"ORDER BY ingested_at DESC, doc_id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return Page[PdfDocument](
            items=[_row_to_doc(r) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    # `?q=` path: the FTS index is per-page, so search runs in two stages and
    # rolls the page hits up to whole documents.
    #
    #   `scored`  — one row per matching page with its BM25 score. The score is
    #               selected as a plain column because FTS5 auxiliary functions
    #               like bm25() can't be nested inside an aggregate. DISTINCT is
    #               an optimisation fence: without it SQLite flattens this CTE
    #               into the aggregate below, putting bm25() back inside MIN()
    #               and failing with "unable to use function bm25".
    #   `ranked`  — collapses to one row per doc_id, keeping that PDF's single
    #               best (most negative = most relevant) page score.
    #
    # The outer query then joins each matched document's metadata and applies
    # the title/author substring filters.
    cte = (
        "WITH scored AS ("
        "  SELECT DISTINCT pages.doc_id AS doc_id, bm25(pages_fts) AS score "
        "  FROM pages_fts "
        "  JOIN pages ON pages.rowid = pages_fts.rowid "
        "  WHERE pages_fts MATCH ?"
        "), ranked AS ("
        "  SELECT doc_id, MIN(score) AS score FROM scored GROUP BY doc_id"
        ")"
    )
    join = "ranked JOIN documents ON documents.doc_id = ranked.doc_id"
    clauses: list[str] = []
    params: list = [q]
    if title is not None:
        clauses.append("documents.title LIKE ?")
        params.append(f"%{title}%")
    if author is not None:
        clauses.append("documents.author LIKE ?")
        params.append(f"%{author}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    # 'recent' keeps newest-first; otherwise rank by each PDF's best-matching
    # page (more-negative BM25 = more relevant, so ranked.score ASC first).
    if sort == "recent":
        order = "documents.ingested_at DESC, documents.doc_id"
    else:
        order = "ranked.score ASC, documents.doc_id"

    with translate_table_errors(
        "pdfs",
        "pdfs/pdfs_index_fts.py",
        "data/pdfs/pdfs.db",
    ):
        total = conn.execute(
            f"{cte} SELECT COUNT(*) FROM {join} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"{cte} SELECT {_COLUMNS_QUALIFIED} FROM {join} {where} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    return Page[PdfDocument](
        items=[_row_to_doc(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _lookup(conn: sqlite3.Connection, doc_id: str) -> sqlite3.Row:
    """Fetch a `documents` row by doc_id or raise 404."""
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM documents WHERE doc_id = ?", [doc_id]
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"PDF {doc_id!r} not found")
    return row


@router.get("/documents/{doc_id}", response_model=PdfDocument)
def get_document(
    doc_id: str,
    conn: sqlite3.Connection = Depends(db.pdfs),
) -> PdfDocument:
    """Return metadata for one ingested PDF."""
    return _row_to_doc(_lookup(conn, doc_id))


@router.get("/documents/{doc_id}/content")
def get_document_content(
    doc_id: str,
    conn: sqlite3.Connection = Depends(db.pdfs),
) -> FileResponse:
    """Stream the original PDF file inline so a browser can render it.

    The body is served as `application/pdf` with an *inline* content
    disposition so the frontend's <iframe> displays it rather than triggering a
    download.
    """
    row = conn.execute(
        "SELECT source_path FROM documents WHERE doc_id = ?", [doc_id]
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"PDF {doc_id!r} not found")

    # Defense in depth: source_path is a relative path produced via
    # Path.relative_to(INCOMING_DIR) at ingest time, but refuse to serve
    # anything whose resolved location escapes the incoming folder.
    root = db.PDFS_INCOMING.resolve()
    full = (db.PDFS_INCOMING / row["source_path"]).resolve()
    if root not in full.parents:
        raise HTTPException(status_code=404, detail="PDF not found")
    if not full.is_file():
        raise HTTPException(status_code=404, detail="file missing on disk")

    return FileResponse(
        full,
        media_type="application/pdf",
        content_disposition_type="inline",
        filename=full.name,
    )


# Semantic search over pdfs_rag.db. Chunks are page-tagged (section = "p. N"),
# so a hit tells the frontend which page to open in the viewer.
add_chunks_route(
    router,
    opener=db.pdfs_rag,
    source_name="pdfs",
    indexer_script="pdfs/pdfs_index_rag.py",
)
add_doc_chunks_route(
    router,
    opener=db.pdfs_rag,
    source_name="pdfs",
    indexer_script="pdfs/pdfs_index_rag.py",
)
