"""RAG database schema and connection helper.

Uniform across all sources. Each source has its own `<source>_rag.db` file
with identical table structure; source identity comes from the file path,
not the table name.

Used by the indexer scripts in read-write mode (sqlite-vec loaded, schema
ensured). The API reads through `api.db._connect_ro_with_vec` instead — same
extension load, but the URI form `file:...?mode=ro` prevents writes.
"""

import pathlib
import sqlite3

import sqlite_vec


def connect_rag(path: pathlib.Path) -> sqlite3.Connection:
    """Open a RAG DB read-write with sqlite-vec loaded and schema ensured.

    Enables `PRAGMA foreign_keys = ON` so the `chunks.doc_id REFERENCES
    docs_meta(doc_id)` constraint is actually enforced — protects against
    future "I forgot the delete order" bugs. The existing indexer already
    deletes chunks before docs_meta so nothing breaks today.

    Args:
        path: Filesystem path to the `<source>_rag.db` file. Parent directories
            must exist; the SQLite file is created if absent.

    Returns:
        sqlite3.Connection with Row factory, sqlite-vec extension loaded, FK
        enforcement on, and all RAG tables guaranteed to exist.
    """
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    create_rag_schema(conn)
    return conn


def create_rag_schema(conn: sqlite3.Connection) -> None:
    """Create chunks/chunks_fts/chunks_vec/docs_meta/_meta if absent. Idempotent."""
    conn.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS _meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS docs_meta (
            doc_id      TEXT PRIMARY KEY,
            version     TEXT NOT NULL,
            title       TEXT NOT NULL,
            chunk_count INTEGER NOT NULL,
            indexed_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id      TEXT NOT NULL REFERENCES docs_meta(doc_id),
            section     TEXT,
            chunk_index INTEGER NOT NULL DEFAULT 0,
            text        TEXT NOT NULL,
            text_length INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            text,
            content='chunks',
            content_rowid='chunk_id',
            tokenize='porter unicode61'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
            chunk_id INTEGER PRIMARY KEY,
            embedding FLOAT[768]
        );
    """)
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """Read a `_meta` key. Returns None if the key isn't set.

    Works with or without `row_factory = sqlite3.Row` on the caller's connection.
    """
    row = conn.execute("SELECT value FROM _meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert a `_meta` key. Caller is responsible for commit()."""
    conn.execute(
        "INSERT OR REPLACE INTO _meta(key, value) VALUES (?, ?)",
        (key, value),
    )


def delete_doc_chunks(
    conn: sqlite3.Connection,
    doc_id: str,
    *,
    sync_fts: bool = False,
) -> None:
    """Remove a doc's chunks, vectors, optional FTS entries, and docs_meta row.

    Two callers, two FTS strategies:

    * `rag.embed_one.embed_doc` calls with ``sync_fts=True`` so a live single-
      doc embed keeps the FTS index current — it issues per-chunk
      ``chunks_fts('delete', rowid, text)`` rows before dropping the
      backing ``chunks`` rows.
    * `rag.indexer._run.flush()` calls with ``sync_fts=False`` because the
      batch indexer does one ``chunks_fts('rebuild')`` at the end of the run;
      per-doc FTS deletes would just be wasted work.

    Order matters: ``chunks_vec`` is a sqlite-vec virtual table whose rows
    aren't reached by the ``chunks.doc_id`` foreign-key cascade, so they
    must be cleared explicitly *before* the backing chunks rows go away.
    Caller is responsible for ``commit()``.
    """
    if sync_fts:
        rows = conn.execute(
            "SELECT chunk_id, text FROM chunks WHERE doc_id = ?", (doc_id,)
        ).fetchall()
        if rows:
            ids = [r["chunk_id"] for r in rows]
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"DELETE FROM chunks_vec WHERE chunk_id IN ({placeholders})", ids
            )
            for r in rows:
                conn.execute(
                    "INSERT INTO chunks_fts(chunks_fts, rowid, text) "
                    "VALUES('delete', ?, ?)",
                    (r["chunk_id"], r["text"]),
                )
            conn.execute(
                f"DELETE FROM chunks WHERE chunk_id IN ({placeholders})", ids
            )
    else:
        # Subquery form skips the SELECT round-trip the batch indexer doesn't need.
        conn.execute(
            "DELETE FROM chunks_vec WHERE chunk_id IN "
            "(SELECT chunk_id FROM chunks WHERE doc_id = ?)",
            (doc_id,),
        )
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM docs_meta WHERE doc_id = ?", (doc_id,))
