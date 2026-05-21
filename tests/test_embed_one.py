"""Unit tests for the live single-document embed (`rag.embed_one.embed_doc`).

The embedder is stubbed so these run without Ollama: `embed_texts_batch` is
replaced with a deterministic fake that returns one 768-dim vector per input.
Each test builds a throwaway RAG DB with the real schema so the FTS / vec /
foreign-key wiring is exercised end to end.
"""

import sqlite3

import pytest

from rag import Doc, embedder
from rag.chunker import chunk_markdown
from rag.embed_one import embed_doc
from rag.schema import connect_rag


@pytest.fixture
def rag_conn(tmp_path):
    conn = connect_rag(tmp_path / "test_rag.db")
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _fake_embedder(monkeypatch):
    """Return one all-zero vector of the right width per input — no network."""

    def fake_batch(texts, base_url=embedder.OLLAMA_URL):
        return [[0.0] * embedder.EMBEDDING_DIM for _ in texts]

    monkeypatch.setattr(embedder, "embed_texts_batch", fake_batch)


def _counts(conn: sqlite3.Connection, doc_id: str) -> tuple[int, int, int]:
    chunks = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE doc_id = ?", (doc_id,)
    ).fetchone()[0]
    vecs = conn.execute(
        "SELECT COUNT(*) FROM chunks_vec WHERE chunk_id IN "
        "(SELECT chunk_id FROM chunks WHERE doc_id = ?)",
        (doc_id,),
    ).fetchone()[0]
    meta = conn.execute(
        "SELECT COUNT(*) FROM docs_meta WHERE doc_id = ?", (doc_id,)
    ).fetchone()[0]
    return chunks, vecs, meta


def _doc(text: str, version: str = "v1") -> Doc:
    return Doc(doc_id="42", title="Earth", version=version, text=text, section=None)


def test_embed_writes_chunks_vec_meta(rag_conn):
    doc = _doc("## History\n" + "word " * 400 + "\n## Geography\n" + "place " * 400)
    n = embed_doc(rag_conn, doc, chunk_fn=chunk_markdown, chunk_size=500, overlap=50)

    assert n > 1  # multi-section body splits into several chunks
    chunks, vecs, meta = _counts(rag_conn, "42")
    assert chunks == n
    assert vecs == n  # one vector per chunk
    assert meta == 1
    row = rag_conn.execute(
        "SELECT version, chunk_count FROM docs_meta WHERE doc_id = '42'"
    ).fetchone()
    assert row["version"] == "v1"
    assert row["chunk_count"] == n


def test_embed_is_searchable_via_fts(rag_conn):
    doc = _doc("## History\nThe planet formed billions of years ago. " * 30)
    embed_doc(rag_conn, doc, chunk_fn=chunk_markdown, chunk_size=500)

    hit = rag_conn.execute(
        "SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'planet'"
    ).fetchone()[0]
    assert hit > 0


def test_reembed_replaces_not_appends(rag_conn):
    doc1 = _doc("## A\n" + "alpha " * 300, version="v1")
    n1 = embed_doc(rag_conn, doc1, chunk_fn=chunk_markdown, chunk_size=500)
    assert n1 > 0

    # Same doc_id, new content + version — must overwrite, not accumulate.
    doc2 = _doc("## B\n" + "beta " * 300, version="v2")
    n2 = embed_doc(rag_conn, doc2, chunk_fn=chunk_markdown, chunk_size=500)

    chunks, vecs, meta = _counts(rag_conn, "42")
    assert chunks == n2  # only the second embed's chunks remain
    assert vecs == n2  # no orphan vectors left behind
    assert meta == 1
    assert (
        rag_conn.execute(
            "SELECT version FROM docs_meta WHERE doc_id = '42'"
        ).fetchone()["version"]
        == "v2"
    )
    # Stale FTS entries from the first embed must be gone.
    assert (
        rag_conn.execute(
            "SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'alpha'"
        ).fetchone()[0]
        == 0
    )


def test_empty_doc_embeds_nothing_and_clears_prior(rag_conn):
    n1 = embed_doc(
        rag_conn, _doc("## A\n" + "alpha " * 300), chunk_fn=chunk_markdown, chunk_size=500
    )
    assert n1 > 0

    n2 = embed_doc(rag_conn, _doc("", version="v2"), chunk_fn=chunk_markdown)
    assert n2 == 0
    chunks, vecs, meta = _counts(rag_conn, "42")
    assert chunks == 0 and vecs == 0 and meta == 0
