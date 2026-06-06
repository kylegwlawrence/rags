"""Hybrid dense+sparse retrieval via RRF.

Dense: sqlite-vec ANN. Sparse: FTS5 BM25 (AND-of-words). Falls back to sparse-only
when Ollama is unreachable (used_dense=False). Missing table → OperationalError → 503.
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
    """True for missing-table / unreadable-DB errors (→ 503), not bad SQL (→ 400)."""
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
    """Hybrid dense+sparse search with RRF fusion. Empty set for allowed_doc_ids → empty result."""
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
    """ANN search over chunks_vec → (chunk_id, distance) ascending.

    With a filter: doc_id lives on chunks not chunks_vec, so post-filtering a fixed global
    pool starves narrow scopes. Instead: ≤ _BRUTE_FORCE_MAX_CHUNKS → exact per-chunk L2 score;
    larger scope → global KNN capped at _VEC_KNN_MAX then post-filtered.
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
    """FTS5 BM25 search. Each word quoted individually (AND-of-stems, no phrase matching).
    Bad FTS syntax → empty list; missing table propagates for 503.
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
