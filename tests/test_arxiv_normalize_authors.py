"""Tests for `scripts.arxiv_normalize_authors` — legacy JSON -> normalized authors."""

import json
import pathlib
import sqlite3
import sys

import pytest

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import arxiv_ingest  # noqa: E402
import arxiv_normalize_authors  # noqa: E402
from arxiv_normalize_authors import (  # noqa: E402
    author_dict_from_legacy,
    backfill,
    split_name,
)


def _seed_paper(
    conn: sqlite3.Connection,
    paper_id: str,
    authors_json: str,
) -> None:
    """Insert one paper row with the legacy JSON authors column populated."""
    cols = (
        "id",
        "oai_datestamp",
        "title",
        "abstract",
        "authors",
        "categories",
        "primary_category",
        "submitted_date",
    )
    conn.execute(
        f"INSERT INTO papers ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' * len(cols))})",
        (
            paper_id,
            "2024-01-22",
            "Title",
            "Abstract.",
            authors_json,
            "cs.CL",
            "cs.CL",
            "2024-01-22",
        ),
    )


@pytest.fixture
def conn(tmp_path: pathlib.Path):
    db = tmp_path / "arxiv.db"
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    # Use arxiv_ingest's schema to mirror what the production DB will look
    # like after the rewire. We additionally need the legacy `authors` JSON
    # column on `papers` to seed the backfill input — arxiv_ingest's schema
    # doesn't include it, but ALTER TABLE makes it available for tests.
    arxiv_ingest.create_schema(c)
    c.execute("ALTER TABLE papers ADD COLUMN authors TEXT")
    c.commit()
    try:
        yield c
    finally:
        c.close()


class TestSplitName:
    def test_two_words(self) -> None:
        assert split_name("Alice Smith") == ("Smith", "Alice")

    def test_three_words_takes_last_as_keyname(self) -> None:
        assert split_name("Bob C. Jones") == ("Jones", "Bob C.")

    def test_mononym(self) -> None:
        assert split_name("Plato") == ("Plato", "")

    def test_whitespace_stripped(self) -> None:
        assert split_name("  Alice  Smith  ") == ("Smith", "Alice")

    def test_empty_string(self) -> None:
        assert split_name("") == ("", "")

    def test_only_whitespace(self) -> None:
        assert split_name("   ") == ("", "")


class TestAuthorDictFromLegacy:
    def test_typical(self) -> None:
        assert author_dict_from_legacy("Alice Smith") == {
            "keyname": "Smith",
            "forenames": "Alice",
            "affiliation": None,
            "display_name": "Alice Smith",
        }

    def test_mononym(self) -> None:
        d = author_dict_from_legacy("Plato")
        assert d == {
            "keyname": "Plato",
            "forenames": "",
            "affiliation": None,
            "display_name": "Plato",
        }

    def test_empty_returns_none(self) -> None:
        assert author_dict_from_legacy("") is None
        assert author_dict_from_legacy("   ") is None

    def test_display_name_whitespace_normalized(self) -> None:
        # Internal double spaces in the source string don't leak into
        # display_name — it gets the same single-space form that split_name
        # produced for keyname/forenames.
        d = author_dict_from_legacy("  Alice   Smith  ")
        assert d == {
            "keyname": "Smith",
            "forenames": "Alice",
            "affiliation": None,
            "display_name": "Alice Smith",
        }


class TestBackfill:
    def test_populates_authors_and_paper_authors(
        self, conn: sqlite3.Connection
    ) -> None:
        _seed_paper(conn, "2401.0001", json.dumps(["Alice Smith", "Bob C. Jones"]))
        conn.commit()

        stats = backfill(conn)
        assert stats["papers"] == 1
        assert stats["links"] == 2

        rows = conn.execute(
            "SELECT a.display_name, a.keyname, a.forenames, pa.position "
            "FROM paper_authors pa JOIN authors a ON a.id = pa.author_id "
            "WHERE pa.paper_id = ? ORDER BY pa.position",
            ("2401.0001",),
        ).fetchall()
        assert [(r["display_name"], r["keyname"], r["forenames"], r["position"]) for r in rows] == [
            ("Alice Smith", "Smith", "Alice", 0),
            ("Bob C. Jones", "Jones", "Bob C.", 1),
        ]

    def test_authors_deduped_across_papers(self, conn: sqlite3.Connection) -> None:
        # Same author on two papers should be one row in authors.
        _seed_paper(conn, "2401.0001", json.dumps(["Alice Smith"]))
        _seed_paper(conn, "2401.0002", json.dumps(["Alice Smith", "Bob Jones"]))
        conn.commit()

        backfill(conn)
        n_authors = conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
        assert n_authors == 2

    def test_empty_authors_column_handled(self, conn: sqlite3.Connection) -> None:
        _seed_paper(conn, "2401.0001", "")
        _seed_paper(conn, "2401.0002", "null")
        _seed_paper(conn, "2401.0003", "[]")
        conn.commit()

        stats = backfill(conn)
        assert stats["papers"] == 3
        # "" → counted as empty (skipped before json parse).
        # "null" → json parses to None, then iteration over None would crash —
        # but we guard with `isinstance(name, str)` so list() on None... wait,
        # actually json.loads("null") is None, and `for position, name in
        # enumerate(None)` raises TypeError. Let's just check no crash.
        assert stats["links"] == 0
        # paper_authors should be empty.
        assert conn.execute("SELECT COUNT(*) FROM paper_authors").fetchone()[0] == 0

    def test_malformed_json_counted(self, conn: sqlite3.Connection) -> None:
        _seed_paper(conn, "2401.0001", "not valid json [")
        conn.commit()

        stats = backfill(conn)
        assert stats["malformed_json"] == 1
        assert stats["links"] == 0

    def test_idempotent_rerun(self, conn: sqlite3.Connection) -> None:
        _seed_paper(conn, "2401.0001", json.dumps(["Alice Smith", "Bob Jones"]))
        conn.commit()

        backfill(conn)
        first_count = conn.execute("SELECT COUNT(*) FROM paper_authors").fetchone()[0]
        first_authors = conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]

        backfill(conn)
        second_count = conn.execute("SELECT COUNT(*) FROM paper_authors").fetchone()[0]
        second_authors = conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]

        # Idempotent: same number of links, no dupes in authors.
        assert first_count == second_count == 2
        assert first_authors == second_authors == 2

    def test_non_string_entries_ignored(self, conn: sqlite3.Connection) -> None:
        # Defensive — if the JSON ever contained non-string entries
        # (it shouldn't in practice), skip them rather than crash.
        _seed_paper(conn, "2401.0001", json.dumps(["Alice Smith", 42, None]))
        conn.commit()

        stats = backfill(conn)
        assert stats["links"] == 1
        assert conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0] == 1


class TestFreshSchemaExit:
    """`main` should exit cleanly when the DB is already on the new schema."""

    def test_main_returns_0_on_fresh_schema(self, tmp_path: pathlib.Path) -> None:
        # A DB created purely by arxiv_ingest.create_schema has no legacy
        # `papers.authors` JSON column. The backfill should detect this and
        # bail without crashing on `no such column: authors`.
        db = tmp_path / "fresh.db"
        c = sqlite3.connect(db)
        arxiv_ingest.create_schema(c)
        c.close()

        # main runs without exception and returns 0.
        rc = arxiv_normalize_authors.main(["--db", str(db)])
        assert rc == 0

    def test_has_legacy_authors_column_detection(
        self, tmp_path: pathlib.Path
    ) -> None:
        # On a fresh-schema DB: False.
        db = tmp_path / "fresh.db"
        c = sqlite3.connect(db)
        arxiv_ingest.create_schema(c)
        assert arxiv_normalize_authors._has_legacy_authors_column(c) is False
        # After ALTER TABLE to add the legacy column: True.
        c.execute("ALTER TABLE papers ADD COLUMN authors TEXT")
        c.commit()
        assert arxiv_normalize_authors._has_legacy_authors_column(c) is True
        c.close()
