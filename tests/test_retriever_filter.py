"""Unit tests for the `allowed_doc_ids` metadata filter on `retriever.retrieve`.

The embedder is stubbed so these run without Ollama — both the batch path used
to populate the test DB (`embed_texts_batch`) and the single-query path the
retriever calls (`embed_text`) return all-zero vectors. All chunks therefore
land at the same dense distance, which is fine: these tests exercise the
*filtering*, not the ranking.
"""

import sqlite3

import pytest

from rag import Doc, embedder, retriever
from rag.chunker import chunk_doc
from rag.embed_one import embed_doc
from rag.schema import connect_rag


@pytest.fixture(autouse=True)
def _fake_embedder(monkeypatch):
    """No-network embedder: one all-zero vector of the right width per input."""

    def fake_batch(texts, base_url=embedder.OLLAMA_URL):
        return [[0.0] * embedder.EMBEDDING_DIM for _ in texts]

    def fake_one(text, base_url=embedder.OLLAMA_URL):
        return [0.0] * embedder.EMBEDDING_DIM

    monkeypatch.setattr(embedder, "embed_texts_batch", fake_batch)
    monkeypatch.setattr(embedder, "embed_text", fake_one)


@pytest.fixture
def rag_conn(tmp_path):
    """A RAG DB seeded with two docs in different 'books' sharing a query word."""
    conn = connect_rag(tmp_path / "filter_rag.db")
    embed_doc(
        conn,
        Doc(
            doc_id="math/s1",
            title="Calculus",
            version="v1",
            text="Energy and the derivative of motion. " * 40,
            section="Limits",
        ),
        chunk_fn=chunk_doc,
        chunk_size=500,
    )
    embed_doc(
        conn,
        Doc(
            doc_id="sci/s1",
            title="Biology",
            version="v1",
            text="Energy flows through the living cell. " * 40,
            section="Cells",
        ),
        chunk_fn=chunk_doc,
        chunk_size=500,
    )
    yield conn
    conn.close()


def test_none_allowlist_searches_whole_corpus(rag_conn):
    result = retriever.retrieve("energy", rag_conn, top_k=20, candidate_k=50)
    doc_ids = {h.doc_id for h in result.hits}
    assert doc_ids == {"math/s1", "sci/s1"}


def test_allowlist_restricts_to_named_docs(rag_conn):
    result = retriever.retrieve(
        "energy", rag_conn, top_k=20, candidate_k=50, allowed_doc_ids={"math/s1"}
    )
    assert result.hits  # the in-scope doc still matches
    assert {h.doc_id for h in result.hits} == {"math/s1"}


def test_empty_allowlist_returns_empty_without_touching_db(rag_conn):
    result = retriever.retrieve(
        "energy", rag_conn, top_k=20, candidate_k=50, allowed_doc_ids=set()
    )
    assert result.hits == []
    assert result.used_dense is False


def test_sparse_filter_handles_large_id_list(rag_conn):
    """A broad filter (thousands of ids) must not hit SQLite's 999-var cap.

    Exercises `_sparse_search` directly with an allowlist far past the
    positional-parameter limit; the JSON/`json_each` binding keeps it one param.
    """
    big = {f"pad/{i}" for i in range(2000)} | {"sci/s1"}
    rows = retriever._sparse_search("energy", rag_conn, 50, big)
    matched_ids = {
        rag_conn.execute(
            "SELECT doc_id FROM chunks WHERE chunk_id = ?", (rowid,)
        ).fetchone()["doc_id"]
        for rowid, _ in rows
    }
    assert matched_ids == {"sci/s1"}


def test_dense_filter_drops_out_of_scope(rag_conn):
    """`_dense_search` keeps only allowed docs."""
    vec = [0.0] * embedder.EMBEDDING_DIM
    rows = retriever._dense_search(vec, rag_conn, 50, {"sci/s1"})
    ids = {
        rag_conn.execute(
            "SELECT doc_id FROM chunks WHERE chunk_id = ?", (cid,)
        ).fetchone()["doc_id"]
        for cid, _ in rows
    }
    assert ids == {"sci/s1"}


def _unit(dim: int, axis: int) -> list[float]:
    """A one-hot float vector of width `dim` with 1.0 on `axis`."""
    v = [0.0] * dim
    v[axis] = 1.0
    return v


def _insert_chunk(
    conn: sqlite3.Connection, doc_id: str, text: str, embedding: list[float]
) -> int:
    """Insert one chunk with a hand-chosen embedding straight into the RAG tables.

    Bypasses `embed_doc`/Ollama so a test can place chunks at exact distances
    from a query vector — needed to reproduce the dense-side starvation bug,
    which only shows up when many out-of-scope chunks sit nearer the query than
    the in-scope ones.
    """
    conn.execute(
        "INSERT OR IGNORE INTO docs_meta(doc_id, version, title, chunk_count, "
        "indexed_at) VALUES (?, 'v1', ?, 0, '2026-01-01')",
        (doc_id, doc_id),
    )
    cur = conn.execute(
        "INSERT INTO chunks(doc_id, section, chunk_index, text, text_length) "
        "VALUES (?, NULL, 0, ?, ?)",
        (doc_id, text, len(text)),
    )
    chunk_id = cur.lastrowid
    conn.execute(
        "INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)", (chunk_id, text)
    )
    conn.execute(
        "INSERT INTO chunks_vec(chunk_id, embedding) VALUES (?, ?)",
        (chunk_id, embedder.pack_embedding(embedding)),
    )
    conn.commit()
    return chunk_id


def test_dense_filter_recovers_narrow_scope_from_starvation(tmp_path):
    """A narrow scope must return its chunks even when out-of-scope ones are nearer.

    Reproduces the reported bug: with the old "global KNN then post-filter"
    design, a candidate pool dominated by nearer out-of-scope chunks left zero
    in-scope survivors. Here 400 out-of-scope chunks sit nearer the query than 5
    in-scope chunks — far more than any fixed candidate pool — so only a
    filter-before-rank dense search can recover the in-scope set.
    """
    dim = embedder.EMBEDDING_DIM
    conn = connect_rag(tmp_path / "starve_rag.db")
    query = _unit(dim, 0)
    near = [0.95] + [0.0] * (dim - 1)  # ~0.05 from the query
    far = _unit(dim, 1)  # ~1.41 from the query
    for i in range(400):
        _insert_chunk(conn, f"other/{i}", f"noise {i}", near)
    for i in range(5):
        _insert_chunk(conn, f"physics/{i}", f"torque {i}", far)

    allowed = {f"physics/{i}" for i in range(5)}
    rows = retriever._dense_search(query, conn, 50, allowed)
    ids = {
        conn.execute(
            "SELECT doc_id FROM chunks WHERE chunk_id = ?", (cid,)
        ).fetchone()["doc_id"]
        for cid, _ in rows
    }
    conn.close()
    assert ids == allowed  # all 5 in-scope recovered, none starved out


def test_dense_search_clamps_k_above_engine_cap(rag_conn):
    """A `k` past the sqlite-vec cap is clamped, not thrown (candidate_k bug #2).

    sqlite-vec 0.1.9 rejects `k` above 4096 with an OperationalError; the old
    code multiplied candidate_k by an oversample factor and could sail past it,
    making large candidate_k values fail instead of returning more.
    """
    vec = [0.0] * embedder.EMBEDDING_DIM
    rows = retriever._dense_search(vec, rag_conn, 10_000)  # no allowlist
    assert rows  # returns results rather than raising "k too large"
