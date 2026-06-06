"""SEC EDGAR filing fetcher + primary-document extractor. Shared by the batch fetcher and the API route."""

import re
import time
from html import escape

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
    """Return a requests session with the SEC-required contact User-Agent header."""
    session = requests.Session()
    session.headers.update({"User-Agent": f"sec-edgar-fetcher {email}"})
    return session


def _clean_soup(payload: str) -> BeautifulSoup:
    """Parse and strip iXBRL headers, display:none containers, and script/style tags."""
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
    """Parse once → (whitespace-normalised text for FTS/embed, cleaned HTML for Content view)."""
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
    """Extract the primary-document payload from an SGML submission (type-matched or first)."""
    blocks = _DOCUMENT_RE.findall(text)
    payload: str | None = None

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
    """Return (cleaned_text, display_html) from a single parse of the primary document."""
    return _payload_to_text_and_html(_select_payload(text, form_type))


def extract_primary_document(text: str, form_type: str) -> str:
    """Back-compat wrapper: returns only the text half of extract_primary."""
    return extract_primary(text, form_type)[0]


def fetch_submission(
    session: requests.Session, url: str, *, max_retries: int = MAX_RETRIES
) -> str | None:
    """Fetch a filing's raw submission text. Returns None on 404 or exhausted retries."""
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
) -> tuple[str, str] | None:
    """One-shot fetch → (cleaned_text, display_html). Returns None on 404 or retries exhausted."""
    session = build_session(email)
    raw = fetch_submission(session, url)
    if raw is None:
        return None
    return extract_primary(raw, form_type)
