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
    """`_dense_search` over-fetches then keeps only allowed docs."""
    vec = [0.0] * embedder.EMBEDDING_DIM
    rows = retriever._dense_search(vec, rag_conn, 50, {"sci/s1"})
    ids = {
        rag_conn.execute(
            "SELECT doc_id FROM chunks WHERE chunk_id = ?", (cid,)
        ).fetchone()["doc_id"]
        for cid, _ in rows
    }
    assert ids == {"sci/s1"}
