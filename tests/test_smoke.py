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
}

HEALTH_DBS = (
    "arxiv", "arxiv_rag",
    "factbook", "factbook_rag",
    "openalex", "openalex_rag",
    "gutenberg", "gutenberg_rag",
    "simplewiki", "simplewiki_rag",
    "pydocs", "pydocs_rag",
    "federal_register", "federal_register_rag",
    "github", "github_rag",
    "sec_edgar", "sec_edgar_rag",
    "worldbank",
    "geonames",
    "billstatus",
    "eurlex", "eurlex_rag",
    "ecfr", "ecfr_rag",
    "enwiki", "enwiki_rag",
    "openstax", "openstax_rag",
    "pdfs", "pdfs_rag",
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


def _redirect_fixture_conn():
    """In-memory `articles` table exercising the redirect resolver in isolation.

    Mirrors only the columns `_resolve_redirect` / `_find_by_title` touch, so it
    runs without a real simplewiki.db. Titles are stored MediaWiki-style (spaces,
    first letter upper); redirect bodies use varied casing on purpose.
    """
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE articles (page_id INTEGER PRIMARY KEY, title TEXT, "
        "namespace INTEGER, text_content TEXT)"
    )
    rows = [
        (1, "Animal", 0, "Animals are living things."),          # real target
        (2, "Animalia", 0, "#redirect [[animal]]"),              # lowercase target
        (3, "Critter", 0, "#REDIRECT [[Animalia]]"),             # chains 3->2->1
        (4, "Loop A", 0, "#REDIRECT [[Loop B]]"),                # cycle
        (5, "Loop B", 0, "#REDIRECT [[Loop A]]"),
        (6, "Broken", 0, "#REDIRECT [[Nonexistent Page]]"),      # missing target
    ]
    conn.executemany(
        "INSERT INTO articles (page_id, title, namespace, text_content) VALUES (?, ?, ?, ?)",
        rows,
    )
    return conn


def test_resolve_redirect_chain_and_edge_cases():
    """`_resolve_redirect` follows chains, normalises casing, and bails on cycles."""
    from api.routers.simplewiki import _resolve_redirect

    conn = _redirect_fixture_conn()

    def resolve(page_id):
        text = conn.execute(
            "SELECT text_content FROM articles WHERE page_id = ?", [page_id]
        ).fetchone()["text_content"]
        return _resolve_redirect(conn, text, page_id)

    assert resolve(2) == 1  # lowercase [[animal]] -> Animal
    assert resolve(3) == 1  # Critter -> Animalia -> Animal
    assert resolve(1) is None  # real article isn't a redirect
    assert resolve(4) is None  # A<->B cycle bails out
    assert resolve(6) is None  # target title not present


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


def test_simplewiki_embed_404(client):
    r = client.post("/simplewiki/articles/99999999/embed")
    assert r.status_code == 404


def _first_simplewiki_page_id(client):
    r = client.get("/simplewiki/articles?limit=1")
    items = r.json()["items"]
    if not items:
        pytest.skip("simplewiki.db has no articles; run scripts/simplewiki/simplewiki_parse.py")
    return items[0]["page_id"]


# Two deterministic chunks so the embed path is exercised regardless of whether
# the first real article happens to be a redirect (which would render empty).
def _fake_chunks(doc, **_kw):
    return [
        {"section": None, "chunk_index": 0, "text": "alpha alpha", "text_length": 11},
        {"section": "History", "chunk_index": 0, "text": "beta beta", "text_length": 9},
    ]


def test_simplewiki_embed_happy(client, monkeypatch, tmp_path):
    """POST .../embed chunks + embeds one article into a throwaway RAG DB.

    The embedder and chunker are stubbed (no Ollama, deterministic chunks) and
    the target RAG DB is redirected to tmp_path so the test never mutates the
    real data/simplewiki/simplewiki_rag.db.
    """
    from rag import embedder

    page_id = _first_simplewiki_page_id(client)
    monkeypatch.setattr(db, "SIMPLEWIKI_RAG_DB", tmp_path / "sw_rag.db")
    monkeypatch.setattr("api.routers.simplewiki.chunk_markdown", _fake_chunks)
    monkeypatch.setattr(
        embedder,
        "embed_texts_batch",
        lambda texts, base_url=embedder.OLLAMA_URL: [
            [0.0] * embedder.EMBEDDING_DIM for _ in texts
        ],
    )

    r = client.post(f"/simplewiki/articles/{page_id}/embed")
    assert r.status_code == 200
    body = r.json()
    assert body["doc_id"] == str(page_id)
    assert set(body) == {"doc_id", "title", "chunk_count", "embedded"}
    assert body["chunk_count"] == 2
    assert body["embedded"] is True


def test_simplewiki_embed_503_when_ollama_down(client, monkeypatch, tmp_path):
    """If embedding raises httpx.HTTPError, the embed route returns 503."""
    from rag import embedder

    page_id = _first_simplewiki_page_id(client)
    monkeypatch.setattr(db, "SIMPLEWIKI_RAG_DB", tmp_path / "sw_rag.db")
    monkeypatch.setattr("api.routers.simplewiki.chunk_markdown", _fake_chunks)

    def boom(*_a, **_kw):
        raise httpx.ConnectError("simulated ollama down")

    monkeypatch.setattr(embedder, "embed_texts_batch", boom)
    r = client.post(f"/simplewiki/articles/{page_id}/embed")
    assert r.status_code == 503


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


def test_pdfs_list(client):
    r = client.get("/pdfs/documents?limit=1")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body
    if body["items"]:
        item = body["items"][0]
        for key in ("doc_id", "title", "author", "num_pages"):
            assert key in item, item


def test_pdfs_fts_query(client):
    """`q` runs an FTS5 match over page text, rolled up to whole documents."""
    r = client.get("/pdfs/documents?q=the&limit=5")
    if r.status_code == 503:
        # FTS index not built yet — surface as a skip, same as the rag.db pattern.
        pytest.skip("pages_fts not built; run scripts/pdfs/pdfs_index_fts.py")
    assert r.status_code == 200
    items = r.json()["items"]
    if not items:
        pytest.skip("pdfs.db has no PDFs; drop files in data/pdfs/incoming/ and ingest")
    # Each PDF appears once however many of its pages matched.
    doc_ids = [it["doc_id"] for it in items]
    assert len(doc_ids) == len(set(doc_ids)), doc_ids


def test_pdfs_sort_relevance_requires_q(client):
    """sort=relevance without q is a 400, mirroring the ecfr contract."""
    r = client.get("/pdfs/documents?sort=relevance")
    assert r.status_code == 400


def test_pdfs_bad_fts_syntax_400(client):
    """Malformed FTS5 syntax surfaces as a 400, not a 500."""
    r = client.get("/pdfs/documents", params={"q": '"unbalanced'})
    if r.status_code == 503:
        pytest.skip("pages_fts not built; run scripts/pdfs/pdfs_index_fts.py")
    assert r.status_code == 400


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

    # A DB whose paper_authors table is missing hits the author-join 503.
    app.dependency_overrides[db.arxiv] = lambda: ro_conn
    try:
        r = client.get("/arxiv/papers?limit=1")
        assert r.status_code == 503, r.text
        assert "arxiv_normalize_authors.py" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(db.arxiv, None)
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
