"""Fetch one OpenAlex work's open-access PDF to disk.

OpenAlex serves no body text itself — only open-access *pointers* (``pdf_url`` /
``oa_url``) to a free copy hosted by some third-party publisher or repository.
This module walks those pointers, downloads the first one that is really a PDF,
and saves it atomically so the file can flow into the ``pdfs`` ingest pipeline.

Shared by the bulk downloader (``scripts/openalex/openalex_fetch_bodies.py``)
and the API's on-demand download route (``POST /openalex/works/{id}/download``),
so a work fetched either way goes through identical request logic — the same
split as ``rag/arxiv_fetch.py`` (arXiv) and ``rag/sec_filing.py`` (SEC). The
``body_status`` bookkeeping schema lives here too, so the two callers can't
drift on its shape.
"""

import os
import sqlite3
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx

PDF_MAGIC = b"%PDF-"
REQUEST_TIMEOUT = 180.0
MAX_ATTEMPTS = 3
BACKOFF_BASE = 5.0
CHUNK_SIZE = 65536

# Terminal outcomes a resumed bulk run skips; 'error' is transient (retried).
TERMINAL_STATUSES = ("fetched", "no_pdf")


class NoPdfAvailable(Exception):
    """No candidate URL yielded a real PDF (all 404 / 403 / non-PDF body).

    Terminal — re-trying won't help (paywalled or genuinely absent), so callers
    record it as ``no_pdf`` rather than the retryable ``error``.
    """


def fetch_work_pdf(
    urls: Sequence[str | None],
    dest: str | Path,
    *,
    user_agent: str,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, str]:
    """Download the first candidate URL that is really a PDF to ``dest``.

    ``urls`` are tried in order (typically ``[pdf_url, oa_url]``); empty/None
    entries are ignored. The PDF is streamed to a ``.part`` temp file and renamed
    into place only after the ``%PDF-`` magic bytes confirm it, so a failed or
    non-PDF response never leaves a poisoned file for ``pdfs_ingest`` to pick up.

    Returns ``(bytes_written, source_url)``. Raises :class:`NoPdfAvailable` when
    every URL is reachable but none is a PDF (terminal — don't retry). Raises
    ``httpx.HTTPError`` when a URL keeps failing with 429 / 5xx / a network error
    after ``MAX_ATTEMPTS`` (transient — a later run can retry).

    ``user_agent`` should carry a contact ``mailto:`` (polite-access etiquette).
    ``sleep`` is injectable so tests can run without real backoff waits.
    """
    candidates = [u for u in urls if u]
    if not candidates:
        raise NoPdfAvailable("no candidate URL")

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    headers = {"User-Agent": user_agent, "Accept": "application/pdf,*/*"}
    last_network_error: httpx.HTTPError | None = None

    try:
        with httpx.Client(
            timeout=REQUEST_TIMEOUT, headers=headers, follow_redirects=True
        ) as client:
            for url in candidates:
                try:
                    written = _stream_pdf(client, url, tmp, sleep)
                except httpx.HTTPError as exc:
                    # Connection error / persistent 5xx on this URL — remember it
                    # and try the next candidate before giving up.
                    last_network_error = exc
                    continue
                if written is not None:
                    os.replace(tmp, dest)
                    return written, url
        # No candidate produced a PDF.
        if last_network_error is not None:
            raise last_network_error  # transient — let the caller retry later
        raise NoPdfAvailable(f"no PDF at any of {len(candidates)} candidate URL(s)")
    finally:
        if tmp.exists():
            tmp.unlink()


def _stream_pdf(
    client: httpx.Client,
    url: str,
    tmp: Path,
    sleep: Callable[[float], None],
) -> int | None:
    """Stream ``url`` to ``tmp`` if it is a real PDF; return bytes written.

    Returns ``None`` if the URL is reachable but isn't a usable PDF: a 404 / 403
    / other 4xx (paywalled or absent), or a 200 whose body doesn't start with the
    ``%PDF-`` magic (e.g. an HTML landing page). Retries 429 / 5xx up to
    ``MAX_ATTEMPTS`` with backoff (honoring ``Retry-After``); raises
    ``httpx.HTTPStatusError`` if they persist. Network/timeout errors propagate.
    """
    for attempt in range(MAX_ATTEMPTS):
        with client.stream("GET", url) as resp:
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                if attempt == MAX_ATTEMPTS - 1:
                    resp.raise_for_status()
                retry_after = resp.headers.get("Retry-After", "")
                wait = (
                    float(retry_after)
                    if retry_after and retry_after.replace(".", "", 1).isdigit()
                    else BACKOFF_BASE * (attempt + 1)
                )
                sleep(wait)
                continue
            if resp.status_code >= 400:
                # 404/403/401/410… not accessible — terminal for this URL.
                return None

            chunks = resp.iter_bytes(CHUNK_SIZE)
            first = next(chunks, b"")
            if not first.startswith(PDF_MAGIC):
                return None  # reachable, but not a PDF (likely a landing page)
            written = len(first)
            with open(tmp, "wb") as fh:
                fh.write(first)
                for chunk in chunks:
                    fh.write(chunk)
                    written += len(chunk)
            return written or None
    raise RuntimeError("unreachable: MAX_ATTEMPTS exhausted without raising")


# --- body_status bookkeeping (shared schema for the script + the route) --------


def ensure_body_status_table(con: sqlite3.Connection) -> None:
    """Create the resumability table in an OpenAlex DB if it's missing."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS body_status (
            work_id    TEXT PRIMARY KEY,
            status     TEXT NOT NULL,   -- 'fetched' | 'no_pdf' | 'error'
            pdf_path   TEXT,            -- saved PDF path, relative to the out-dir
            bytes      INTEGER,
            source_url TEXT,            -- which candidate URL actually served it
            note       TEXT,            -- error message / skip reason
            updated_at TEXT NOT NULL
        )
        """
    )
    con.commit()


def record_body_status(
    con: sqlite3.Connection,
    work_id: str,
    status: str,
    *,
    pdf_path: str | None = None,
    nbytes: int | None = None,
    source_url: str | None = None,
    note: str | None = None,
) -> None:
    """Upsert one work's fetch outcome into ``body_status``."""
    con.execute(
        """
        INSERT OR REPLACE INTO body_status
            (work_id, status, pdf_path, bytes, source_url, note, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            work_id,
            status,
            pdf_path,
            nbytes,
            source_url,
            note,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        ),
    )
    con.commit()
