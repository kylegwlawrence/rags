import sqlite3

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api.models import EmbedResult, GutenbergText, Page
from rag import Doc
from rag.cleaner import CLEANER_VERSION
from rag.embed_one import embed_doc
from rag.gutenberg_text import file_fingerprint, read_text, strip_banners
from rag.profiles import LONG_FORM as _PROFILE

router = APIRouter(prefix="/gutenberg", tags=["gutenberg"])

# Live-embed chunk settings come from `rag.profiles.LONG_FORM` — the same
# profile `scripts/gutenberg/gutenberg_index_rag.py` uses, so a text
# embedded via the button chunks identically to a batch indexer pass.
# Whole-book embeds can produce thousands of chunks and take many minutes
# on local Ollama; that's a deliberate trade so the click does the real
# work synchronously rather than queueing a job.


def _row_to_text(row: sqlite3.Row) -> GutenbergText:
    """Map a `texts` row to its response model."""
    return GutenbergText(
        id=row["id"],
        title=row["title"],
        author=row["author"],
        language=row["language"],
        release_date=row["release_date"],
        size_bytes=row["size_bytes"],
        path=row["path"],
    )


@router.get("/texts", response_model=Page[GutenbergText])
def list_texts(
    title: str | None = Query(None, description="Substring match on title"),
    author: str | None = Query(None, description="Substring match on author"),
    language: str | None = Query(None, description="Exact language code, e.g. 'en'"),
    embedded: bool | None = Query(
        None,
        description=(
            "Filter by RAG embedding state: true = only texts whose body has "
            "been chunked into gutenberg_rag.db, false = only texts not yet "
            "embedded. Omit to list all texts (the default)."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.gutenberg),
) -> Page[GutenbergText]:
    """List Gutenberg texts. Title/author are substring filters; language is exact.

    The `embedded` filter cross-references the separate `gutenberg_rag.db` to
    select texts that do (or don't) have any chunks indexed. The rag DB is
    opened lazily so a missing rag DB still allows unfiltered listing.
    """
    clauses: list[str] = []
    params: list = []
    if title is not None:
        clauses.append("title LIKE ?")
        params.append(f"%{title}%")
    if author is not None:
        clauses.append("author LIKE ?")
        params.append(f"%{author}%")
    if language is not None:
        clauses.append("language = ?")
        params.append(language)
    if embedded is not None:
        # doc_id is stored as TEXT in gutenberg_rag.db (values are stringified
        # int text ids). Pull the distinct set once and inline it as a literal
        # IN-list — the rag corpus is small (low thousands at most) so this
        # stays well under SQLite's parameter and expression limits.
        rag_conn = db.gutenberg_rag()
        embedded_ids = [
            int(r[0]) for r in rag_conn.execute("SELECT DISTINCT doc_id FROM chunks")
        ]
        if embedded:
            if not embedded_ids:
                # No texts are embedded yet → empty page without touching the
                # main DB at all.
                return Page[GutenbergText](items=[], total=0, limit=limit, offset=offset)
            placeholders = ",".join("?" * len(embedded_ids))
            clauses.append(f"id IN ({placeholders})")
            params.extend(embedded_ids)
        else:
            if embedded_ids:
                placeholders = ",".join("?" * len(embedded_ids))
                clauses.append(f"id NOT IN ({placeholders})")
                params.extend(embedded_ids)
            # else: nothing is embedded → every row qualifies as "unembedded",
            # so no extra clause needed.
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    total = conn.execute(f"SELECT COUNT(*) FROM texts {where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT id, path, title, author, language, release_date, size_bytes "
        f"FROM texts {where} ORDER BY id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return Page[GutenbergText](
        items=[_row_to_text(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _lookup(conn: sqlite3.Connection, text_id: int) -> sqlite3.Row:
    """Fetch a `texts` row by id or raise 404."""
    row = conn.execute(
        "SELECT id, path, title, author, language, release_date, size_bytes "
        "FROM texts WHERE id = ?",
        [text_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"text {text_id} not found")
    return row


@router.get("/texts/{text_id}", response_model=GutenbergText)
def get_text(
    text_id: int,
    conn: sqlite3.Connection = Depends(db.gutenberg),
) -> GutenbergText:
    """Return metadata for one Gutenberg text."""
    return _row_to_text(_lookup(conn, text_id))


@router.get("/texts/{text_id}/content")
def get_text_content(
    text_id: int,
    conn: sqlite3.Connection = Depends(db.gutenberg),
) -> FileResponse:
    """Stream the raw .txt file for one Gutenberg text."""
    row = _lookup(conn, text_id)
    # Defense in depth: even though the indexer only stores relative paths
    # produced via Path.relative_to(GUTENBERG_ROOT), refuse to serve anything
    # whose resolved location escapes the gutenberg root.
    root = db.GUTENBERG_ROOT.resolve()
    full = (db.GUTENBERG_ROOT / row["path"]).resolve()
    if root not in full.parents:
        raise HTTPException(status_code=404, detail="text not found")
    if not full.is_file():
        raise HTTPException(status_code=404, detail="file missing on disk")
    return FileResponse(full, media_type="text/plain; charset=utf-8")


@router.post("/texts/{text_id}/embed", response_model=EmbedResult)
def embed_text(
    text_id: int,
    conn: sqlite3.Connection = Depends(db.gutenberg),
) -> EmbedResult:
    """Embed one Gutenberg text into gutenberg_rag.db on demand (synchronous).

    Reads the `.txt` body from disk via `rag.gutenberg_text`, strips the PG
    start/end banners, and replaces any chunks already stored for the text.
    The text becomes searchable through `/gutenberg/chunks` immediately — the
    RAG DB runs in WAL mode, so the cached read-only connection picks up the
    new rows without a uvicorn restart.

    Whole-book embeds can run for tens of minutes on local Ollama — the
    button stays in "Embedding…" the whole time. A 503 means Ollama was
    unreachable; existing chunks are untouched.
    """
    row = _lookup(conn, text_id)
    # Defense in depth: even though the indexer only stores relative paths
    # produced via Path.relative_to(GUTENBERG_ROOT), refuse to embed anything
    # whose resolved location escapes the gutenberg root.
    root = db.GUTENBERG_ROOT.resolve()
    full = (db.GUTENBERG_ROOT / row["path"]).resolve()
    if root not in full.parents:
        raise HTTPException(status_code=404, detail="text not found")
    if not full.is_file():
        raise HTTPException(status_code=404, detail="file missing on disk")

    body = strip_banners(read_text(full))
    title = row["title"] or row["author"] or str(row["id"])
    doc = Doc(
        doc_id=str(row["id"]),
        title=title,
        version=f"{file_fingerprint(full)}-{CLEANER_VERSION}",
        text=body,
        section=None,
    )

    rag_conn = db.connect_rag_rw(db.GUTENBERG_RAG_DB)
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


add_chunks_route(
    router,
    opener=db.gutenberg_rag,
    source_name="gutenberg",
    indexer_script="gutenberg_index_rag.py",
)
add_doc_chunks_route(
    router,
    opener=db.gutenberg_rag,
    source_name="gutenberg",
    indexer_script="gutenberg_index_rag.py",
)
