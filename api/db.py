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

from rag.schema import connect_rag

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

# arxiv is sharded by parent category: data/arxiv/{parent}.db. The live set is
# whatever shard files are present (see `arxiv_shards()`), so unarchiving a
# category is just dropping its {parent}.db here and restarting.
ARXIV_DIR = DATA_DIR / "arxiv"
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
ECFR_RAG_DB = DATA_DIR / "ecfr" / "ecfr_rag.db"
OPENSTAX_DB = DATA_DIR / "openstax" / "openstax.db"
OPENSTAX_RAG_DB = DATA_DIR / "openstax" / "openstax_rag.db"
PDFS_DB = DATA_DIR / "pdfs" / "pdfs.db"
PDFS_RAG_DB = DATA_DIR / "pdfs" / "pdfs_rag.db"
# Original PDF files live in the drop folder; the /content route streams them
# straight from here, so the path is needed alongside the metadata DB.
PDFS_INCOMING = DATA_DIR / "pdfs" / "incoming"


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


def connect_rag_rw(path: Path) -> sqlite3.Connection:
    """Open a `<source>_rag.db` read-write for a single live embed.

    The cached `*_rag()` openers above are read-only and shared process-wide;
    this returns a FRESH read-write connection (sqlite-vec loaded, schema
    ensured, foreign keys on) that the caller must close. The narrow write path
    for the "embed this article" button is the only writer in the API — the
    read path stays read-only.

    Writes land on this connection; the cached read-only connection sees them
    immediately because the RAG DBs run in WAL mode (no uvicorn restart needed,
    unlike a full indexer-script rebuild). `busy_timeout` lets a concurrent
    embed wait for the single WAL writer slot rather than erroring out.
    """
    conn = connect_rag(path)
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def connect_rw(path: Path) -> sqlite3.Connection:
    """Open a plain read-write connection to a source DB for one narrow live write.

    Used by the SEC "Download full filing" route to write a freshly-fetched body
    onto its `filings` row. Returns a FRESH connection the caller must close;
    the cached read-only connection sees the committed UPDATE on its next query
    because it's an in-place single-row write to the same file (no inode swap),
    so no uvicorn restart is needed — unlike a full indexer rebuild that
    replaces the file. `busy_timeout` lets the write wait briefly for the lock
    rather than erroring if a read query is in flight.
    """
    try:
        conn = sqlite3.connect(path)
    except sqlite3.OperationalError as e:
        raise HTTPException(
            status_code=503,
            detail=f"{path.name} not available for writing: {e}",
        ) from e
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
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


_arxiv_shards: dict[str, sqlite3.Connection] | None = None
_federal_register: sqlite3.Connection | None = None
_federal_register_rag: sqlite3.Connection | None = None
_github: sqlite3.Connection | None = None
_github_rag: sqlite3.Connection | None = None
_arxiv_rag: sqlite3.Connection | None = None
_factbook: sqlite3.Connection | None = None
_factbook_rag: sqlite3.Connection | None = None
_openalex: sqlite3.Connection | None = None
_openalex_rag: sqlite3.Connection | None = None
_gutenberg: sqlite3.Connection | None = None
_gutenberg_rag: sqlite3.Connection | None = None
_simplewiki: sqlite3.Connection | None = None
_simplewiki_rag: sqlite3.Connection | None = None
_enwiki_rag: sqlite3.Connection | None = None
_pydocs: sqlite3.Connection | None = None
_pydocs_rag: sqlite3.Connection | None = None
_sec_edgar: sqlite3.Connection | None = None
_sec_edgar_rag: sqlite3.Connection | None = None
_worldbank: sqlite3.Connection | None = None
_geonames: sqlite3.Connection | None = None
_billstatus: sqlite3.Connection | None = None
_eurlex: sqlite3.Connection | None = None
_eurlex_rag: sqlite3.Connection | None = None
_ecfr: sqlite3.Connection | None = None
_ecfr_rag: sqlite3.Connection | None = None
_openstax: sqlite3.Connection | None = None
_openstax_rag: sqlite3.Connection | None = None
_pdfs: sqlite3.Connection | None = None
_pdfs_rag: sqlite3.Connection | None = None


def arxiv_shards() -> dict[str, sqlite3.Connection]:
    """Cached read-only connections to the per-category shards under data/arxiv/.

    Keyed by parent-category name (the file stem, e.g. ``"math"``). The set is
    discovered once at first call from ``data/arxiv/*.db`` (excluding the RAG
    DB), so unarchiving a category is just decompressing its ``{parent}.db``
    into ``data/arxiv/`` and restarting uvicorn — no code change.

    Each shard carries its own ``papers`` / ``authors`` / ``paper_authors``
    tables and its own ``papers_fts`` index (built by
    ``arxiv_index_fts.py --db data/arxiv/{parent}.db``). A paper lives in
    exactly one shard, so the router fans a query out across shards and merges.

    Raises 503 if no shard files are present (e.g. all categories archived).
    """
    global _arxiv_shards
    if _arxiv_shards is None:
        paths = sorted(
            p for p in ARXIV_DIR.glob("*.db") if p.name != ARXIV_RAG_DB.name
        )
        if not paths:
            raise HTTPException(
                status_code=503,
                detail="no arxiv category shards found in data/arxiv/ "
                "(all archived?) — decompress at least one {parent}.db there",
            )
        _arxiv_shards = {p.stem: _connect_ro(p) for p in paths}
    return _arxiv_shards


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


def enwiki_rag() -> sqlite3.Connection:
    """Cached read-only connection to enwiki_rag.db (populated on demand by the embed button)."""
    global _enwiki_rag
    if _enwiki_rag is None:
        _enwiki_rag = _connect_ro_with_vec(ENWIKI_RAG_DB)
    return _enwiki_rag


def pydocs() -> sqlite3.Connection:
    """Cached read-only connection to python_docs.db (FTS index built by scripts/python_docs/python_docs_index_fts.py)."""
    global _pydocs
    if _pydocs is None:
        _pydocs = _connect_ro(PYDOCS_DB)
    return _pydocs


def pydocs_rag() -> sqlite3.Connection:
    """Cached read-only connection to python_docs_rag.db (built by scripts/python_docs/python_docs_index_rag.py)."""
    global _pydocs_rag
    if _pydocs_rag is None:
        _pydocs_rag = _connect_ro_with_vec(PYDOCS_RAG_DB)
    return _pydocs_rag


def federal_register() -> sqlite3.Connection:
    """Cached read-only connection to federal_register.db (FTS built by scripts/federal_register/federal_register_index_fts.py)."""
    global _federal_register
    if _federal_register is None:
        _federal_register = _connect_ro(FEDERAL_REGISTER_DB)
    return _federal_register


def federal_register_rag() -> sqlite3.Connection:
    """Cached read-only connection to federal_register_rag.db (built by scripts/federal_register/federal_register_index_rag.py)."""
    global _federal_register_rag
    if _federal_register_rag is None:
        _federal_register_rag = _connect_ro_with_vec(FEDERAL_REGISTER_RAG_DB)
    return _federal_register_rag


def github() -> sqlite3.Connection:
    """Cached read-only connection to readmes.db (FTS built by scripts/github_readmes/github_readmes_index_fts.py)."""
    global _github
    if _github is None:
        _github = _connect_ro(GITHUB_DB)
    return _github


def github_rag() -> sqlite3.Connection:
    """Cached read-only connection to github_readmes_rag.db (built by scripts/github_readmes/github_readmes_index_rag.py)."""
    global _github_rag
    if _github_rag is None:
        _github_rag = _connect_ro_with_vec(GITHUB_RAG_DB)
    return _github_rag


def sec_edgar() -> sqlite3.Connection:
    """Cached read-only connection to sec_edgar.db (FTS built by scripts/sec_edgar/sec_edgar_index_fts.py)."""
    global _sec_edgar
    if _sec_edgar is None:
        _sec_edgar = _connect_ro(SEC_EDGAR_DB)
    return _sec_edgar


def sec_edgar_rag() -> sqlite3.Connection:
    """Cached read-only connection to sec_edgar_rag.db (built by scripts/sec_edgar/sec_edgar_index_rag.py)."""
    global _sec_edgar_rag
    if _sec_edgar_rag is None:
        _sec_edgar_rag = _connect_ro_with_vec(SEC_EDGAR_RAG_DB)
    return _sec_edgar_rag


def worldbank() -> sqlite3.Connection:
    """Cached read-only connection to worldbank.db (built by scripts/worldbank/worldbank_download.py)."""
    global _worldbank
    if _worldbank is None:
        _worldbank = _connect_ro(WORLDBANK_DB)
    return _worldbank


def geonames() -> sqlite3.Connection:
    """Cached read-only connection to geonames.db (FTS index built by scripts/geonames/geonames_index_fts.py)."""
    global _geonames
    if _geonames is None:
        _geonames = _connect_ro(GEONAMES_DB)
    return _geonames


def billstatus() -> sqlite3.Connection:
    """Cached read-only connection to billstatus.db (FTS index built by scripts/billstatus/billstatus_index_fts.py)."""
    global _billstatus
    if _billstatus is None:
        _billstatus = _connect_ro(BILLSTATUS_DB)
    return _billstatus


def eurlex() -> sqlite3.Connection:
    """Cached read-only connection to eurlex.db (built by scripts/eurlex/)."""
    global _eurlex
    if _eurlex is None:
        _eurlex = _connect_ro(EURLEX_DB)
    return _eurlex


def eurlex_rag() -> sqlite3.Connection:
    """Cached read-only connection to eurlex_rag.db (built by scripts/eurlex/eurlex_index_rag.py)."""
    global _eurlex_rag
    if _eurlex_rag is None:
        _eurlex_rag = _connect_ro_with_vec(EURLEX_RAG_DB)
    return _eurlex_rag


def ecfr() -> sqlite3.Connection:
    """Cached read-only connection to ecfr.db (FTS index built by scripts/ecfr/ecfr_index_fts.py)."""
    global _ecfr
    if _ecfr is None:
        _ecfr = _connect_ro(ECFR_DB)
    return _ecfr


def ecfr_rag() -> sqlite3.Connection:
    """Cached read-only connection to ecfr_rag.db (populated on demand by the embed button)."""
    global _ecfr_rag
    if _ecfr_rag is None:
        _ecfr_rag = _connect_ro_with_vec(ECFR_RAG_DB)
    return _ecfr_rag


def openstax() -> sqlite3.Connection:
    """Cached read-only connection to openstax.db (FTS index built by scripts/openstax/openstax_index_fts.py)."""
    global _openstax
    if _openstax is None:
        _openstax = _connect_ro(OPENSTAX_DB)
    return _openstax


def openstax_rag() -> sqlite3.Connection:
    """Cached read-only connection to openstax_rag.db (built by scripts/openstax/openstax_index_rag.py or the embed button)."""
    global _openstax_rag
    if _openstax_rag is None:
        _openstax_rag = _connect_ro_with_vec(OPENSTAX_RAG_DB)
    return _openstax_rag


def pdfs() -> sqlite3.Connection:
    """Cached read-only connection to pdfs.db (built by scripts/pdfs/pdfs_ingest.py)."""
    global _pdfs
    if _pdfs is None:
        _pdfs = _connect_ro(PDFS_DB)
    return _pdfs


def pdfs_rag() -> sqlite3.Connection:
    """Cached read-only connection to pdfs_rag.db (built by scripts/pdfs/pdfs_index_rag.py)."""
    global _pdfs_rag
    if _pdfs_rag is None:
        _pdfs_rag = _connect_ro_with_vec(PDFS_RAG_DB)
    return _pdfs_rag
