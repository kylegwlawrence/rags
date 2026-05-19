"""Re-runnable RAG indexer skeleton used by every per-source script.

Each `scripts/<source>_index_rag.py` is a ~40-line wrapper that parses CLI
flags, builds a per-source extractor closure, and calls `run_indexer(...)`.
This module owns everything that's identical across sources:

- schema-mismatch / model-mismatch detection → wipe + rebuild
- WAL/SHM sidecar cleanup
- `_meta` stamping (embed_model, embedding_dim, chunk_size, overlap, extras)
- existing version dict load
- the batched embed-and-write loop (chunks_vec → chunks → docs_meta, in that
  order so the FK constraint stays satisfied and no orphan vectors accumulate)
- periodic progress prints every flush boundary
- FTS rebuild at end
- `n_new` / `n_updated` accounting (post-chunks check)

Per-source variation comes through the `extractor`, `legacy_table_prefixes`,
`extra_meta`, and `source_label` parameters — see the per-source scripts for
usage.
"""

import sqlite3
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from rag import Doc, embedder, schema
from rag.chunker import chunk_doc


def run_indexer(
    *,
    source_db_path: Path,
    rag_db_path: Path,
    extractor: Callable[[sqlite3.Connection], Iterable[Doc]],
    chunk_fn: Callable[..., list[dict]] = chunk_doc,
    reset: bool = False,
    batch: int = 32,
    ollama_url: str = embedder.OLLAMA_URL,
    chunk_size: int = 1600,
    chunk_overlap: int = 0,
    extra_meta: dict[str, str] | None = None,
    legacy_table_prefixes: tuple[str, ...] = (),
    source_label: str = "docs",
) -> int:
    """Build / update `<source>_rag.db` from a per-source extractor.

    Args:
        source_db_path: Read-only source SQLite file (e.g. `data/arxiv/arxiv.db`).
        rag_db_path: Target `<source>_rag.db` to write.
        extractor: Callable taking the source `sqlite3.Connection` and yielding
            `Doc` instances. Per-source script applies any sampling / limit
            inside this closure.
        reset: When True, wipe `rag_db_path` and rebuild from scratch.
        batch: Embedding batch size (chunks per Ollama HTTP call).
        ollama_url: Override the embedder's default URL.
        chunk_size, chunk_overlap: Per-source chunker config.
        extra_meta: Additional `_meta` rows stored alongside the standard keys
            (e.g. `{"source_limit": "5000"}` for openalex).
        legacy_table_prefixes: Trigger an auto-rebuild if any table starting
            with one of these names exists (e.g. `("paper_chunks",)` for arxiv).
        source_label: Used in the summary line (`"papers"`, `"works"`, etc.).

    Returns:
        Process exit code: 0 on success, 1 if the source DB is missing.
    """
    if not source_db_path.is_file():
        print(f"missing source DB: {source_db_path}", file=sys.stderr)
        return 1
    rag_db_path.parent.mkdir(parents=True, exist_ok=True)

    reason = _needs_rebuild(rag_db_path, legacy_table_prefixes)
    if reset or reason:
        if rag_db_path.exists():
            why = "user --reset" if reset and not reason else reason
            print(f"rebuilding {rag_db_path.name}: {why}", file=sys.stderr)
            rag_db_path.unlink()
            for sidecar in (
                rag_db_path.with_suffix(rag_db_path.suffix + "-wal"),
                rag_db_path.with_suffix(rag_db_path.suffix + "-shm"),
            ):
                if sidecar.exists():
                    sidecar.unlink()

    source_conn = sqlite3.connect(f"file:{source_db_path}?mode=ro", uri=True)
    source_conn.row_factory = sqlite3.Row
    rag_conn = schema.connect_rag(rag_db_path)
    try:
        return _run(
            source_conn=source_conn,
            rag_conn=rag_conn,
            rag_db_path=rag_db_path,
            extractor=extractor,
            chunk_fn=chunk_fn,
            batch=batch,
            ollama_url=ollama_url,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            extra_meta=extra_meta,
            source_label=source_label,
        )
    finally:
        rag_conn.close()
        source_conn.close()


def _run(
    *,
    source_conn: sqlite3.Connection,
    rag_conn: sqlite3.Connection,
    rag_db_path: Path,
    extractor: Callable[[sqlite3.Connection], Iterable[Doc]],
    chunk_fn: Callable[..., list[dict]],
    batch: int,
    ollama_url: str,
    chunk_size: int,
    chunk_overlap: int,
    extra_meta: dict[str, str] | None,
    source_label: str,
) -> int:
    """Inner body of `run_indexer`; called within the try/finally that owns the connections."""
    schema.set_meta(rag_conn, "embed_model", embedder.EMBED_MODEL)
    schema.set_meta(rag_conn, "embedding_dim", str(embedder.EMBEDDING_DIM))
    schema.set_meta(rag_conn, "chunk_size", str(chunk_size))
    schema.set_meta(rag_conn, "chunk_overlap", str(chunk_overlap))
    if extra_meta:
        for k, v in extra_meta.items():
            schema.set_meta(rag_conn, k, v)
    rag_conn.commit()

    existing_versions = {
        r["doc_id"]: r["version"]
        for r in rag_conn.execute("SELECT doc_id, version FROM docs_meta")
    }

    t0 = time.time()
    n_seen = n_skipped = n_new = n_updated = n_chunks = 0

    batch_docs: list[tuple[Doc, list[dict]]] = []
    batch_texts: list[str] = []

    def flush() -> None:
        nonlocal n_chunks
        if not batch_texts:
            return
        vectors = embedder.embed_texts_batch(batch_texts, base_url=ollama_url)
        if len(vectors) != len(batch_texts):
            raise RuntimeError(
                f"embed returned {len(vectors)} vectors for {len(batch_texts)} inputs"
            )
        v_iter = iter(vectors)
        for doc, chunks in batch_docs:
            # Order matters: chunks_vec is a sqlite-vec virtual table and FK
            # cascade doesn't reach it, so its rows must be cleared explicitly
            # before the chunks rows that reference them go away.
            rag_conn.execute(
                "DELETE FROM chunks_vec WHERE chunk_id IN "
                "(SELECT chunk_id FROM chunks WHERE doc_id = ?)",
                (doc.doc_id,),
            )
            rag_conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc.doc_id,))
            rag_conn.execute("DELETE FROM docs_meta WHERE doc_id = ?", (doc.doc_id,))
            rag_conn.execute(
                "INSERT INTO docs_meta(doc_id, version, title, chunk_count, indexed_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (doc.doc_id, doc.version, doc.title, len(chunks)),
            )
            for chunk in chunks:
                cur = rag_conn.execute(
                    "INSERT INTO chunks(doc_id, section, chunk_index, text, text_length) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        doc.doc_id,
                        chunk["section"],
                        chunk["chunk_index"],
                        chunk["text"],
                        chunk["text_length"],
                    ),
                )
                chunk_id = cur.lastrowid
                vec = next(v_iter)
                rag_conn.execute(
                    "INSERT INTO chunks_vec(chunk_id, embedding) VALUES (?, ?)",
                    (chunk_id, embedder.pack_embedding(vec)),
                )
                n_chunks += 1
        rag_conn.commit()
        batch_docs.clear()
        batch_texts.clear()

    for doc in extractor(source_conn):
        n_seen += 1
        existing = existing_versions.get(doc.doc_id)
        if existing == doc.version:
            n_skipped += 1
            continue
        chunks = chunk_fn(doc, chunk_size=chunk_size, overlap=chunk_overlap)
        if not chunks:
            # Empty doc — don't count toward n_new/n_updated or it'd re-count
            # on every subsequent run (since no docs_meta row gets written).
            continue
        if existing is None:
            n_new += 1
        else:
            n_updated += 1
        batch_docs.append((doc, chunks))
        batch_texts.extend(
            embedder.format_document(doc.title, chunk["section"], chunk["text"])
            for chunk in chunks
        )
        if len(batch_texts) >= batch:
            flush()
            print(
                f"  {n_seen} seen / {n_new} new / {n_updated} updated / {n_skipped} unchanged",
                file=sys.stderr,
            )
    flush()

    print("rebuilding chunks_fts...", file=sys.stderr)
    rag_conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    rag_conn.commit()

    elapsed = time.time() - t0
    db_mb = rag_db_path.stat().st_size / (1024**2)
    print(
        f"Done in {elapsed:.1f}s. "
        f"{source_label.capitalize()}: {n_seen} seen, {n_new} new, "
        f"{n_updated} updated, {n_skipped} unchanged. "
        f"Chunks embedded: {n_chunks}. DB size: {db_mb:.1f} MB."
    )
    print(f"(Restart uvicorn so api.db.{rag_db_path.stem}() reopens the new file.)")
    return 0


def _needs_rebuild(
    path: Path, legacy_table_prefixes: tuple[str, ...]
) -> str | None:
    """Detect upstream-schema leftovers, missing target tables, or model mismatch."""
    if not path.exists():
        return None
    conn = sqlite3.connect(path)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for prefix in legacy_table_prefixes:
            if any(t.startswith(prefix) for t in tables):
                return f"legacy schema ({prefix}* tables present)"
        if "chunks" not in tables or "docs_meta" not in tables:
            return "missing required tables"
        stored_model = schema.get_meta(conn, "embed_model")
        if stored_model and stored_model != embedder.EMBED_MODEL:
            return f"embed_model mismatch ({stored_model!r} vs {embedder.EMBED_MODEL!r})"
        stored_dim = schema.get_meta(conn, "embedding_dim")
        if stored_dim and stored_dim != str(embedder.EMBEDDING_DIM):
            return f"embedding_dim mismatch ({stored_dim!r} vs {embedder.EMBEDDING_DIM})"
    finally:
        conn.close()
    return None
