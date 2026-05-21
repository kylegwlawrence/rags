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
    pytest.param("simplewiki", "simplewiki_rag", id="simplewiki"),
    pytest.param("pydocs", "pydocs_rag", id="pydocs"),
    pytest.param("wikihow", "wikihow_rag", id="wikihow"),
]

# Path to each source's rag.db; used by the happy-path test to skip cleanly
# when the indexer hasn't run yet (a freshly-checked-out repo, or a corpus
# the user is mid-rebuilding).
RAG_DB_PATHS = {
    "arxiv_rag": db.ARXIV_RAG_DB,
    "openalex_rag": db.OPENALEX_RAG_DB,
    "factbook_rag": db.FACTBOOK_RAG_DB,
    "gutenberg_rag": db.GUTENBERG_RAG_DB,
    "simplewiki_rag": db.SIMPLEWIKI_RAG_DB,
    "pydocs_rag": db.PYDOCS_RAG_DB,
    "wikihow_rag": db.WIKIHOW_RAG_DB,
}

HEALTH_DBS = (
    "arxiv", "arxiv_rag",
    "factbook", "factbook_rag",
    "openalex", "openalex_rag",
    "gutenberg", "gutenberg_rag",
    "simplewiki", "simplewiki_rag",
    "pydocs", "pydocs_rag",
    "wikihow", "wikihow_rag",
)

# Repo-relative path to each source's rag indexer script. Most sources have
# `scripts/<source>/<source>_index_rag.py`; pydocs lives at
# `scripts/python_docs/python_docs_index_rag.py` (the on-disk script dir name
# doesn't match the short `pydocs` source name). Used in skip messages.
INDEXER_SCRIPTS = {
    "arxiv": "scripts/arxiv/arxiv_index_rag.py",
    "openalex": "scripts/openalex/openalex_index_rag.py",
    "factbook": "scripts/factbook/factbook_index_rag.py",
    "gutenberg": "scripts/gutenberg/gutenberg_index_rag.py",
    "simplewiki": "scripts/simplewiki/simplewiki_index_rag.py",
    "pydocs": "scripts/python_docs/python_docs_index_rag.py",
    "wikihow": "scripts/wikihow/wikihow_index_rag.py",
}


def test_health_all_dbs_ok(client):
    r = client.get("/health")
    body = r.json()
    # A freshly-checked-out repo may not have every rag.db built yet; skip the
    # green-path assertion in that case rather than fail noisily. Other tests
    # (`test_health_503_when_any_db_broken`) still cover the failure path.
    missing = [
        name for name, val in body["databases"].items()
        if "unable to open database file" in val
    ]
    if missing:
        pytest.skip(f"DB file(s) missing on disk: {missing} — run the relevant *_index_rag.py")
    assert r.status_code == 200
    assert body["ok"] is True
    for name in HEALTH_DBS:
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
    assert len(body["databases"]) == len(HEALTH_DBS)


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


def test_simplewiki_list(client):
    r = client.get("/simplewiki/articles?limit=1")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body
    if body["items"]:
        item = body["items"][0]
        for key in ("page_id", "title", "namespace", "revision_id", "timestamp"):
            assert key in item, item
        assert item["namespace"] == 0  # default filter is main namespace


def test_simplewiki_fts_trigram(client):
    """Trigram FTS5 matches substrings anywhere in the title."""
    r = client.get("/simplewiki/articles?q=ngineer&limit=3")
    assert r.status_code == 200
    items = r.json()["items"]
    if not items:
        pytest.skip("simplewiki.db has no 'ngineer' matches")
    # Trigram tokenizer: 'ngineer' as a 3-gram pattern hits 'Engineer', 'Engineering', etc.
    assert any("ngineer" in it["title"].lower() for it in items)


def test_simplewiki_detail_404(client):
    r = client.get("/simplewiki/articles/99999999")
    assert r.status_code == 404


def test_simplewiki_content_returns_wikitext(client):
    """/simplewiki/articles/{id}/content returns raw wikitext as text/plain."""
    r = client.get("/simplewiki/articles?limit=1")
    items = r.json()["items"]
    if not items:
        pytest.skip("simplewiki.db has no articles; run scripts/simplewiki/simplewiki_parse.py")
    page_id = items[0]["page_id"]
    r = client.get(f"/simplewiki/articles/{page_id}/content")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert len(r.content) > 0


def test_pydocs_list(client):
    r = client.get("/pydocs/docs?limit=1")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body
    if body["items"]:
        item = body["items"][0]
        for key in ("doc_path", "section", "title", "content_chars"):
            assert key in item, item


def test_pydocs_section_filter(client):
    """Filtering by top-level section returns only docs from that section."""
    r = client.get("/pydocs/docs?section=library&limit=3")
    assert r.status_code == 200
    items = r.json()["items"]
    if not items:
        pytest.skip("pydocs has no library docs; run scripts/python_docs/python_docs_download.py")
    for item in items:
        assert item["section"] == "library", item


def test_pydocs_fts_query(client):
    """`q` runs an FTS5 match over title + content; results are bm25-ranked."""
    r = client.get("/pydocs/docs?q=asyncio&limit=3")
    if r.status_code == 503:
        # FTS index not built yet — surface as a skip, same as the rag.db pattern.
        pytest.skip("docs_fts not built; run scripts/python_docs/python_docs_index_fts.py")
    assert r.status_code == 200
    items = r.json()["items"]
    if not items:
        pytest.skip("pydocs has no 'asyncio' matches")
    # Both title and content are indexed; at least one of them should mention asyncio.
    assert any("asyncio" in (it["title"] or "").lower() or it["doc_path"].startswith("library")
               for it in items)


def test_pydocs_detail_404(client):
    r = client.get("/pydocs/docs/nonexistent/page")
    assert r.status_code == 404


def test_wikihow_list(client):
    r = client.get("/wikihow/articles?limit=1")
    if r.status_code == 503:
        pytest.skip("wikihow.db not built; run scripts/wikihow/wikihow_loader.py")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body
    if body["items"]:
        item = body["items"][0]
        for key in ("id", "title", "section_label", "headline", "text_chars"):
            assert key in item, item


def test_wikihow_fts_query(client):
    """`q` runs an FTS5 match over title + headline + text; results are bm25-ranked."""
    r = client.get("/wikihow/articles?q=water&limit=3")
    if r.status_code == 503:
        pytest.skip("articles_fts not built; run scripts/wikihow/wikihow_index_fts.py")
    assert r.status_code == 200
    assert "items" in r.json()


def test_wikihow_detail_404(client):
    r = client.get("/wikihow/articles?limit=1")
    if r.status_code == 503:
        pytest.skip("wikihow.db not built; run scripts/wikihow/wikihow_loader.py")
    r = client.get("/wikihow/articles/999999999")
    assert r.status_code == 404


def test_wikihow_content_returns_text(client):
    """/wikihow/articles/{id}/content returns the raw step body as text/plain."""
    r = client.get("/wikihow/articles?limit=1")
    if r.status_code == 503:
        pytest.skip("wikihow.db not built; run scripts/wikihow/wikihow_loader.py")
    items = r.json()["items"]
    if not items:
        pytest.skip("wikihow.db has no rows; run scripts/wikihow/wikihow_loader.py")
    article_id = items[0]["id"]
    r = client.get(f"/wikihow/articles/{article_id}/content")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert len(r.content) > 0


def test_pydocs_content_returns_text(client):
    """/pydocs/docs/{doc_path}/content returns raw Sphinx-text body as text/plain."""
    r = client.get("/pydocs/docs?limit=1")
    items = r.json()["items"]
    if not items:
        pytest.skip("python_docs.db has no docs; run scripts/python_docs/python_docs_download.py")
    doc_path = items[0]["doc_path"]
    r = client.get(f"/pydocs/docs/{doc_path}/content")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert len(r.content) > 0


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


def test_arxiv_papers_503_when_paper_authors_missing(client, tmp_path):
    """A DB without paper_authors/authors should produce 503, not 500.

    Catches the regression where /arxiv/papers reaches the author-join
    SELECT and gets `no such table: paper_authors` from SQLite. Without
    the translation it'd surface as a generic 500; with the
    translate_fts_errors wrap it surfaces as 503 with a hint to run the
    backfill or ingest scripts.
    """
    import sqlite3

    db_path = tmp_path / "no_authors.db"
    c = sqlite3.connect(db_path)
    c.executescript(
        """
        CREATE TABLE papers (
            id TEXT PRIMARY KEY,
            oai_datestamp TEXT NOT NULL,
            title TEXT NOT NULL,
            abstract TEXT NOT NULL,
            categories TEXT NOT NULL,
            primary_category TEXT NOT NULL,
            submitted_date TEXT NOT NULL,
            updated_date TEXT,
            doi TEXT,
            journal_ref TEXT,
            comments TEXT,
            html_content TEXT,
            download_status TEXT,
            downloaded_at TEXT
        );
        """
    )
    c.execute(
        "INSERT INTO papers (id, oai_datestamp, title, abstract, categories, "
        "primary_category, submitted_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("2401.0001", "2024-01-22", "T", "A", "cs.CL", "cs.CL", "2024-01-22"),
    )
    c.commit()
    c.close()

    ro_conn = sqlite3.connect(
        f"file:{db_path}?mode=ro", uri=True, check_same_thread=False
    )
    ro_conn.row_factory = sqlite3.Row

    app.dependency_overrides[db.arxiv] = lambda: ro_conn
    try:
        r = client.get("/arxiv/papers?limit=1")
        assert r.status_code == 503, r.text
        assert "arxiv_normalize_authors.py" in r.json()["detail"]
    finally:
        ro_conn.close()


# ---------------------------------------------------------------------------
# /<source>/chunks — parametrized across every RAG source.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source,opener_name", RAG_SOURCES)
def test_chunks_happy(client, source, opener_name):
    rag_path = RAG_DB_PATHS[opener_name]
    if not rag_path.exists():
        pytest.skip(
            f"{rag_path} missing — run {INDEXER_SCRIPTS[source]} to build it"
        )
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
    # Depends(opener) runs before the empty-q check; if the file is missing
    # we get 503 (legitimate — the system can't answer), not 400.
    rag_path = RAG_DB_PATHS[opener_name]
    if not rag_path.exists():
        pytest.skip(
            f"{rag_path} missing — run {INDEXER_SCRIPTS[source]} to build it"
        )
    r = client.get(f"/{source}/chunks", params={"q": "   "})
    assert r.status_code == 400


@pytest.mark.parametrize("source,opener_name", RAG_SOURCES)
def test_chunks_missing_q_4xx(client, source, opener_name):
    rag_path = RAG_DB_PATHS[opener_name]
    if not rag_path.exists():
        pytest.skip(
            f"{rag_path} missing — run {INDEXER_SCRIPTS[source]} to build it"
        )
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

    rag_path = RAG_DB_PATHS[opener_name]
    if not rag_path.exists():
        pytest.skip(
            f"{rag_path} missing — run {INDEXER_SCRIPTS[source]} to build it"
        )

    def boom(*_a, **_kw):
        raise httpx.ConnectError("simulated ollama down")

    monkeypatch.setattr(embedder, "embed_text", boom)
    r = client.get(f"/{source}/chunks", params={"q": "learning"})
    assert r.status_code == 200
    assert r.json()["used_dense"] is False


def test_gutenberg_content_serves_file(client):
    """/gutenberg/texts/{id}/content streams the raw .txt body."""
    r = client.get("/gutenberg/texts?limit=1")
    assert r.status_code == 200
    items = r.json()["items"]
    if not items:
        pytest.skip("gutenberg.db has no texts; run scripts/gutenberg_index.py")
    text_id = items[0]["id"]
    r = client.get(f"/gutenberg/texts/{text_id}/content")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert len(r.content) > 0


def test_gutenberg_content_rejects_path_traversal(client, monkeypatch):
    """Defense-in-depth: serving rejects any path that escapes GUTENBERG_ROOT.

    Monkeypatches `_lookup` to return a malicious row; the router's resolved-path
    check should turn the request into a 404 without reading the file.
    """
    from api.routers import gutenberg as gutenberg_router

    def malicious_lookup(conn, text_id):
        return {
            "id": text_id,
            "path": "../../etc/passwd",
            "title": "x",
            "author": "x",
            "language": "en",
            "release_date": "",
            "size_bytes": 0,
        }

    monkeypatch.setattr(gutenberg_router, "_lookup", malicious_lookup)
    r = client.get("/gutenberg/texts/1/content")
    assert r.status_code == 404


def test_arxiv_content_endpoint(client):
    """/arxiv/papers/{id}/content returns 200 text/html when has_html, 404 otherwise."""
    r = client.get("/arxiv/papers?limit=1")
    assert r.status_code == 200
    items = r.json()["items"]
    if not items:
        pytest.skip("arxiv.db has no papers")
    paper = items[0]
    r = client.get(f"/arxiv/papers/{paper['id']}/content")
    if paper["has_html"]:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert len(r.content) > 0
    else:
        assert r.status_code == 404


@pytest.mark.parametrize("source,opener_name", RAG_SOURCES)
def test_rag_no_orphan_vectors(source, opener_name):
    """chunks_vec must have exactly one row per chunks row across every RAG DB.

    Catches the indexer-orphan bug where re-embedding a doc deletes from
    chunks but leaves the corresponding chunks_vec rows behind. sqlite-vec
    is a virtual table and FK cascade doesn't reach it, so the indexer's
    flush() must delete from chunks_vec explicitly.
    """
    rag_path = RAG_DB_PATHS[opener_name]
    if not rag_path.exists():
        pytest.skip(
            f"{rag_path} missing — run {INDEXER_SCRIPTS[source]} to build it"
        )
    conn = getattr(db, opener_name)()
    n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    n_vecs = conn.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()[0]
    if n_chunks == 0:
        pytest.skip(
            f"{opener_name}.db has no chunks; run {INDEXER_SCRIPTS[source]} first"
        )
    assert n_chunks == n_vecs, f"orphan vectors: {n_chunks} chunks vs {n_vecs} vectors"
