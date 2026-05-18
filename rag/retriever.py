"""Hybrid dense + sparse retrieval for the RAG pipeline.

Dense search: sqlite-vec ANN on chunk embeddings.
Sparse search: FTS5 BM25 on chunk text, AND-of-quoted-words for natural-language recall.
Fusion: Reciprocal Rank Fusion (RRF).

Falls back to sparse-only with `used_dense=False` when Ollama is unreachable —
the API surfaces this flag to the client. Missing `chunks_fts` / `chunks_vec`
tables propagate `sqlite3.OperationalError` to the caller, which translates
it to a 503 per `WORK.md` §2.2.
"""

import sqlite3

import httpx

from rag import Hit, RetrievalResult, embedder


def is_operational_error(err: sqlite3.OperationalError) -> bool:
    """True when the error means a missing table / unreadable DB file, not bad SQL.

    Shared by the retriever (decides whether to swallow vs. propagate) and the
    route handlers (decide 503 vs. 400). SQLite doesn't expose distinct error
    codes for these cases; the message strings are the only available signal.
    """
    msg = str(err)
    return "no such table" in msg or "unable to open database file" in msg


def retrieve(
    query: str,
    rag_conn: sqlite3.Connection,
    *,
    top_k: int = 20,
    candidate_k: int = 50,
    rrf_k: int = 60,
    ollama_url: str = embedder.OLLAMA_URL,
) -> RetrievalResult:
    """Run hybrid dense+sparse retrieval, merge with RRF, hydrate hits.

    Args:
        query: Natural-language query. Empty/whitespace-only → empty result.
        rag_conn: RAG DB connection with sqlite-vec loaded.
        top_k: Final hit count after RRF merging.
        candidate_k: Number of candidates pulled from each side before merging.
        rrf_k: RRF smoothing constant; standard value is 60.
        ollama_url: Override the embedder's default URL (useful for tests).

    Returns:
        RetrievalResult with `hits` sorted by descending RRF score and
        `used_dense` flag indicating whether Ollama embedding succeeded.
    """
    if not query.strip():
        return RetrievalResult(hits=[], used_dense=False)

    sparse = _sparse_search(query, rag_conn, candidate_k)

    dense: list[tuple[int, float]] = []
    used_dense = False
    try:
        vec = embedder.embed_text(embedder.format_query(query), base_url=ollama_url)
        dense = _dense_search(vec, rag_conn, candidate_k)
        used_dense = True
    except httpx.HTTPError:
        pass  # sparse-only fallback

    merged = _rrf_merge(dense, sparse, k=rrf_k)[:top_k]
    if not merged:
        return RetrievalResult(hits=[], used_dense=used_dense)

    chunk_ids = [cid for cid, _ in merged]
    score_map = {cid: score for cid, score in merged}
    hydrated = _fetch_chunks(chunk_ids, score_map, rag_conn)
    hits = [hydrated[cid] for cid in chunk_ids if cid in hydrated]
    return RetrievalResult(hits=hits, used_dense=used_dense)


def _dense_search(
    query_embedding: list[float],
    rag_conn: sqlite3.Connection,
    k: int,
) -> list[tuple[int, float]]:
    """ANN search over chunks_vec. Returns (chunk_id, distance) ordered ascending."""
    packed = embedder.pack_embedding(query_embedding)
    rows = rag_conn.execute(
        "SELECT chunk_id, distance FROM chunks_vec WHERE embedding MATCH ? AND k = ?",
        (packed, k),
    ).fetchall()
    return [(r["chunk_id"], r["distance"]) for r in rows]


def _sparse_search(
    query: str,
    rag_conn: sqlite3.Connection,
    k: int,
) -> list[tuple[int, float]]:
    """FTS5 BM25 search over chunks_fts.

    Each query word is wrapped in double-quotes individually so FTS5 applies
    AND-of-terms across the unstemmed forms (the porter tokenizer still
    matches via stem). Phrase matching across the whole query is intentionally
    avoided — natural-language queries rarely match verbatim phrases.

    Malformed FTS5 syntax returns an empty list rather than raising; missing-
    table errors propagate to the caller for 503 translation.
    """
    words = query.split()
    if not words:
        return []
    escaped = " ".join('"' + w.replace('"', "") + '"' for w in words)
    try:
        rows = rag_conn.execute(
            "SELECT rowid, rank FROM chunks_fts WHERE chunks_fts MATCH ? LIMIT ?",
            (escaped, k),
        ).fetchall()
        return [(r["rowid"], r["rank"]) for r in rows]
    except sqlite3.OperationalError as e:
        if is_operational_error(e):
            raise
        return []  # bad FTS syntax → no sparse hits, dense (if up) still tried


def _rrf_merge(
    dense: list[tuple[int, float]],
    sparse: list[tuple[int, float]],
    k: int = 60,
) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion across two ordered result lists.

    Each chunk's score is `sum(1 / (k + rank))` over every list it appears in.
    """
    scores: dict[int, float] = {}
    for rank, (chunk_id, _) in enumerate(dense, 1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    for rank, (chunk_id, _) in enumerate(sparse, 1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def _fetch_chunks(
    chunk_ids: list[int],
    score_map: dict[int, float],
    rag_conn: sqlite3.Connection,
) -> dict[int, Hit]:
    """Hydrate chunk IDs into Hit objects joined with docs_meta."""
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" * len(chunk_ids))
    rows = rag_conn.execute(
        f"SELECT c.chunk_id, c.doc_id, c.section, c.chunk_index, c.text, c.text_length, dm.title "
        f"FROM chunks c JOIN docs_meta dm USING(doc_id) "
        f"WHERE c.chunk_id IN ({placeholders})",
        chunk_ids,
    ).fetchall()
    return {
        r["chunk_id"]: Hit(
            chunk_id=r["chunk_id"],
            doc_id=r["doc_id"],
            title=r["title"],
            section=r["section"],
            chunk_index=r["chunk_index"],
            text=r["text"],
            text_length=r["text_length"],
            score=score_map.get(r["chunk_id"], 0.0),
        )
        for r in rows
    }
