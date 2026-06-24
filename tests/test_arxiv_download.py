"""Tests for `scripts.arxiv_download` — DB selection logic, status writes, retry path.

HTTP transport is exercised via ``httpx.MockTransport`` for the single
``fetch_html`` test that hits 200 / 404 paths; the rest of the suite uses
the ``fetch_fn`` dependency-injection seam to keep the test boilerplate
short and the test deps unchanged (no ``respx``).
"""

import pathlib
import sqlite3
import sys

import httpx
import pytest

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "arxiv"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import arxiv_download  # noqa: E402
import arxiv_ingest  # noqa: E402
from arxiv_download import (  # noqa: E402
    HTML_URL_TEMPLATE,
    download_papers,
    fetch_html,
    select_pending,
)


def _record(arxiv_id: str, submitted_date: str = "2024-01-22") -> dict:
    """One minimal parsed-OAI dict, suitable for arxiv_ingest.upsert_paper."""
    return {
        "id": arxiv_id,
        "oai_datestamp": "2024-01-22",
        "title": f"Paper {arxiv_id}",
        "abstract": "Abstract.",
        "authors": [],
        "categories": "cs.CL",
        "primary_category": "cs.CL",
        "submitted_date": submitted_date,
        "updated_date": None,
        "doi": None,
        "journal_ref": None,
        "comments": None,
    }


@pytest.fixture
def conn(tmp_path: pathlib.Path):
    db = tmp_path / "arxiv.db"
    c = arxiv_ingest.connect(db)
    try:
        yield c
    finally:
        c.close()


def _no_sleep(_: float) -> None:
    return None


class TestSelectPending:
    def test_only_null_and_retry(self, conn: sqlite3.Connection) -> None:
        # Three papers: one NULL (pending), one 'downloaded' (done),
        # one 'retry' (pending), one 'no_html' (done).
        for i, status in enumerate([None, "downloaded", "retry", "no_html"]):
            arxiv_ingest.upsert_paper(conn, _record(f"2401.000{i}"))
            if status is not None:
                conn.execute(
                    "UPDATE papers SET download_status = ? WHERE id = ?",
                    (status, f"2401.000{i}"),
                )
        conn.commit()
        ids = set(select_pending(conn, limit=None, force=False))
        assert ids == {"2401.0000", "2401.0002"}

    def test_newest_first_ordering(self, conn: sqlite3.Connection) -> None:
        arxiv_ingest.upsert_paper(conn, _record("2401.0001", submitted_date="2023-12-01"))
        arxiv_ingest.upsert_paper(conn, _record("2401.0002", submitted_date="2024-06-01"))
        arxiv_ingest.upsert_paper(conn, _record("2401.0003", submitted_date="2024-03-01"))
        ids = select_pending(conn, limit=None, force=False)
        assert ids == ["2401.0002", "2401.0003", "2401.0001"]

    def test_limit_applied(self, conn: sqlite3.Connection) -> None:
        for i in range(5):
            arxiv_ingest.upsert_paper(conn, _record(f"2401.000{i}"))
        ids = select_pending(conn, limit=2, force=False)
        assert len(ids) == 2

    def test_force_includes_already_downloaded(self, conn: sqlite3.Connection) -> None:
        arxiv_ingest.upsert_paper(conn, _record("2401.0001"))
        conn.execute(
            "UPDATE papers SET download_status = 'downloaded' WHERE id = ?",
            ("2401.0001",),
        )
        conn.commit()
        ids_normal = select_pending(conn, limit=None, force=False)
        ids_force = select_pending(conn, limit=None, force=True)
        assert ids_normal == []
        assert ids_force == ["2401.0001"]


class TestDownloadPapers:
    @pytest.fixture
    def bodies_dir(self, tmp_path: pathlib.Path) -> pathlib.Path:
        return tmp_path / "bodies"

    def test_success_writes_html_and_status(
        self, conn: sqlite3.Connection, bodies_dir: pathlib.Path
    ) -> None:
        arxiv_ingest.upsert_paper(conn, _record("2401.0001"))
        stats = download_papers(
            conn,
            ["2401.0001"],
            bodies_dir=bodies_dir,
            fetch_fn=lambda _id: "<html>body</html>",
            sleep=_no_sleep,
        )
        assert stats == {
            "downloaded": 1,
            "downloaded_pdf": 0,
            "no_body": 0,
            "error": 0,
        }
        row = conn.execute(
            "SELECT html_content, download_status, downloaded_at "
            "FROM papers WHERE id = ?",
            ("2401.0001",),
        ).fetchone()
        assert row["html_content"] == "<html>body</html>"
        assert row["download_status"] == "downloaded"
        assert row["downloaded_at"] is not None

    def test_no_html_no_pdf_writes_no_body(
        self, conn: sqlite3.Connection, bodies_dir: pathlib.Path
    ) -> None:
        arxiv_ingest.upsert_paper(conn, _record("2401.0001"))
        stats = download_papers(
            conn,
            ["2401.0001"],
            bodies_dir=bodies_dir,
            fetch_fn=lambda _id: None,
            pdf_fetch_fn=lambda _id: None,
            sleep=_no_sleep,
        )
        assert stats == {
            "downloaded": 0,
            "downloaded_pdf": 0,
            "no_body": 1,
            "error": 0,
        }
        row = conn.execute(
            "SELECT html_content, pdf_text, download_status FROM papers WHERE id = ?",
            ("2401.0001",),
        ).fetchone()
        assert row["html_content"] is None
        assert row["pdf_text"] is None
        assert row["download_status"] == "no_body"

    def test_html_404_pdf_fallback_succeeds(
        self, conn: sqlite3.Connection, bodies_dir: pathlib.Path
    ) -> None:
        arxiv_ingest.upsert_paper(conn, _record("2401.0001"))
        stats = download_papers(
            conn,
            ["2401.0001"],
            bodies_dir=bodies_dir,
            fetch_fn=lambda _id: None,
            pdf_fetch_fn=lambda _id: b"%PDF-1.7 fake bytes",
            extract_fn=lambda _b: "extracted text",
            sleep=_no_sleep,
        )
        assert stats == {
            "downloaded": 0,
            "downloaded_pdf": 1,
            "no_body": 0,
            "error": 0,
        }
        row = conn.execute(
            "SELECT html_content, pdf_text, download_status FROM papers WHERE id = ?",
            ("2401.0001",),
        ).fetchone()
        assert row["html_content"] is None
        assert row["pdf_text"] == "extracted text"
        assert row["download_status"] == "downloaded_pdf"
        # The raw PDF is kept on disk for debugging.
        saved = bodies_dir / "2401.0001.pdf"
        assert saved.read_bytes() == b"%PDF-1.7 fake bytes"

    def test_transient_html_error_leaves_status_unchanged(
        self, conn: sqlite3.Connection, bodies_dir: pathlib.Path
    ) -> None:
        arxiv_ingest.upsert_paper(conn, _record("2401.0001"))

        def raises(_id: str) -> str:
            raise httpx.ConnectError("simulated network outage")

        stats = download_papers(
            conn,
            ["2401.0001"],
            bodies_dir=bodies_dir,
            fetch_fn=raises,
            sleep=_no_sleep,
        )
        assert stats == {
            "downloaded": 0,
            "downloaded_pdf": 0,
            "no_body": 0,
            "error": 1,
        }
        # download_status stays NULL so the next run will retry.
        row = conn.execute(
            "SELECT download_status FROM papers WHERE id = ?", ("2401.0001",)
        ).fetchone()
        assert row["download_status"] is None

    def test_transient_pdf_error_leaves_status_unchanged(
        self, conn: sqlite3.Connection, bodies_dir: pathlib.Path
    ) -> None:
        arxiv_ingest.upsert_paper(conn, _record("2401.0001"))

        def pdf_raises(_id: str) -> bytes:
            raise httpx.ConnectError("simulated network outage")

        stats = download_papers(
            conn,
            ["2401.0001"],
            bodies_dir=bodies_dir,
            fetch_fn=lambda _id: None,
            pdf_fetch_fn=pdf_raises,
            sleep=_no_sleep,
        )
        assert stats == {
            "downloaded": 0,
            "downloaded_pdf": 0,
            "no_body": 0,
            "error": 1,
        }
        row = conn.execute(
            "SELECT download_status FROM papers WHERE id = ?", ("2401.0001",)
        ).fetchone()
        assert row["download_status"] is None

    def test_mixed_results_per_paper(
        self, conn: sqlite3.Connection, bodies_dir: pathlib.Path
    ) -> None:
        for i in range(3):
            arxiv_ingest.upsert_paper(conn, _record(f"2401.000{i}"))

        def fake(arxiv_id: str) -> str | None:
            if arxiv_id == "2401.0000":
                return "<html>ok</html>"
            if arxiv_id == "2401.0001":
                return None
            raise httpx.ConnectError("nope")

        stats = download_papers(
            conn,
            ["2401.0000", "2401.0001", "2401.0002"],
            bodies_dir=bodies_dir,
            fetch_fn=fake,
            pdf_fetch_fn=lambda _id: None,
            sleep=_no_sleep,
        )
        assert stats == {
            "downloaded": 1,
            "downloaded_pdf": 0,
            "no_body": 1,
            "error": 1,
        }

    def test_progress_callback_fires_every_ten(
        self, conn: sqlite3.Connection, bodies_dir: pathlib.Path
    ) -> None:
        ids = []
        for i in range(25):
            arxiv_ingest.upsert_paper(conn, _record(f"2401.{i:04d}"))
            ids.append(f"2401.{i:04d}")
        seen: list[str] = []
        download_papers(
            conn,
            ids,
            bodies_dir=bodies_dir,
            fetch_fn=lambda _id: "<html/>",
            sleep=_no_sleep,
            progress=seen.append,
        )
        # 25 papers / 10 per progress line = 2 callbacks (at 10 and 20).
        assert len(seen) == 2


class TestFetchHtml:
    """Exercise the real HTTP layer via httpx.MockTransport."""

    def _client_factory(
        self, responses: list[httpx.Response]
    ) -> type[httpx.Client]:
        iterator = iter(responses)

        def handler(_request: httpx.Request) -> httpx.Response:
            return next(iterator)

        transport = httpx.MockTransport(handler)
        base = httpx.Client  # capture for nesting

        class _MockClient(base):  # type: ignore[misc, valid-type]
            def __init__(self, **kw):  # noqa: D401
                kw.setdefault("transport", transport)
                super().__init__(**kw)

        return _MockClient

    def test_200_returns_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            arxiv_download.httpx,
            "Client",
            self._client_factory([httpx.Response(200, text="<html>ok</html>")]),
        )
        body = fetch_html("2401.0001", sleep=_no_sleep)
        assert body == "<html>ok</html>"

    def test_404_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            arxiv_download.httpx,
            "Client",
            self._client_factory([httpx.Response(404)]),
        )
        body = fetch_html("2401.0001", sleep=_no_sleep)
        assert body is None

    def test_5xx_retries_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr(
            arxiv_download.httpx,
            "Client",
            self._client_factory(
                [
                    httpx.Response(503),
                    httpx.Response(200, text="<html>ok</html>"),
                ]
            ),
        )
        body = fetch_html("2401.0001", sleep=sleeps.append)
        assert body == "<html>ok</html>"
        # One backoff sleep happened between the 503 and the 200.
        assert sleeps

    def test_429_honors_retry_after(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr(
            arxiv_download.httpx,
            "Client",
            self._client_factory(
                [
                    httpx.Response(429, headers={"Retry-After": "7"}),
                    httpx.Response(200, text="ok"),
                ]
            ),
        )
        fetch_html("2401.0001", sleep=sleeps.append)
        assert 7.0 in sleeps


class TestModuleConfig:
    def test_url_template_has_placeholder(self) -> None:
        assert "{arxiv_id}" in HTML_URL_TEMPLATE

    def test_user_agent_includes_mailto(self) -> None:
        assert "mailto:" in arxiv_download.USER_AGENT
