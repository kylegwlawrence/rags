"""Tests for `rag.openalex_fetch` — PDF candidate-walking, magic-byte validation,
fallback, retry, and the body_status bookkeeping helpers.

HTTP is exercised via ``httpx.MockTransport``, injected by monkeypatching
``rag.openalex_fetch.httpx.Client`` — the same seam the arxiv download tests use.
"""

import pathlib
import sqlite3

import httpx
import pytest

from rag import openalex_fetch
from rag.openalex_fetch import (
    NoPdfAvailable,
    ensure_body_status_table,
    fetch_work_pdf,
    record_body_status,
)

PDF_BYTES = b"%PDF-1.4\n%fake pdf body\n"
HTML_BYTES = b"<html><body>landing page</body></html>"


def _no_sleep(_: float) -> None:
    return None


def _patch_client(monkeypatch: pytest.MonkeyPatch, responses: list[httpx.Response]) -> None:
    """Make `rag.openalex_fetch` build clients backed by a scripted MockTransport."""
    iterator = iter(responses)

    def handler(_request: httpx.Request) -> httpx.Response:
        return next(iterator)

    transport = httpx.MockTransport(handler)
    base = httpx.Client

    class _MockClient(base):  # type: ignore[misc, valid-type]
        def __init__(self, **kw):
            kw.setdefault("transport", transport)
            super().__init__(**kw)

    monkeypatch.setattr(openalex_fetch.httpx, "Client", _MockClient)


class TestFetchWorkPdf:
    def test_first_url_pdf(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        _patch_client(monkeypatch, [httpx.Response(200, content=PDF_BYTES)])
        dest = tmp_path / "W1.pdf"
        nbytes, src = fetch_work_pdf(
            ["http://pub/a.pdf", "http://repo/a"], dest,
            user_agent="x", sleep=_no_sleep,
        )
        assert nbytes == len(PDF_BYTES)
        assert src == "http://pub/a.pdf"
        assert dest.read_bytes() == PDF_BYTES
        assert not dest.with_name(dest.name + ".part").exists()

    def test_falls_back_to_second_url(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        # First URL is a (non-PDF) landing page; second is the real PDF.
        _patch_client(monkeypatch, [
            httpx.Response(200, content=HTML_BYTES),
            httpx.Response(200, content=PDF_BYTES),
        ])
        dest = tmp_path / "W2.pdf"
        nbytes, src = fetch_work_pdf(
            ["http://pub/landing", "http://repo/a.pdf"], dest,
            user_agent="x", sleep=_no_sleep,
        )
        assert src == "http://repo/a.pdf"
        assert dest.read_bytes() == PDF_BYTES

    def test_404_then_pdf(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        _patch_client(monkeypatch, [
            httpx.Response(404),
            httpx.Response(200, content=PDF_BYTES),
        ])
        dest = tmp_path / "W3.pdf"
        _, src = fetch_work_pdf(
            ["http://pub/missing", "http://repo/a.pdf"], dest,
            user_agent="x", sleep=_no_sleep,
        )
        assert src == "http://repo/a.pdf"

    def test_no_pdf_anywhere_raises(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        _patch_client(monkeypatch, [
            httpx.Response(200, content=HTML_BYTES),
            httpx.Response(403),
        ])
        dest = tmp_path / "W4.pdf"
        with pytest.raises(NoPdfAvailable):
            fetch_work_pdf(
                ["http://pub/landing", "http://repo/forbidden"], dest,
                user_agent="x", sleep=_no_sleep,
            )
        assert not dest.exists()
        assert not dest.with_name(dest.name + ".part").exists()

    def test_no_candidates_raises(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(NoPdfAvailable):
            fetch_work_pdf([None, None], tmp_path / "W5.pdf", user_agent="x")

    def test_5xx_retries_then_other_url_succeeds(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        sleeps: list[float] = []
        # First URL: 503 on every attempt (exhausts MAX_ATTEMPTS -> raises);
        # the error is remembered and the second URL serves the PDF.
        _patch_client(monkeypatch, [
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, content=PDF_BYTES),
        ])
        dest = tmp_path / "W6.pdf"
        _, src = fetch_work_pdf(
            ["http://flaky", "http://repo/a.pdf"], dest,
            user_agent="x", sleep=sleeps.append,
        )
        assert src == "http://repo/a.pdf"
        assert sleeps  # backoff happened on the flaky URL

    def test_persistent_5xx_propagates(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        # Only URL keeps 5xx-ing — surfaces as a retryable httpx error, not no_pdf.
        _patch_client(monkeypatch, [httpx.Response(503)] * 3)
        with pytest.raises(httpx.HTTPError):
            fetch_work_pdf(
                ["http://flaky"], tmp_path / "W7.pdf",
                user_agent="x", sleep=_no_sleep,
            )


class TestBodyStatus:
    def test_record_and_read_back(self, tmp_path: pathlib.Path) -> None:
        con = sqlite3.connect(tmp_path / "openalex.db")
        con.row_factory = sqlite3.Row
        ensure_body_status_table(con)
        ensure_body_status_table(con)  # idempotent

        record_body_status(con, "W1", "fetched", pdf_path="W1.pdf",
                           nbytes=1234, source_url="http://repo/a.pdf")
        record_body_status(con, "W2", "no_pdf", note="paywalled")
        # Upsert replaces a prior row for the same work.
        record_body_status(con, "W2", "fetched", pdf_path="W2.pdf", nbytes=9)

        rows = {r["work_id"]: r for r in con.execute("SELECT * FROM body_status")}
        assert rows["W1"]["status"] == "fetched"
        assert rows["W1"]["bytes"] == 1234
        assert rows["W2"]["status"] == "fetched"  # replaced
        assert rows["W2"]["bytes"] == 9
        con.close()
