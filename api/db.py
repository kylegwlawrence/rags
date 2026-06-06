"""Read-only SQLite connection helpers for each datasource.

Each opener is cached via `functools.cache` — it returns the same process-wide
connection on every call. Read-only connections are safe to share across threads
(`check_same_thread=False`). Restart the server after any indexer/downloader run;
cached connections point at the inode at open-time. Missing/unreadable DB files
raise HTTPException(503) at first access.
"""

import sqlite3
from functools import cache
from pathlib import Path

import sqlite_vec
from fastapi import HTTPException

from rag.schema import connect_rag

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

# arxiv lives outside the repo (~80 GB, too large for /home).
ARXIV_DB = Path("/datasets/arxiv/arxiv.db")
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
ENWIKI_DB = DATA_DIR / "enwiki" / "enwiki.db"
ENWIKI_RAG_DB = DATA_DIR / "enwiki" / "enwiki_rag.db"
PYDOCS_DB = DATA_DIR / "pydocs" / "python_docs.db"
PYDOCS_RAG_DB = DATA_DIR / "pydocs" / "python_docs_rag.db"
FEDERAL_REGISTER_DB = DATA_DIR / "federal_register" / "federal_register.db"
FEDERAL_REGISTER_RAG_DB = DATA_DIR / "federal_register" / "federal_register_rag.db"
GITHUB_DB = DATA_DIR / "github" / "readmes.db"
GITHUB_RAG_DB = DATA_DIR / "github" / "github_readmes_rag.db"
SEC_EDGAR_DB = DATA_DIR / "sec_edgar" / "sec_edgar.db"
SEC_EDGAR_RAG_DB = DATA_DIR / "sec_edgar" / "sec_edgar_rag.db"
WORLDBANK_DB = DATA_DIR / "worldbank" / "worldbank.db"
GEONAMES_DB = DATA_DIR / "geonames" / "geonames.db"
BILLSTATUS_DB = DATA_DIR / "billstatus" / "billstatus.db"
EURLEX_DB = DATA_DIR / "eurlex" / "eurlex.db"
EURLEX_RAG_DB = DATA_DIR / "eurlex" / "eurlex_rag.db"
ECFR_DB = DATA_DIR / "ecfr" / "ecfr.db"
JUSTICE_CANADA_DB = DATA_DIR / "justice_canada" / "justice_canada.db"
ECFR_RAG_DB = DATA_DIR / "ecfr" / "ecfr_rag.db"
OPENSTAX_DB = DATA_DIR / "openstax" / "openstax.db"
OPENSTAX_RAG_DB = DATA_DIR / "openstax" / "openstax_rag.db"
PDFS_DB = DATA_DIR / "pdfs" / "pdfs.db"
PDFS_RAG_DB = DATA_DIR / "pdfs" / "pdfs_rag.db"
# Original PDFs stay in the drop folder; /content streams them from here.
PDFS_INCOMING = DATA_DIR / "pdfs" / "incoming"


def _connect_ro(path: Path) -> sqlite3.Connection:
    """Open `path` read-only; raises HTTPException(503) if missing or unreadable."""
    uri = f"file:{path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    except sqlite3.OperationalError as e:
        raise HTTPException(
            status_code=503, detail=f"{path.name} not available: {e}"
        ) from e
    conn.row_factory = sqlite3.Row
    return conn


def _connect_ro_with_vec(path: Path) -> sqlite3.Connection:
    """Open `path` read-only with the sqlite-vec extension loaded."""
    conn = _connect_ro(path)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (sqlite3.OperationalError, OSError) as e:
        raise HTTPException(
            status_code=503,
            detail=f"sqlite-vec failed to load for {path.name}: {e}",
        ) from e
    return conn


def connect_rag_rw(path: Path) -> sqlite3.Connection:
    """Open a rag DB read-write for a single live embed; caller must close.

    Returns a fresh (uncached) connection with sqlite-vec loaded, schema
    ensured, and a 10-second busy timeout for concurrent embed requests.
    Writes land here; the cached read-only connection sees them immediately
    because rag DBs run in WAL mode.
    """
    conn = connect_rag(path)
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def connect_rw(path: Path) -> sqlite3.Connection:
    """Open a source DB read-write for a narrow in-place write; caller must close.

    Used for single-row UPDATEs (e.g. SEC filing body download). The cached
    read-only connection sees the commit immediately — no inode swap, no restart.
    """
    try:
        conn = sqlite3.connect(path)
    except sqlite3.OperationalError as e:
        raise HTTPException(
            status_code=503, detail=f"{path.name} not available for writing: {e}"
        ) from e
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


# --- Cached read-only openers ---
# Each function is called as a FastAPI Depends; @cache makes it a process-wide
# singleton. Failed opens (HTTPException) are not cached, so a missing DB retries
# on the next request.

@cache
def arxiv() -> sqlite3.Connection:
    """arxiv.db — lives at ARXIV_DB (outside the repo; too large for /home)."""
    return _connect_ro(ARXIV_DB)


@cache
def arxiv_rag() -> sqlite3.Connection:
    return _connect_ro_with_vec(ARXIV_RAG_DB)


@cache
def factbook() -> sqlite3.Connection:
    return _connect_ro(FACTBOOK_DB)


@cache
def factbook_rag() -> sqlite3.Connection:
    return _connect_ro_with_vec(FACTBOOK_RAG_DB)


@cache
def openalex() -> sqlite3.Connection:
    return _connect_ro(OPENALEX_DB)


@cache
def openalex_rag() -> sqlite3.Connection:
    return _connect_ro_with_vec(OPENALEX_RAG_DB)


@cache
def gutenberg() -> sqlite3.Connection:
    return _connect_ro(GUTENBERG_DB)


@cache
def gutenberg_rag() -> sqlite3.Connection:
    return _connect_ro_with_vec(GUTENBERG_RAG_DB)


@cache
def simplewiki() -> sqlite3.Connection:
    return _connect_ro(SIMPLEWIKI_DB)


@cache
def simplewiki_rag() -> sqlite3.Connection:
    return _connect_ro_with_vec(SIMPLEWIKI_RAG_DB)


@cache
def enwiki() -> sqlite3.Connection:
    """enwiki.db — ~263 GB, served directly from disk (old raspberrypi6 proxy is gone)."""
    return _connect_ro(ENWIKI_DB)


@cache
def enwiki_rag() -> sqlite3.Connection:
    return _connect_ro_with_vec(ENWIKI_RAG_DB)


@cache
def pydocs() -> sqlite3.Connection:
    return _connect_ro(PYDOCS_DB)


@cache
def pydocs_rag() -> sqlite3.Connection:
    return _connect_ro_with_vec(PYDOCS_RAG_DB)


@cache
def federal_register() -> sqlite3.Connection:
    return _connect_ro(FEDERAL_REGISTER_DB)


@cache
def federal_register_rag() -> sqlite3.Connection:
    return _connect_ro_with_vec(FEDERAL_REGISTER_RAG_DB)


@cache
def github() -> sqlite3.Connection:
    return _connect_ro(GITHUB_DB)


@cache
def github_rag() -> sqlite3.Connection:
    return _connect_ro_with_vec(GITHUB_RAG_DB)


@cache
def sec_edgar() -> sqlite3.Connection:
    return _connect_ro(SEC_EDGAR_DB)


@cache
def sec_edgar_rag() -> sqlite3.Connection:
    return _connect_ro_with_vec(SEC_EDGAR_RAG_DB)


@cache
def worldbank() -> sqlite3.Connection:
    return _connect_ro(WORLDBANK_DB)


@cache
def geonames() -> sqlite3.Connection:
    return _connect_ro(GEONAMES_DB)


@cache
def billstatus() -> sqlite3.Connection:
    return _connect_ro(BILLSTATUS_DB)


@cache
def eurlex() -> sqlite3.Connection:
    return _connect_ro(EURLEX_DB)


@cache
def eurlex_rag() -> sqlite3.Connection:
    return _connect_ro_with_vec(EURLEX_RAG_DB)


@cache
def ecfr() -> sqlite3.Connection:
    return _connect_ro(ECFR_DB)


@cache
def ecfr_rag() -> sqlite3.Connection:
    return _connect_ro_with_vec(ECFR_RAG_DB)


@cache
def openstax() -> sqlite3.Connection:
    return _connect_ro(OPENSTAX_DB)


@cache
def openstax_rag() -> sqlite3.Connection:
    return _connect_ro_with_vec(OPENSTAX_RAG_DB)


@cache
def pdfs() -> sqlite3.Connection:
    return _connect_ro(PDFS_DB)


@cache
def pdfs_rag() -> sqlite3.Connection:
    return _connect_ro_with_vec(PDFS_RAG_DB)


@cache
def justice_canada() -> sqlite3.Connection:
    return _connect_ro(JUSTICE_CANADA_DB)
