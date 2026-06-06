"""Fetch one OpenAlex work's OA PDF to disk. Shared by the bulk downloader and the API route.
Walks pdf_url/oa_url candidates, streams with %PDF- magic check, renames atomically.
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
    """Download the first candidate URL that is a real PDF → (bytes_written, source_url).
    Raises NoPdfAvailable when all URLs are reachable but none is a PDF (terminal).
    Raises httpx.HTTPError on persistent 429/5xx (transient — caller retries).
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
    """Stream url → tmp if the body starts with %PDF-; return bytes written or None if not a PDF."""
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
