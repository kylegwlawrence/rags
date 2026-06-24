"""Fetch one arXiv paper's body. Shared by the bulk downloader and the API route.

Primary path is the LaTeXML HTML version; for papers arXiv has no HTML for,
``fetch_paper_pdf`` grabs the PDF instead and ``extract_pdf_text`` pulls plain
text out of it for indexing.
"""

import io
import time
from collections.abc import Callable

import httpx

HTML_URL_TEMPLATE = "https://arxiv.org/html/{arxiv_id}"
PDF_URL_TEMPLATE = "https://arxiv.org/pdf/{arxiv_id}"
REQUEST_TIMEOUT = 60.0
MAX_ATTEMPTS = 3
BACKOFF_BASE = 5.0


def fetch_paper_html(
    arxiv_id: str,
    *,
    user_agent: str,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """Fetch LaTeXML HTML for arxiv_id. Returns None on 404; raises HTTPStatusError after retries."""
    url = HTML_URL_TEMPLATE.format(arxiv_id=arxiv_id)
    headers = {"User-Agent": user_agent}
    for attempt in range(MAX_ATTEMPTS):
        with httpx.Client(
            timeout=REQUEST_TIMEOUT, headers=headers, follow_redirects=True
        ) as client:
            resp = client.get(url)
        if resp.status_code == 404:
            return None
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
        resp.raise_for_status()
        return resp.text
    raise RuntimeError("unreachable: MAX_ATTEMPTS exhausted without raising")


def fetch_paper_pdf(
    arxiv_id: str,
    *,
    user_agent: str,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes | None:
    """Fetch the PDF bytes for arxiv_id. Returns None on 404; raises after retries.

    Mirrors ``fetch_paper_html``'s retry/back-off logic but returns raw bytes.
    A 200 whose body is not a real PDF (arXiv occasionally serves an HTML error
    page) is treated as "no PDF" and returns None, so we never store junk.
    """
    url = PDF_URL_TEMPLATE.format(arxiv_id=arxiv_id)
    headers = {"User-Agent": user_agent}
    for attempt in range(MAX_ATTEMPTS):
        with httpx.Client(
            timeout=REQUEST_TIMEOUT, headers=headers, follow_redirects=True
        ) as client:
            resp = client.get(url)
        if resp.status_code == 404:
            return None
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
        resp.raise_for_status()
        content = resp.content
        # Guard against a 200 that isn't actually a PDF.
        return content if content.startswith(b"%PDF-") else None
    raise RuntimeError("unreachable: MAX_ATTEMPTS exhausted without raising")


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract plain text from PDF bytes, pages joined by blank lines.

    pdfplumber is imported lazily so the API process doesn't load it unless a
    PDF actually needs parsing.
    """
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        parts = [page.extract_text() or "" for page in pdf.pages]
    return "\n\n".join(part for part in parts if part).strip()


def body_filename(arxiv_id: str) -> str:
    """Filesystem-safe PDF filename for an arxiv id (old-style ids contain '/')."""
    return arxiv_id.replace("/", "_") + ".pdf"
