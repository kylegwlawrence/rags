import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from api import db
from api.models import GutenbergText, Page

router = APIRouter(prefix="/gutenberg", tags=["gutenberg"])


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
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.gutenberg),
) -> Page[GutenbergText]:
    """List Gutenberg texts. Title/author are substring filters; language is exact."""
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
