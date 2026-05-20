"""Tests for `scripts.arxiv_ingest` — schema, upsert, author normalization, state."""

import pathlib
import sqlite3
import sys

import pytest

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import arxiv_ingest  # noqa: E402
from arxiv_ingest import (  # noqa: E402
    _get_or_create_author,
    connect,
    create_schema,
    get_state,
    ingest_records,
    reset_data,
    set_state,
    upsert_paper,
)


def _record(
    arxiv_id: str = "2401.0001",
    oai_datestamp: str = "2024-01-22",
    title: str = "Test Paper",
    abstract: str = "Abstract.",
    authors: list[dict] | None = None,
) -> dict:
    """Synthesize one parsed-OAI dict for the tests."""
    if authors is None:
        authors = [
            {
                "keyname": "Smith",
                "forenames": "Alice",
                "affiliation": None,
                "display_name": "Alice Smith",
            },
            {
                "keyname": "Jones",
                "forenames": "Bob",
                "affiliation": "MIT",
                "display_name": "Bob Jones",
            },
        ]
    return {
        "id": arxiv_id,
        "oai_datestamp": oai_datestamp,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "categories": "cs.CL cs.LG",
        "primary_category": "cs.CL",
        "submitted_date": "2024-01-22",
        "updated_date": "2024-01-25",
        "doi": None,
        "journal_ref": None,
        "comments": "9 pages",
    }


@pytest.fixture
def conn(tmp_path: pathlib.Path):
    db = tmp_path / "arxiv.db"
    c = connect(db)
    try:
        yield c
    finally:
        c.close()


class TestCreateSchema:
    def test_all_tables_present(self, conn: sqlite3.Connection) -> None:
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        }
        assert {"papers", "authors", "paper_authors", "ingest_state"} <= names

    def test_idempotent(self, conn: sqlite3.Connection) -> None:
        # Calling again should not raise nor wipe data.
        upsert_paper(conn, _record())
        create_schema(conn)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1


class TestUpsertPaper:
    def test_first_insert_sets_action_inserted(self, conn: sqlite3.Connection) -> None:
        assert upsert_paper(conn, _record()) == "inserted"
        row = conn.execute("SELECT * FROM papers WHERE id = ?", ("2401.0001",)).fetchone()
        assert row["title"] == "Test Paper"
        assert row["oai_datestamp"] == "2024-01-22"
        # html_content / download_status / downloaded_at not set by ingest:
        assert row["html_content"] is None
        assert row["download_status"] is None
        assert row["downloaded_at"] is None

    def test_same_datestamp_skipped(self, conn: sqlite3.Connection) -> None:
        upsert_paper(conn, _record())
        assert upsert_paper(conn, _record()) == "skipped"

    def test_newer_datestamp_updates(self, conn: sqlite3.Connection) -> None:
        upsert_paper(conn, _record(oai_datestamp="2024-01-22"))
        action = upsert_paper(conn, _record(oai_datestamp="2024-02-01", title="Edited"))
        assert action == "updated"
        title = conn.execute("SELECT title FROM papers WHERE id = ?", ("2401.0001",)).fetchone()[0]
        assert title == "Edited"

    def test_update_preserves_html_content(self, conn: sqlite3.Connection) -> None:
        # The downloader is the only writer to html_content; an OAI re-harvest
        # of metadata must NOT clobber an already-downloaded paper's body.
        upsert_paper(conn, _record())
        conn.execute(
            "UPDATE papers SET html_content = ?, download_status = 'downloaded', "
            "downloaded_at = '2024-02-01T00:00:00+00:00' WHERE id = ?",
            ("<html>body</html>", "2401.0001"),
        )
        conn.commit()
        upsert_paper(conn, _record(oai_datestamp="2024-02-15"))
        row = conn.execute(
            "SELECT html_content, download_status, downloaded_at "
            "FROM papers WHERE id = ?",
            ("2401.0001",),
        ).fetchone()
        assert row["html_content"] == "<html>body</html>"
        assert row["download_status"] == "downloaded"
        assert row["downloaded_at"] == "2024-02-01T00:00:00+00:00"

    def test_paper_authors_built_with_positions(self, conn: sqlite3.Connection) -> None:
        upsert_paper(conn, _record())
        rows = conn.execute(
            "SELECT pa.position, a.display_name "
            "FROM paper_authors pa JOIN authors a ON a.id = pa.author_id "
            "WHERE pa.paper_id = ? ORDER BY pa.position",
            ("2401.0001",),
        ).fetchall()
        assert [(r["position"], r["display_name"]) for r in rows] == [
            (0, "Alice Smith"),
            (1, "Bob Jones"),
        ]

    def test_paper_authors_rebuilt_on_update(self, conn: sqlite3.Connection) -> None:
        upsert_paper(conn, _record())
        upsert_paper(
            conn,
            _record(
                oai_datestamp="2024-02-01",
                authors=[
                    {
                        "keyname": "Doe",
                        "forenames": "Jane",
                        "affiliation": None,
                        "display_name": "Jane Doe",
                    }
                ],
            ),
        )
        rows = conn.execute(
            "SELECT a.display_name FROM paper_authors pa "
            "JOIN authors a ON a.id = pa.author_id "
            "WHERE pa.paper_id = ? ORDER BY pa.position",
            ("2401.0001",),
        ).fetchall()
        assert [r["display_name"] for r in rows] == ["Jane Doe"]

    def test_authors_table_grows_monotonically(self, conn: sqlite3.Connection) -> None:
        # When a paper's authors change, the old `authors` rows remain
        # (they may still be referenced by other papers, or by an earlier
        # version of this paper if metadata advances yet again).
        upsert_paper(conn, _record())
        upsert_paper(
            conn,
            _record(
                oai_datestamp="2024-02-01",
                authors=[
                    {
                        "keyname": "Doe",
                        "forenames": "Jane",
                        "affiliation": None,
                        "display_name": "Jane Doe",
                    }
                ],
            ),
        )
        n_authors = conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
        assert n_authors == 3  # original 2 + 1 new


class TestGetOrCreateAuthor:
    def test_new_author_inserted(self, conn: sqlite3.Connection) -> None:
        author = {
            "keyname": "Smith",
            "forenames": "Alice",
            "affiliation": None,
            "display_name": "Alice Smith",
        }
        author_id = _get_or_create_author(conn, author)
        row = conn.execute("SELECT * FROM authors WHERE id = ?", (author_id,)).fetchone()
        assert row["keyname"] == "Smith"
        assert row["forenames"] == "Alice"
        assert row["affiliation"] is None
        assert row["display_name"] == "Alice Smith"

    def test_duplicate_returns_same_id(self, conn: sqlite3.Connection) -> None:
        author = {
            "keyname": "Smith",
            "forenames": "Alice",
            "affiliation": None,
            "display_name": "Alice Smith",
        }
        a = _get_or_create_author(conn, author)
        b = _get_or_create_author(conn, author)
        assert a == b
        assert conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0] == 1

    def test_null_affiliation_dedups(self, conn: sqlite3.Connection) -> None:
        # SQLite's UNIQUE treats NULL as not-equal-to-NULL (per SQL standard).
        # The SELECT-then-INSERT path must catch this case explicitly.
        author = {
            "keyname": "Smith",
            "forenames": "Alice",
            "affiliation": None,
            "display_name": "Alice Smith",
        }
        a = _get_or_create_author(conn, author)
        b = _get_or_create_author(conn, author)
        assert a == b
        assert conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0] == 1

    def test_different_affiliation_distinct_rows(self, conn: sqlite3.Connection) -> None:
        # Same name + different affiliations → distinct authors (same human at
        # different orgs, or different humans of the same name).
        a = _get_or_create_author(
            conn,
            {
                "keyname": "Smith",
                "forenames": "Alice",
                "affiliation": "MIT",
                "display_name": "Alice Smith",
            },
        )
        b = _get_or_create_author(
            conn,
            {
                "keyname": "Smith",
                "forenames": "Alice",
                "affiliation": "Stanford",
                "display_name": "Alice Smith",
            },
        )
        assert a != b
        assert conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0] == 2

    def test_display_name_updated_on_dedup_hit(self, conn: sqlite3.Connection) -> None:
        # Suffix folds into display_name but isn't part of the dedup key.
        # The same author arriving later with a different display_name (e.g.
        # gaining a suffix) updates the existing row in place.
        a = _get_or_create_author(
            conn,
            {
                "keyname": "Smith",
                "forenames": "Alice",
                "affiliation": None,
                "display_name": "Alice Smith",
            },
        )
        b = _get_or_create_author(
            conn,
            {
                "keyname": "Smith",
                "forenames": "Alice",
                "affiliation": None,
                "display_name": "Alice Smith Jr.",
            },
        )
        assert a == b
        assert conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0] == 1
        stored = conn.execute(
            "SELECT display_name FROM authors WHERE id = ?", (a,)
        ).fetchone()[0]
        assert stored == "Alice Smith Jr."


class TestIngestRecords:
    def test_accumulates_stats(self, conn: sqlite3.Connection) -> None:
        # Two new + one update + one skipped.
        records = [
            _record(arxiv_id="2401.0001"),
            _record(arxiv_id="2401.0002"),
        ]
        stats = ingest_records(conn, iter(records))
        assert stats == {"inserted": 2, "updated": 0, "skipped": 0}

        records = [
            _record(arxiv_id="2401.0001"),  # unchanged → skip
            _record(arxiv_id="2401.0002", oai_datestamp="2024-02-01", title="Edited"),  # update
            _record(arxiv_id="2401.0003"),  # new
        ]
        stats = ingest_records(conn, iter(records))
        assert stats == {"inserted": 1, "updated": 1, "skipped": 1}

    def test_progress_callback_fires_on_commit_boundary(
        self, conn: sqlite3.Connection
    ) -> None:
        seen: list[str] = []
        records = [_record(arxiv_id=f"2401.{i:04d}") for i in range(5)]
        # batch_size=2 should trigger the callback twice (after records 2 and 4).
        ingest_records(conn, iter(records), batch_size=2, progress=seen.append)
        assert len(seen) == 2
        assert "2 seen" in seen[0]


class TestState:
    def test_round_trip(self, conn: sqlite3.Connection) -> None:
        set_state(conn, "last_harvested_date", "2024-06-01")
        conn.commit()
        assert get_state(conn, "last_harvested_date") == "2024-06-01"

    def test_get_unset_returns_none(self, conn: sqlite3.Connection) -> None:
        assert get_state(conn, "absent") is None

    def test_set_replaces_existing(self, conn: sqlite3.Connection) -> None:
        set_state(conn, "k", "v1")
        conn.commit()
        set_state(conn, "k", "v2")
        conn.commit()
        assert get_state(conn, "k") == "v2"


class TestResetData:
    def test_clears_all_tables(self, conn: sqlite3.Connection) -> None:
        upsert_paper(conn, _record())
        set_state(conn, "last_harvested_date", "2024-06-01")
        conn.commit()
        reset_data(conn)
        for table in ("papers", "authors", "paper_authors", "ingest_state"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0

    def test_schema_survives(self, conn: sqlite3.Connection) -> None:
        upsert_paper(conn, _record())
        reset_data(conn)
        # Schema should still exist; we can upsert again afterwards.
        assert upsert_paper(conn, _record()) == "inserted"

    def test_resets_authors_autoincrement(self, conn: sqlite3.Connection) -> None:
        # Insert a paper (creates 2 authors with id=1, id=2).
        upsert_paper(conn, _record())
        max_id = conn.execute("SELECT MAX(id) FROM authors").fetchone()[0]
        assert max_id == 2

        reset_data(conn)

        # After reset, the next inserted author should start at id=1 again,
        # not continue from id=3 onwards.
        upsert_paper(conn, _record())
        first_id = conn.execute("SELECT MIN(id) FROM authors").fetchone()[0]
        assert first_id == 1

    def test_handles_freshly_created_db_without_sqlite_sequence(
        self, tmp_path: pathlib.Path
    ) -> None:
        # sqlite_sequence is created lazily on first AUTOINCREMENT fire.
        # reset_data on a brand-new DB (no inserts yet) shouldn't crash.
        db = tmp_path / "fresh.db"
        c = connect(db)
        try:
            reset_data(c)  # should not raise
        finally:
            c.close()


class TestArxivIngestImport:
    def test_module_constants_match_plan(self) -> None:
        assert arxiv_ingest.DEFAULT_FROM == "2021-01-01"
        assert arxiv_ingest.DEFAULT_DB.name == "arxiv.db"
        assert arxiv_ingest.BATCH_SIZE == 1000
