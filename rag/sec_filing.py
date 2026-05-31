"""Download and extract the primary-document text from a SEC EDGAR filing.

SEC full-submission ``.txt`` files are SGML: a ``<SEC-HEADER>`` block followed
by one or more ``<DOCUMENT>`` blocks, each carrying a ``<TYPE>`` and a
``<TEXT>`` payload (HTML for modern filings, plain text for older ones). This
module turns that raw submission into clean prose for a single filing.

It is shared by the batch fetcher
(``scripts/sec_edgar/sec_edgar_fetch_bodies.py``) and the API's on-demand
"Download full filing" route (``api/routers/sec_edgar.py``) so both extract
bodies identically. Like ``rag.wikitext`` / ``rag.html_to_markdown``, it is source-specific
parsing that more than one entry point needs.
"""

import re
import time
from html import escape
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
# Real HTML filings open with a structural tag; legacy plain-text filings may
# still contain stray angle-bracket sequences (e.g. `<co>`) that BeautifulSoup
# would parse as bogus tags. Sniffing the payload for a genuine block-level tag
# is more reliable than asking the parsed tree whether it found "a tag".
_HTML_HINT_RE = re.compile(
    r"<(?:html|body|div|p|table|tr|td|span|br|h[1-6]|font)\b", re.IGNORECASE
)


def build_session(email: str) -> requests.Session:
    """Return a requests session carrying the SEC-required contact User-Agent.

    SEC rejects requests without an identifying User-Agent; ``email`` is the
    contact address SEC asks automated clients to advertise.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": f"sec-edgar-fetcher {email}"})
    return session


def _clean_soup(payload: str) -> BeautifulSoup:
    """Parse a filing payload and strip machine-only / non-visible markup.

    Modern 10-Ks are inline XBRL (iXBRL): the visible document is preceded by
    an ``<ix:header>`` block (and/or ``display:none`` containers) holding
    thousands of machine-readable tagging facts. Both the text extractor and
    the render-HTML builder decompose that hidden metadata first — leaving the
    *visible* ``<ix:nonFraction>`` / ``<ix:nonNumeric>`` figures embedded in the
    narrative intact, plus dropping ``<script>`` / ``<style>``. Plain-text
    legacy filings parse to a tagless tree and pass through unchanged.
    """
    soup = BeautifulSoup(payload, "html.parser")
    for tag in soup.find_all(["script", "style", "ix:header"]):
        tag.decompose()
    for tag in soup.find_all(style=_DISPLAY_NONE_RE):
        tag.decompose()
    return soup


def _html_to_text(payload: str) -> str:
    """Convert a filing's primary-document payload to plain text."""
    return _clean_soup(payload).get_text(separator=" ")


def _payload_to_text_and_html(payload: str) -> tuple[str, str]:
    """Render one cleaned payload into (plain text, display HTML), parsing once.

    The text half is whitespace-normalised for FTS / embedding (identical to
    what ``extract_primary_document`` has always produced). The HTML half keeps
    the visible structure — tables, headings, paragraphs — for the rendered
    Content view, with the iXBRL header, hidden containers, scripts, and styles
    already removed by ``_clean_soup``. A legacy plain-text filing (no tags) is
    escaped and wrapped in ``<pre>`` so its line breaks survive in the browser.
    """
    soup = _clean_soup(payload)
    # Text is always derived via get_text() — unchanged from the historical
    # extractor, so `body` stays byte-identical and embeddings never shift.
    text = normalize_whitespace(soup.get_text(separator=" "))
    if _HTML_HINT_RE.search(payload):
        html = str(soup)
    else:
        # Legacy plain-text filing: escape the raw payload and keep its line
        # breaks so it renders faithfully rather than collapsing in the browser.
        html = f"<pre>{escape(payload)}</pre>"
    return text, html


def _select_payload(text: str, form_type: str) -> str:
    """Return the raw primary-document payload from a full submission.

    Picks the ``<DOCUMENT>`` whose ``<TYPE>`` matches ``form_type`` (e.g.
    ``10-K``), falling back to the first document, then to everything after the
    header for pre-``<DOCUMENT>`` legacy filings.
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

    return payload or ""


def extract_primary(text: str, form_type: str) -> tuple[str, str]:
    """Return ``(cleaned_text, display_html)`` for a filing's primary document.

    The text is the whitespace-normalised, HTML-stripped body used for FTS and
    embedding; the HTML is the cleaned, render-ready markup used by the Content
    view. Both come from a single parse of the same selected payload, so they
    can never drift apart.
    """
    return _payload_to_text_and_html(_select_payload(text, form_type))


def extract_primary_document(text: str, form_type: str) -> str:
    """Return the cleaned text of a filing's primary document.

    Back-compat wrapper over ``extract_primary`` for callers that only need the
    text half (the historical behaviour).
    """
    return extract_primary(text, form_type)[0]


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


def download_filing_content(
    url: str, form_type: str, email: str
) -> Optional[tuple[str, str]]:
    """Fetch one filing and return ``(cleaned_text, display_html)``.

    Convenience wrapper for a single on-demand fetch: builds a session, fetches
    the submission, and extracts the primary document as both the embedding /
    FTS text and the render-ready HTML. Returns None when the submission
    couldn't be fetched (404 / retries exhausted). The batch fetcher reuses
    ``build_session`` / ``fetch_submission`` / ``extract_primary`` directly so it
    can share one session across many requests.
    """
    session = build_session(email)
    raw = fetch_submission(session, url)
    if raw is None:
        return None
    return extract_primary(raw, form_type)
