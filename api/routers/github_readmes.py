import sqlite3

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._embedded import embedded_clauses
from api._fts import translate_table_errors
from api.models import EmbedResult, GithubReadme, Page
from rag import Doc, content_hash
from rag.chunker import chunk_markdown
from rag.cleaner import CLEANER_VERSION, strip_html
from rag.embed_one import embed_doc
from rag.profiles import DEFAULT as _PROFILE

router = APIRouter(prefix="/github", tags=["github"])


def _row_to_readme(row: sqlite3.Row) -> GithubReadme:
    return GithubReadme(
        repo=row["repo"],
        owner=row["owner"],
        name=row["name"],
        source_list=row["source_list"],
        readme_chars=row["readme_chars"],
    )


def _lookup_meta(conn: sqlite3.Connection, repo: str) -> sqlite3.Row:
    """Fetch a `readmes` row's metadata (no body) by repo or raise 404."""
    row = conn.execute(
        "SELECT repo, owner, name, source_list, status, "
        "       length(readme) AS readme_chars "
        "FROM readmes WHERE repo = ? AND status = 'fetched'",
        [repo],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"readme {repo!r} not found")
    return row


def _lookup_with_body(conn: sqlite3.Connection, repo: str) -> sqlite3.Row:
    """Fetch a `readmes` row including `readme` body by repo or raise 404."""
    row = conn.execute(
        "SELECT repo, owner, name, source_list, status, readme, "
        "       length(readme) AS readme_chars "
        "FROM readmes WHERE repo = ? AND status = 'fetched'",
        [repo],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"readme {repo!r} not found")
    return row


@router.get("/readmes", response_model=Page[GithubReadme])
def list_readmes(
    q: str | None = Query(
        None,
        description=(
            "FTS5 full-text search over repo name + README body. Accepts FTS5 syntax: "
            "`\"phrase\"`, `term*`, `a OR b`, `a NOT b`."
        ),
    ),
    owner: str | None = Query(
        None,
        description="Substring match on the repository owner (case-insensitive via LIKE).",
    ),
    source_list: str | None = Query(
        None,
        description="Exact match on the awesome-list this repo was discovered from.",
    ),
    embedded: bool | None = Query(
        None,
        description=(
            "Filter by RAG embedding state: true = only READMEs chunked into "
            "github_readmes_rag.db, false = only READMEs not yet embedded. "
            "Omit to list all (the default)."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.github),
) -> Page[GithubReadme]:
    """List GitHub READMEs (fetched only) with optional FTS, owner, and source-list filters."""
    from_clause = "readmes"
    clauses: list[str] = ["readmes.status = 'fetched'"]
    params: list = []

    if q is not None:
        from_clause = "readmes JOIN readmes_fts ON readmes_fts.rowid = readmes.rowid"
        clauses.append("readmes_fts MATCH ?")
        params.append(q)
    if owner is not None:
        clauses.append("readmes.owner LIKE ?")
        params.append(f"%{owner}%")
    if source_list is not None:
        clauses.append("readmes.source_list = ?")
        params.append(source_list)
    if embedded is not None:
        # doc_id == repo slug, matches readmes.repo 1:1.
        c, p, empty = embedded_clauses(
            db.github_rag, embedded=embedded, column="readmes.repo",
        )
        if empty:
            return Page[GithubReadme](items=[], total=0, limit=limit, offset=offset)
        clauses.extend(c)
        params.extend(p)

    where = "WHERE " + " AND ".join(clauses)
    order = "bm25(readmes_fts) ASC" if q is not None else "readmes.repo ASC"

    with translate_table_errors(
        "github",
        "github_readmes/github_readmes_index_fts.py",
        "data/github/readmes.db",
    ):
        total = conn.execute(
            f"SELECT COUNT(*) FROM {from_clause} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT readmes.repo, readmes.owner, readmes.name, readmes.source_list, "
            f"       length(readmes.readme) AS readme_chars "
            f"FROM {from_clause} {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    return Page[GithubReadme](
        items=[_row_to_readme(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# Content route must come before the detail route — both use the {repo:path} prefix.
@router.get("/readmes/{repo:path}/content")
def get_readme_content(
    repo: str,
    conn: sqlite3.Connection = Depends(db.github),
) -> Response:
    """Return the raw README markdown for one repository as text/plain."""
    row = _lookup_with_body(conn, repo)
    if not row["readme"]:
        raise HTTPException(status_code=404, detail="README has no content")
    return Response(content=row["readme"], media_type="text/plain; charset=utf-8")


@router.post("/readmes/{repo:path}/embed", response_model=EmbedResult)
def embed_readme(
    repo: str,
    conn: sqlite3.Connection = Depends(db.github),
) -> EmbedResult:
    """Embed one README into github_readmes_rag.db on demand (synchronous).

    Cleans the README via the same `strip_html` path as
    `github_readmes_index_rag.py` and replaces any chunks already stored for
    it, so the repo becomes searchable through `/github/chunks` immediately —
    the RAG DB runs in WAL mode, so the cached read-only connection picks up
    the new rows without a uvicorn restart.

    Returns `embedded=false` for empty READMEs. A 503 means Ollama was
    unreachable; existing chunks (if any) are left untouched.
    """
    row = _lookup_with_body(conn, repo)
    readme = row["readme"] or ""
    doc = Doc(
        doc_id=row["repo"],
        title=row["name"] or row["repo"],
        version=f"{content_hash(readme)}-{CLEANER_VERSION}",
        text=strip_html(readme),
        section=None,
    )

    rag_conn = db.connect_rag_rw(db.GITHUB_RAG_DB)
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


@router.get("/readmes/{repo:path}", response_model=GithubReadme)
def get_readme(
    repo: str,
    conn: sqlite3.Connection = Depends(db.github),
) -> GithubReadme:
    """Return metadata for one repository's README by owner/repo slug."""
    return _row_to_readme(_lookup_meta(conn, repo))


add_chunks_route(
    router,
    opener=db.github_rag,
    source_name="github",
    indexer_script="github_readmes/github_readmes_index_rag.py",
    rag_db_path="data/github/github_readmes_rag.db",
)
add_doc_chunks_route(
    router,
    opener=db.github_rag,
    source_name="github",
    indexer_script="github_readmes/github_readmes_index_rag.py",
    rag_db_path="data/github/github_readmes_rag.db",
)
