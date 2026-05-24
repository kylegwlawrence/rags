import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._fts import translate_table_errors
from api.models import GithubReadme, Page

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
