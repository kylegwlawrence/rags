"""Happy-path smoke tests for every route plus 400/503 cases.

Phase 2a baseline. Each test makes one request and asserts shape (status code,
required keys) — not values, since the underlying data evolves.
"""

import httpx
import pytest
from fastapi import HTTPException

from api import db
from api.main import app


def test_health_all_dbs_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    for name in ("arxiv", "arxiv_rag", "factbook", "openalex", "gutenberg"):
        assert body["databases"][name] == "ok", body["databases"]


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


def test_arxiv_chunks_happy(client):
    r = client.get("/arxiv/chunks", params={"q": "learning", "top_k": 3})
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "used_dense" in body
    assert isinstance(body["used_dense"], bool)
    assert body["top_k"] == 3
    if body["items"]:
        item = body["items"][0]
        for key in ("chunk_id", "doc_id", "title", "section", "text", "score"):
            assert key in item, item


def test_arxiv_chunks_empty_q_400(client):
    r = client.get("/arxiv/chunks", params={"q": "   "})
    assert r.status_code == 400


def test_arxiv_chunks_missing_q_400(client):
    r = client.get("/arxiv/chunks")
    # FastAPI rejects the missing required Query with 422; that's also a 4xx.
    assert r.status_code in (400, 422)


def test_arxiv_chunks_503_when_rag_db_missing(client):
    def fake_rag():
        raise HTTPException(status_code=503, detail="arxiv_rag.db not available: test")

    app.dependency_overrides[db.arxiv_rag] = fake_rag
    r = client.get("/arxiv/chunks", params={"q": "foo"})
    assert r.status_code == 503


def test_arxiv_chunks_sparse_only_when_ollama_down(client, monkeypatch):
    """If embedding raises httpx.HTTPError, the route still returns 200 with used_dense=False."""
    from rag import embedder

    def boom(*_a, **_kw):
        raise httpx.ConnectError("simulated ollama down")

    monkeypatch.setattr(embedder, "embed_text", boom)
    r = client.get("/arxiv/chunks", params={"q": "learning"})
    assert r.status_code == 200
    assert r.json()["used_dense"] is False
