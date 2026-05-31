"""Read-only API for locally ingested PDFs.

The `pdfs_ingest.py` script stores one metadata row per PDF (plus per-page text)
in `pdfs.db`, leaving the original files in the `incoming/` drop folder. This
router lists/serves that metadata and streams the original PDF bytes from
`incoming/` so the frontend can render the document in an in-browser viewer.

`doc_id` is the source filename stem. There is no FTS or RAG layer for this
source yet, so the list endpoint offers only substring filters.
"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from api import db
from api.models import Page, PdfDocument

router = APIRouter(prefix="/pdfs", tags=["pdfs"])

# Metadata columns surfaced by the list/detail endpoints (everything except the
# on-disk bookkeeping fields source_path / sha256, which callers don't need).
_COLUMNS = (
    "doc_id, title, author, subject, keywords, creator, producer, "
    "creation_date, mod_date, num_pages, file_size, ingested_at"
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
    title: str | None = Query(None, description="Substring match on PDF title metadata"),
    author: str | None = Query(None, description="Substring match on PDF author metadata"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.pdfs),
) -> Page[PdfDocument]:
    """List ingested PDFs, newest first. Title/author are substring filters."""
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
