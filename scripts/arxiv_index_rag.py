#!/usr/bin/env python3
"""Index data/arxiv/arxiv.db into data/arxiv/arxiv_rag.db (chunks + FTS + vectors).

Re-runnable: skips docs whose `version` matches the previously-stored value in
`docs_meta`. Detects schema mismatch (old `paper_chunks` tables from upstream
local_wikipedia copies, or a stored embed_model/embedding_dim that differs
from the current code) and rebuilds the file from scratch.

Phase 2a embeds title + abstract only; full-HTML chunking is deferred to Phase 3.
After this script runs, restart uvicorn so the cached connection picks up the
new file (see `api/db.py` docstring).
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import arxiv_rag_extract  # noqa: E402
from rag import embedder, schema  # noqa: E402
from rag.chunker import chunk_doc  # noqa: E402

ARXIV_DB = REPO_ROOT / "data" / "arxiv" / "arxiv.db"
RAG_DB = REPO_ROOT / "data" / "arxiv" / "arxiv_rag.db"


def _needs_rebuild(path: Path) -> str | None:
    """Return a human-readable reason if the existing RAG DB is incompatible.

    Returns None when the DB is good to update incrementally. Checks for
    upstream-schema leftovers (`paper_chunks*`/`papers_meta`), missing target
    tables, or `_meta` keys that disagree with the current embedder constants.
    """
    if not path.exists():
        return None
    conn = sqlite3.connect(path)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if any(t.startswith("paper_chunks") for t in tables) or "papers_meta" in tables:
            return "legacy upstream schema (paper_chunks tables present)"
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N papers (testing).")
    parser.add_argument("--reset", action="store_true",
                        help="Wipe arxiv_rag.db and rebuild from scratch.")
    parser.add_argument("--batch", type=int, default=32,
                        help="Embedding batch size (chunks per HTTP call).")
    parser.add_argument("--ollama-url", default=embedder.OLLAMA_URL,
                        help="Override Ollama base URL.")
    parser.add_argument("--chunk-size", type=int, default=1600,
                        help="Max chars per chunk (default 1600).")
    args = parser.parse_args()

    if not ARXIV_DB.is_file():
        print(f"missing source DB: {ARXIV_DB}", file=sys.stderr)
        return 1
    RAG_DB.parent.mkdir(parents=True, exist_ok=True)

    reason = _needs_rebuild(RAG_DB)
    if args.reset or reason:
        if RAG_DB.exists():
            why = "user --reset" if args.reset and not reason else reason
            print(f"rebuilding {RAG_DB.name}: {why}", file=sys.stderr)
            RAG_DB.unlink()
            # WAL/SHM sidecar files would be stale against the deleted file.
            for sidecar in (RAG_DB.with_suffix(RAG_DB.suffix + "-wal"),
                            RAG_DB.with_suffix(RAG_DB.suffix + "-shm")):
                if sidecar.exists():
                    sidecar.unlink()

    arxiv_conn = sqlite3.connect(f"file:{ARXIV_DB}?mode=ro", uri=True)
    arxiv_conn.row_factory = sqlite3.Row
    rag_conn = schema.connect_rag(RAG_DB)

    schema.set_meta(rag_conn, "embed_model", embedder.EMBED_MODEL)
    schema.set_meta(rag_conn, "embedding_dim", str(embedder.EMBEDDING_DIM))
    schema.set_meta(rag_conn, "chunk_size", str(args.chunk_size))
    schema.set_meta(rag_conn, "chunk_overlap", "0")
    rag_conn.commit()

    existing_versions = {
        r["doc_id"]: r["version"]
        for r in rag_conn.execute("SELECT doc_id, version FROM docs_meta")
    }

    t0 = time.time()
    n_seen = n_skipped = n_new = n_updated = n_chunks = 0

    batch_docs: list[tuple] = []     # list of (Doc, list[chunk_dict])
    batch_texts: list[str] = []      # parallel list of strings to embed

    def flush() -> None:
        nonlocal n_chunks
        if not batch_texts:
            return
        vectors = embedder.embed_texts_batch(batch_texts, base_url=args.ollama_url)
        if len(vectors) != len(batch_texts):
            raise RuntimeError(
                f"embed returned {len(vectors)} vectors for {len(batch_texts)} inputs"
            )
        v_iter = iter(vectors)
        for doc, chunks in batch_docs:
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
                    (doc.doc_id, chunk["section"], chunk["chunk_index"],
                     chunk["text"], chunk["text_length"]),
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

    for doc in arxiv_rag_extract.iter_docs(arxiv_conn):
        if args.limit and n_seen >= args.limit:
            break
        n_seen += 1
        existing = existing_versions.get(doc.doc_id)
        if existing == doc.version:
            n_skipped += 1
            continue
        if existing is None:
            n_new += 1
        else:
            n_updated += 1
        chunks = chunk_doc(doc, chunk_size=args.chunk_size)
        if not chunks:
            continue
        batch_docs.append((doc, chunks))
        batch_texts.extend(
            embedder.format_document(doc.title, chunk["section"], chunk["text"])
            for chunk in chunks
        )
        if len(batch_texts) >= args.batch:
            flush()
    flush()

    print("rebuilding chunks_fts...", file=sys.stderr)
    rag_conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    rag_conn.commit()
    rag_conn.close()
    arxiv_conn.close()

    elapsed = time.time() - t0
    db_mb = RAG_DB.stat().st_size / (1024**2)
    print(f"Done in {elapsed:.1f}s. "
          f"Papers: {n_seen} seen, {n_new} new, {n_updated} updated, {n_skipped} unchanged. "
          f"Chunks embedded: {n_chunks}. DB size: {db_mb:.1f} MB.")
    print("(Restart uvicorn so api.db.arxiv_rag() reopens the new file.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
