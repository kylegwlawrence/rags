"""Happy-path smoke tests for every route plus 400/503 cases.

Phase 2a baseline. Each test makes one request and asserts shape (status code,
required keys) — not values, since the underlying data evolves. The chunks
endpoint tests are parametrized over every source that has a `_rag.db`:
factbook joins the list in Phase 2c.
"""

import httpx
import pytest
from fastapi import HTTPException

from api import db
from api.main import app

# (source_name, db.attr_name). Add new sources as their /<source>/chunks
# endpoint ships.
RAG_SOURCES = [
    pytest.param("arxiv", "arxiv_rag", id="arxiv"),
    pytest.param("openalex", "openalex_rag", id="openalex"),
    pytest.param("factbook", "factbook_rag", id="factbook"),
    pytest.param("gutenberg", "gutenberg_rag", id="gutenberg"),
]


def test_health_all_dbs_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    for name in ("arxiv", "arxiv_rag", "factbook", "factbook_rag",
                 "openalex", "openalex_rag", "gutenberg", "gutenberg_rag"):
        assert body["databases"][name] == "ok", body["databases"]


def test_health_503_when_any_db_broken(client, monkeypatch):
    """/health flips to 503 if any opener raises; the body still names which one failed."""
    def broken():
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(db, "arxiv", broken)
    r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False
    assert body["databases"]["arxiv"].startswith("error:")
    # Other DBs continue to be probed; the broken one doesn't short-circuit the loop.
    assert len(body["databases"]) == 8


def test_factbook_list(client):
    r = client.get("/factbook/countries?limit=1")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body
    assert len(body["items"]) <= 1


def test_openalex_list(client):
    r = client.get("/openalex/works?limit=1")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body


def test_gutenberg_list(client):
    r = client.get("/gutenberg/texts?limit=1")
    assert r.status_code == 200
    assert "items" in r.json()


def test_arxiv_list(client):
    r = client.get("/arxiv/papers?limit=1")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body
    if body["items"]:
        item = body["items"][0]
        assert isinstance(item["authors"], list)
        assert isinstance(item["categories"], list)
        assert isinstance(item["has_html"], bool)


def test_arxiv_detail_404(client):
    r = client.get("/arxiv/papers/9999.99999")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /<source>/chunks — parametrized across every RAG source.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source,opener_name", RAG_SOURCES)
def test_chunks_happy(client, source, opener_name):
    r = client.get(f"/{source}/chunks", params={"q": "learning", "top_k": 3})
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "used_dense" in body
    assert isinstance(body["used_dense"], bool)
    assert body["top_k"] == 3
    if not body["items"]:
        pytest.skip(
            f"{source}_rag.db returned no hits for 'learning'; "
            f"run scripts/{source}_index_rag.py to build the corpus before this assertion is meaningful"
        )
    item = body["items"][0]
    for key in ("chunk_id", "doc_id", "title", "section", "text", "score"):
        assert key in item, item


@pytest.mark.parametrize("source,opener_name", RAG_SOURCES)
def test_chunks_empty_q_400(client, source, opener_name):
    r = client.get(f"/{source}/chunks", params={"q": "   "})
    assert r.status_code == 400


@pytest.mark.parametrize("source,opener_name", RAG_SOURCES)
def test_chunks_missing_q_4xx(client, source, opener_name):
    r = client.get(f"/{source}/chunks")
    # FastAPI rejects missing required Query with 422; that's also a 4xx.
    assert r.status_code in (400, 422)


@pytest.mark.parametrize("source,opener_name", RAG_SOURCES)
def test_chunks_503_when_rag_db_missing(client, source, opener_name):
    opener = getattr(db, opener_name)

    def fake_rag():
        raise HTTPException(status_code=503, detail=f"{opener_name}.db not available: test")

    app.dependency_overrides[opener] = fake_rag
    r = client.get(f"/{source}/chunks", params={"q": "foo"})
    assert r.status_code == 503


@pytest.mark.parametrize("source,opener_name", RAG_SOURCES)
def test_chunks_sparse_only_when_ollama_down(client, source, opener_name, monkeypatch):
    """If embedding raises httpx.HTTPError, the route still returns 200 with used_dense=False."""
    from rag import embedder

    def boom(*_a, **_kw):
        raise httpx.ConnectError("simulated ollama down")

    monkeypatch.setattr(embedder, "embed_text", boom)
    r = client.get(f"/{source}/chunks", params={"q": "learning"})
    assert r.status_code == 200
    assert r.json()["used_dense"] is False


@pytest.mark.parametrize("source,opener_name", RAG_SOURCES)
def test_rag_no_orphan_vectors(source, opener_name):
    """chunks_vec must have exactly one row per chunks row across every RAG DB.

    Catches the indexer-orphan bug where re-embedding a doc deletes from
    chunks but leaves the corresponding chunks_vec rows behind. sqlite-vec
    is a virtual table and FK cascade doesn't reach it, so the indexer's
    flush() must delete from chunks_vec explicitly.
    """
    conn = getattr(db, opener_name)()
    n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    n_vecs = conn.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()[0]
    if n_chunks == 0:
        pytest.skip(
            f"{opener_name}.db has no chunks; run scripts/{source}_index_rag.py first"
        )
    assert n_chunks == n_vecs, f"orphan vectors: {n_chunks} chunks vs {n_vecs} vectors"
