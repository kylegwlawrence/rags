"""Hybrid dense + sparse retrieval for the RAG pipeline.

Dense search: sqlite-vec ANN on chunk embeddings.
Sparse search: FTS5 BM25 on chunk text, AND-of-quoted-words for natural-language recall.
Fusion: Reciprocal Rank Fusion (RRF).

Falls back to sparse-only with `used_dense=False` when Ollama is unreachable —
the API surfaces this flag to the client. Missing `chunks_fts` / `chunks_vec`
tables propagate `sqlite3.OperationalError` to the caller, which translates
it to a 503 per `WORK.md` §2.2.
"""

import json
import sqlite3

import httpx

from rag import Hit, RetrievalResult, embedder

# sqlite-vec 0.1.9 rejects a KNN `k` above this hard cap ("k value in knn query
# too large, provided N and the limit is 4096"), so every KNN we issue clamps to
# it. This is also what made `candidate_k` non-monotonic before: a large
# `candidate_k` pushed `k` past the cap and the query threw instead of returning
# more candidates.
_VEC_KNN_MAX = 4096

# When a `doc_id` allowlist is active the dense side can't filter inside the
# sqlite-vec KNN scan (the `doc_id` column lives on `chunks`, not the vec table).
# For a scope of at most this many chunks we score every in-scope chunk directly
# (exact, and cheap because the scan is bounded by the scope); above it we fall
# back to a capped global KNN that we post-filter. See `_dense_search`.
_BRUTE_FORCE_MAX_CHUNKS = 8000


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
    allowed_doc_ids: set[str] | None = None,
) -> RetrievalResult:
    """Run hybrid dense+sparse retrieval, merge with RRF, hydrate hits.

    Args:
        query: Natural-language query. Empty/whitespace-only → empty result.
        rag_conn: RAG DB connection with sqlite-vec loaded.
        top_k: Final hit count after RRF merging.
        candidate_k: Number of candidates pulled from each side before merging.
        rrf_k: RRF smoothing constant; standard value is 60.
        ollama_url: Override the embedder's default URL (useful for tests).
        allowed_doc_ids: Optional metadata filter — restrict retrieval to chunks
            whose `doc_id` is in this set. `None` means no filter (search the
            whole corpus); an empty set means the filter matched no documents,
            so the result is empty without touching the DB. Both arms apply the
            filter *before* ranking: the sparse side constrains the FTS match in
            SQL, and the dense side either scores only in-scope chunks (narrow
            scope) or post-filters a maxed-out KNN pool (broad scope) — see
            `_dense_search`. This avoids the post-filter starvation where a
            narrow scope's chunks never reach a fixed global candidate pool.

    Returns:
        RetrievalResult with `hits` sorted by descending RRF score and
        `used_dense` flag indicating whether Ollama embedding succeeded.
    """
    if not query.strip():
        return RetrievalResult(hits=[], used_dense=False)

    # A filter that resolved to no documents can never match a chunk — skip the
    # round-trips. This is distinct from `None`, which means "no filter at all".
    if allowed_doc_ids is not None and not allowed_doc_ids:
        return RetrievalResult(hits=[], used_dense=False)

    sparse = _sparse_search(query, rag_conn, candidate_k, allowed_doc_ids)

    dense: list[tuple[int, float]] = []
    used_dense = False
    try:
        vec = embedder.embed_text(embedder.format_query(query), base_url=ollama_url)
        dense = _dense_search(vec, rag_conn, candidate_k, allowed_doc_ids)
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
    allowed_doc_ids: set[str] | None = None,
) -> list[tuple[int, float]]:
    """ANN search over chunks_vec. Returns (chunk_id, distance) ordered ascending.

    Without a filter this is a plain top-`k` KNN (k clamped to the sqlite-vec
    cap, `_VEC_KNN_MAX`).

    With `allowed_doc_ids`, the `doc_id` column lives on `chunks`, not on the
    `chunks_vec` virtual table, so sqlite-vec can't constrain the KNN scan
    itself. Post-filtering a fixed global pool would (and did) starve narrow
    scopes: when the corpus-wide nearest neighbours are dominated by other docs,
    no in-scope chunk survives. So the filter is applied *before* ranking, with
    the method chosen by scope size:

    * Scope of at most `_BRUTE_FORCE_MAX_CHUNKS` chunks — score every in-scope
      chunk with `vec_distance_L2` (the same metric the KNN uses) and keep the
      nearest `k`. Exact, and bounded by the scope rather than the corpus.
    * Larger scope — run the global KNN at the maximum `k` the engine allows and
      post-filter to the allowlist. A broad scope is well represented in that
      pool so its nearest in-scope chunks survive; this only loses recall for a
      scope that is both large and semantically far from the query, which the
      sparse arm (exact in SQL) still covers.
    """
    packed = embedder.pack_embedding(query_embedding)
    if allowed_doc_ids is None:
        rows = rag_conn.execute(
            "SELECT chunk_id, distance FROM chunks_vec WHERE embedding MATCH ? AND k = ?",
            (packed, min(k, _VEC_KNN_MAX)),
        ).fetchall()
        return [(r["chunk_id"], r["distance"]) for r in rows]

    # One JSON parameter expanded via `json_each`, so a broad allowlist of
    # thousands of ids stays one bound variable (under SQLite's 999-var cap).
    ids_json = json.dumps(sorted(allowed_doc_ids))
    in_scope = rag_conn.execute(
        "SELECT count(*) FROM chunks WHERE doc_id IN (SELECT value FROM json_each(?))",
        (ids_json,),
    ).fetchone()[0]

    if in_scope <= _BRUTE_FORCE_MAX_CHUNKS:
        rows = rag_conn.execute(
            "SELECT v.chunk_id AS chunk_id, "
            "vec_distance_L2(v.embedding, ?) AS distance "
            "FROM chunks_vec v JOIN chunks c ON c.chunk_id = v.chunk_id "
            "WHERE c.doc_id IN (SELECT value FROM json_each(?)) "
            "ORDER BY distance LIMIT ?",
            (packed, ids_json, k),
        ).fetchall()
        return [(r["chunk_id"], r["distance"]) for r in rows]

    rows = rag_conn.execute(
        "SELECT v.chunk_id AS chunk_id, v.distance AS distance, c.doc_id AS doc_id "
        "FROM chunks_vec v JOIN chunks c ON c.chunk_id = v.chunk_id "
        "WHERE v.embedding MATCH ? AND k = ?",
        (packed, _VEC_KNN_MAX),
    ).fetchall()
    filtered = [
        (r["chunk_id"], r["distance"]) for r in rows if r["doc_id"] in allowed_doc_ids
    ]
    return filtered[:k]


def _sparse_search(
    query: str,
    rag_conn: sqlite3.Connection,
    k: int,
    allowed_doc_ids: set[str] | None = None,
) -> list[tuple[int, float]]:
    """FTS5 BM25 search over chunks_fts.

    Each query word is wrapped in double-quotes individually so FTS5 applies
    AND-of-terms across the unstemmed forms (the porter tokenizer still
    matches via stem). Phrase matching across the whole query is intentionally
    avoided — natural-language queries rarely match verbatim phrases.

    With `allowed_doc_ids`, the match is constrained to those documents' chunks
    directly in SQL (exact, no recall loss). The id list is bound as one JSON
    parameter and expanded via `json_each`, so it sidesteps SQLite's
    999-variable cap even when a broad subject filter yields thousands of ids.

    Malformed FTS5 syntax returns an empty list rather than raising; missing-
    table errors propagate to the caller for 503 translation.
    """
    words = query.split()
    if not words:
        return []
    escaped = " ".join('"' + w.replace('"', "") + '"' for w in words)
    try:
        if allowed_doc_ids is None:
            rows = rag_conn.execute(
                "SELECT rowid, rank FROM chunks_fts WHERE chunks_fts MATCH ? LIMIT ?",
                (escaped, k),
            ).fetchall()
        else:
            # JOIN to `chunks` rather than `rowid IN (subquery)`: the subquery
            # form makes SQLite materialise every in-scope rowid into a temp
            # b-tree before the FTS match, which costs seconds on a broad
            # allowlist; the JOIN lets the FTS index drive and filters per hit,
            # returning the identical rows ~600x faster.
            rows = rag_conn.execute(
                "SELECT chunks_fts.rowid AS rowid, chunks_fts.rank AS rank "
                "FROM chunks_fts JOIN chunks c ON c.chunk_id = chunks_fts.rowid "
                "WHERE chunks_fts MATCH ? "
                "AND c.doc_id IN (SELECT value FROM json_each(?)) "
                "LIMIT ?",
                (escaped, json.dumps(sorted(allowed_doc_ids)), k),
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
