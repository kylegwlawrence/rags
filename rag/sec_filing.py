"""Download and extract the primary-document text from a SEC EDGAR filing.

SEC full-submission ``.txt`` files are SGML: a ``<SEC-HEADER>`` block followed
by one or more ``<DOCUMENT>`` blocks, each carrying a ``<TYPE>`` and a
``<TEXT>`` payload (HTML for modern filings, plain text for older ones). This
module turns that raw submission into clean prose for a single filing.

It is shared by the batch fetcher
(``scripts/sec_edgar/sec_edgar_fetch_bodies.py``) and the API's on-demand
"Download full filing" route (``api/routers/sec_edgar.py``) so both extract
bodies identically. Like ``rag.wikitext`` / ``rag.render``, it is source-specific
parsing that more than one entry point needs.
"""

import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from rag.cleaner import normalize_whitespace

# SEC allows up to 10 requests/sec; 0.15 s between requests gives headroom.
DEFAULT_DELAY = 0.15
MAX_RETRIES = 3
REQUEST_TIMEOUT = 60

_DOCUMENT_RE = re.compile(r"<DOCUMENT>(.*?)</DOCUMENT>", re.DOTALL | re.IGNORECASE)
_TYPE_RE = re.compile(r"<TYPE>\s*([^\n<]+)", re.IGNORECASE)
_TEXT_RE = re.compile(r"<TEXT>(.*?)</TEXT>", re.DOTALL | re.IGNORECASE)
_HEADER_END_RE = re.compile(r"</(?:SEC|IMS)-HEADER>", re.IGNORECASE)
_DISPLAY_NONE_RE = re.compile(r"display\s*:\s*none", re.IGNORECASE)


def build_session(email: str) -> requests.Session:
    """Return a requests session carrying the SEC-required contact User-Agent.

    SEC rejects requests without an identifying User-Agent; ``email`` is the
    contact address SEC asks automated clients to advertise.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": f"sec-edgar-fetcher {email}"})
    return session


def _html_to_text(payload: str) -> str:
    """Convert a filing's primary-document payload to plain text.

    Modern 10-Ks are inline XBRL (iXBRL): the visible document is preceded by
    an ``<ix:header>`` block (and/or ``display:none`` containers) holding
    thousands of machine-readable tagging facts. ``get_text()`` would surface
    all of that as leading noise. We decompose the hidden metadata first, then
    extract text — leaving the *visible* ``<ix:nonFraction>`` /
    ``<ix:nonNumeric>`` figures embedded in the narrative intact. Plain-text
    legacy filings pass through unchanged.
    """
    soup = BeautifulSoup(payload, "html.parser")
    for tag in soup.find_all(["script", "style", "ix:header"]):
        tag.decompose()
    for tag in soup.find_all(style=_DISPLAY_NONE_RE):
        tag.decompose()
    return soup.get_text(separator=" ")


def extract_primary_document(text: str, form_type: str) -> str:
    """Return the cleaned text of a filing's primary document.

    Picks the ``<DOCUMENT>`` whose ``<TYPE>`` matches ``form_type`` (e.g.
    ``10-K``), falling back to the first document, then to everything after the
    header for pre-``<DOCUMENT>`` legacy filings. The chosen payload is
    HTML-stripped and whitespace-normalised.
    """
    blocks = _DOCUMENT_RE.findall(text)
    payload: Optional[str] = None

    if blocks:
        target = form_type.strip().upper()
        for block in blocks:
            type_match = _TYPE_RE.search(block)
            doc_type = type_match.group(1).strip().upper() if type_match else ""
            if doc_type == target:
                text_match = _TEXT_RE.search(block)
                payload = text_match.group(1) if text_match else block
                break
        if payload is None:
            # No type match — use the first document's TEXT payload.
            first = blocks[0]
            text_match = _TEXT_RE.search(first)
            payload = text_match.group(1) if text_match else first
    else:
        # Legacy filing with no <DOCUMENT> tags: take everything after the header.
        header_end = _HEADER_END_RE.search(text)
        payload = text[header_end.end():] if header_end else text

    return normalize_whitespace(_html_to_text(payload or ""))


def fetch_submission(
    session: requests.Session, url: str, *, max_retries: int = MAX_RETRIES
) -> Optional[str]:
    """Fetch one filing's raw submission text, honouring SEC rate limits.

    Returns None on a 404 (treated as a permanent miss) or after exhausting
    retries on transient errors. Sleeps on a 429 (honouring ``Retry-After``)
    and on network errors before retrying.
    """
    for attempt in range(max_retries):
        try:
            r = session.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 60))
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.text
        except requests.RequestException:
            if attempt < max_retries - 1:
                time.sleep(5)
    return None


def download_filing_body(url: str, form_type: str, email: str) -> Optional[str]:
    """Fetch and extract one filing's primary document as clean text.

    Convenience wrapper for a single on-demand fetch: builds a session, fetches
    the submission, and extracts the primary document. Returns the cleaned body
    text, or None when the submission couldn't be fetched (404 / retries
    exhausted). The batch fetcher reuses ``build_session`` / ``fetch_submission``
    / ``extract_primary_document`` directly so it can share one session across
    many requests.
    """
    session = build_session(email)
    raw = fetch_submission(session, url)
    if raw is None:
        return None
    return extract_primary_document(raw, form_type)
