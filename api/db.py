"""Read-only SQLite connection helpers for each datasource.

Each opener returns a process-wide cached connection opened with `mode=ro`
via the SQLite URI form. Read-only connections are safe to share across
threads, so `check_same_thread=False` is OK here.

If a downloader script rewrites a DB file while the API is running, restart
the server — the cached connection still points at the previous file.
"""

import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

ARXIV_DB = DATA_DIR / "arxiv" / "arxiv.db"
FACTBOOK_DB = DATA_DIR / "factbook" / "factbook.db"
OPENALEX_DB = DATA_DIR / "openalex" / "openalex.db"
GUTENBERG_DB = DATA_DIR / "gutenberg" / "gutenberg.db"
GUTENBERG_ROOT = DATA_DIR / "gutenberg"


def _connect_ro(path: Path) -> sqlite3.Connection:
    """Open `path` read-only and configure dict-style row access."""
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


_arxiv: sqlite3.Connection | None = None
_factbook: sqlite3.Connection | None = None
_openalex: sqlite3.Connection | None = None
_gutenberg: sqlite3.Connection | None = None


def arxiv() -> sqlite3.Connection:
    """Cached read-only connection to arxiv.db (FTS index built by scripts/arxiv_index_fts.py)."""
    global _arxiv
    if _arxiv is None:
        _arxiv = _connect_ro(ARXIV_DB)
    return _arxiv


def factbook() -> sqlite3.Connection:
    """Cached read-only connection to factbook.db."""
    global _factbook
    if _factbook is None:
        _factbook = _connect_ro(FACTBOOK_DB)
    return _factbook


def openalex() -> sqlite3.Connection:
    """Cached read-only connection to openalex.db."""
    global _openalex
    if _openalex is None:
        _openalex = _connect_ro(OPENALEX_DB)
    return _openalex


def gutenberg() -> sqlite3.Connection:
    """Cached read-only connection to gutenberg.db (built by scripts/gutenberg_index.py)."""
    global _gutenberg
    if _gutenberg is None:
        _gutenberg = _connect_ro(GUTENBERG_DB)
    return _gutenberg
