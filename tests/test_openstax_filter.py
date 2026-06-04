"""API tests for the metadata filters on `GET /openstax/chunks`.

Rather than depend on the real `data/openstax/*.db`, these build tiny throwaway
SQLite DBs (a source DB with `books`/`sections`, a RAG DB seeded via the real
schema + embedder) and swap them in through FastAPI dependency overrides. The
embedder is stubbed so no Ollama is needed.
"""

import sqlite3

import pytest
import sqlite_vec

from api import db
from api.main import app
from rag import Doc, embedder
from rag.chunker import chunk_doc
from rag.embed_one import embed_doc
from rag.schema import create_rag_schema

# (section_id, book_id, subject, chapter_number, chapter_title, objectives, body)
_SECTIONS = [
    ("calc/s1", "calc", "mathematics", 1, "Limits",
     "Understand limits", "Energy and the derivative of motion. " * 40),
    ("bio/s1", "bio", "science", 2, "Cells",
     "Describe the cell", "Energy flows through the living cell. " * 40),
]


@pytest.fixture(autouse=True)
def _fake_embedder(monkeypatch):
    def fake_batch(texts, base_url=embedder.OLLAMA_URL):
        return [[0.0] * embedder.EMBEDDING_DIM for _ in texts]

    def fake_one(text, base_url=embedder.OLLAMA_URL):
        return [0.0] * embedder.EMBEDDING_DIM

    monkeypatch.setattr(embedder, "embed_texts_batch", fake_batch)
    monkeypatch.setattr(embedder, "embed_text", fake_one)


@pytest.fixture
def overridden(tmp_path):
    """Build source + RAG DBs, wire them into the app via dependency overrides."""
    # check_same_thread=False: TestClient serves the request from a worker
    # thread, but the connection is created here in the test thread.
    src = sqlite3.connect(tmp_path / "openstax.db", check_same_thread=False)
    src.row_factory = sqlite3.Row
    src.executescript(
        "CREATE TABLE books (book_id TEXT PRIMARY KEY, title TEXT, subject TEXT);"
        "CREATE TABLE sections (section_id TEXT PRIMARY KEY, book_id TEXT, "
        "chapter_number INTEGER, chapter_title TEXT, objectives TEXT);"
    )
    seen_books = set()
    rag = sqlite3.connect(tmp_path / "openstax_rag.db", check_same_thread=False)
    rag.row_factory = sqlite3.Row
    rag.enable_load_extension(True)
    sqlite_vec.load(rag)
    rag.enable_load_extension(False)
    create_rag_schema(rag)
    for section_id, book_id, subject, ch_no, ch_title, obj, body in _SECTIONS:
        if book_id not in seen_books:
            src.execute(
                "INSERT INTO books VALUES (?, ?, ?)", (book_id, book_id.title(), subject)
            )
            seen_books.add(book_id)
        src.execute(
            "INSERT INTO sections VALUES (?, ?, ?, ?, ?)",
            (section_id, book_id, ch_no, ch_title, obj),
        )
        embed_doc(
            rag,
            Doc(doc_id=section_id, title=book_id.title(), version="v1",
                text=body, section=ch_title),
            chunk_fn=chunk_doc,
            chunk_size=500,
        )
    src.commit()

    app.dependency_overrides[db.openstax] = lambda: src
    app.dependency_overrides[db.openstax_rag] = lambda: rag
    yield
    src.close()
    rag.close()


def _chunks(client, **params):
    r = client.get("/openstax/chunks", params={"q": "energy", **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_no_filter_searches_all_books(client, overridden):
    books = {it["book_id"] for it in _chunks(client)}
    assert books == {"calc", "bio"}


def test_subject_filter_scopes_results(client, overridden):
    items = _chunks(client, subject="mathematics")
    assert items
    assert {it["book_id"] for it in items} == {"calc"}
    assert all(it["subject"] == "mathematics" for it in items)


def test_book_id_filter_scopes_results(client, overridden):
    items = _chunks(client, book_id="bio")
    assert {it["book_id"] for it in items} == {"bio"}


def test_multi_value_book_id_is_or(client, overridden):
    # Repeated query params → list → OR (the prerequisite-expansion case).
    items = _chunks(client, book_id=["calc", "bio"])
    assert {it["book_id"] for it in items} == {"calc", "bio"}


def test_subject_and_book_intersect(client, overridden):
    # AND across filter types: science subject but a maths book → nothing.
    items = _chunks(client, subject="science", book_id="calc")
    assert items == []


def test_chapter_number_filter(client, overridden):
    items = _chunks(client, book_id="bio", chapter_number=2)
    assert {it["book_id"] for it in items} == {"bio"}
    assert _chunks(client, book_id="bio", chapter_number=99) == []


def test_unknown_subject_returns_empty_not_error(client, overridden):
    assert _chunks(client, subject="underwater-basket-weaving") == []


def test_provenance_fields_populated(client, overridden):
    item = next(it for it in _chunks(client, book_id="calc"))
    assert item["subject"] == "mathematics"
    assert item["chapter_number"] == 1
    assert item["chapter_title"] == "Limits"
    assert item["objectives"] == "Understand limits"
