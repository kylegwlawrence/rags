"""Read-only SQLite connection helpers for each datasource.

Each opener returns a process-wide cached connection opened with `mode=ro`
via the SQLite URI form. Read-only connections are safe to share across
threads, so `check_same_thread=False` is OK here.

If a downloader script rewrites a DB file while the API is running, restart
the server — the cached connection still points at the previous file.

A missing or unreadable DB file at open time raises HTTPException(503) so
routes return "Service Unavailable" rather than an opaque 500. /health
catches the exception and reports the failure per-database without 503-ing
the whole probe.
"""

import sqlite3
from pathlib import Path

import sqlite_vec
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

ARXIV_DB = DATA_DIR / "arxiv" / "arxiv.db"
ARXIV_RAG_DB = DATA_DIR / "arxiv" / "arxiv_rag.db"
FACTBOOK_DB = DATA_DIR / "factbook" / "factbook.db"
FACTBOOK_RAG_DB = DATA_DIR / "factbook" / "factbook_rag.db"
OPENALEX_DB = DATA_DIR / "openalex" / "openalex.db"
OPENALEX_RAG_DB = DATA_DIR / "openalex" / "openalex_rag.db"
GUTENBERG_DB = DATA_DIR / "gutenberg" / "gutenberg.db"
GUTENBERG_RAG_DB = DATA_DIR / "gutenberg" / "gutenberg_rag.db"
GUTENBERG_ROOT = DATA_DIR / "gutenberg"
SIMPLEWIKI_DB = DATA_DIR / "simplewiki" / "simplewiki.db"
SIMPLEWIKI_RAG_DB = DATA_DIR / "simplewiki" / "simplewiki_rag.db"


def _connect_ro(path: Path) -> sqlite3.Connection:
    """Open `path` read-only and configure dict-style row access.

    Missing-file / permission-denied / unreadable cases surface as 503 with the
    DB filename in the detail. Routers can catch their own per-query
    OperationalErrors (e.g. missing FTS table) separately.
    """
    uri = f"file:{path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    except sqlite3.OperationalError as e:
        raise HTTPException(
            status_code=503,
            detail=f"{path.name} not available: {e}",
        ) from e
    conn.row_factory = sqlite3.Row
    return conn


def _connect_ro_with_vec(path: Path) -> sqlite3.Connection:
    """Open `path` read-only with the sqlite-vec extension loaded.

    Used for the per-source `<source>_rag.db` files that include a `chunks_vec`
    virtual table. Reuses `_connect_ro`'s 503 translation for missing /
    unreadable DB files; also translates extension-load failures (rare: would
    mean sqlite_vec is missing or ABI-incompatible) to 503 rather than 500.
    """
    conn = _connect_ro(path)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (sqlite3.OperationalError, OSError) as e:
        raise HTTPException(
            status_code=503,
            detail=f"sqlite-vec extension failed to load for {path.name}: {e}",
        ) from e
    return conn


_arxiv: sqlite3.Connection | None = None
_arxiv_rag: sqlite3.Connection | None = None
_factbook: sqlite3.Connection | None = None
_factbook_rag: sqlite3.Connection | None = None
_openalex: sqlite3.Connection | None = None
_openalex_rag: sqlite3.Connection | None = None
_gutenberg: sqlite3.Connection | None = None
_gutenberg_rag: sqlite3.Connection | None = None
_simplewiki: sqlite3.Connection | None = None
_simplewiki_rag: sqlite3.Connection | None = None


def arxiv() -> sqlite3.Connection:
    """Cached read-only connection to arxiv.db (FTS index built by scripts/arxiv_index_fts.py)."""
    global _arxiv
    if _arxiv is None:
        _arxiv = _connect_ro(ARXIV_DB)
    return _arxiv


def arxiv_rag() -> sqlite3.Connection:
    """Cached read-only connection to arxiv_rag.db (built by scripts/arxiv_index_rag.py)."""
    global _arxiv_rag
    if _arxiv_rag is None:
        _arxiv_rag = _connect_ro_with_vec(ARXIV_RAG_DB)
    return _arxiv_rag


def factbook() -> sqlite3.Connection:
    """Cached read-only connection to factbook.db."""
    global _factbook
    if _factbook is None:
        _factbook = _connect_ro(FACTBOOK_DB)
    return _factbook


def factbook_rag() -> sqlite3.Connection:
    """Cached read-only connection to factbook_rag.db (built by scripts/factbook_index_rag.py)."""
    global _factbook_rag
    if _factbook_rag is None:
        _factbook_rag = _connect_ro_with_vec(FACTBOOK_RAG_DB)
    return _factbook_rag


def openalex() -> sqlite3.Connection:
    """Cached read-only connection to openalex.db."""
    global _openalex
    if _openalex is None:
        _openalex = _connect_ro(OPENALEX_DB)
    return _openalex


def openalex_rag() -> sqlite3.Connection:
    """Cached read-only connection to openalex_rag.db (built by scripts/openalex_index_rag.py)."""
    global _openalex_rag
    if _openalex_rag is None:
        _openalex_rag = _connect_ro_with_vec(OPENALEX_RAG_DB)
    return _openalex_rag


def gutenberg() -> sqlite3.Connection:
    """Cached read-only connection to gutenberg.db (built by scripts/gutenberg_index.py)."""
    global _gutenberg
    if _gutenberg is None:
        _gutenberg = _connect_ro(GUTENBERG_DB)
    return _gutenberg


def gutenberg_rag() -> sqlite3.Connection:
    """Cached read-only connection to gutenberg_rag.db (built by scripts/gutenberg_index_rag.py)."""
    global _gutenberg_rag
    if _gutenberg_rag is None:
        _gutenberg_rag = _connect_ro_with_vec(GUTENBERG_RAG_DB)
    return _gutenberg_rag


def simplewiki() -> sqlite3.Connection:
    """Cached read-only connection to simplewiki.db (built by scripts/simplewiki_parse.py)."""
    global _simplewiki
    if _simplewiki is None:
        _simplewiki = _connect_ro(SIMPLEWIKI_DB)
    return _simplewiki


def simplewiki_rag() -> sqlite3.Connection:
    """Cached read-only connection to simplewiki_rag.db (built by scripts/simplewiki_index_rag.py)."""
    global _simplewiki_rag
    if _simplewiki_rag is None:
        _simplewiki_rag = _connect_ro_with_vec(SIMPLEWIKI_RAG_DB)
    return _simplewiki_rag
