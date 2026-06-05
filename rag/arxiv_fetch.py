"""Fetch one arXiv paper's LaTeXML HTML body from ``arxiv.org/html/{id}``.

Shared by the bulk downloader (``scripts/arxiv/arxiv_download.py``) and the
API's on-demand download route (``POST /arxiv/papers/{id}/download``), so a
paper fetched either way goes through identical request logic — the same split
as ``rag/sec_filing.py`` for SEC filings.
"""

import time
from collections.abc import Callable

import httpx

HTML_URL_TEMPLATE = "https://arxiv.org/html/{arxiv_id}"
REQUEST_TIMEOUT = 60.0
MAX_ATTEMPTS = 3
BACKOFF_BASE = 5.0


def fetch_paper_html(
    arxiv_id: str,
    *,
    user_agent: str,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """Fetch the LaTeXML HTML body for ``arxiv_id``.

    Returns the body text on 200, ``None`` on 404 (arXiv has no HTML version
    for that paper), or raises ``httpx.HTTPStatusError`` after ``MAX_ATTEMPTS``
    failed retries on persistent 429 / 5xx. Honors ``Retry-After`` on 429 / 5xx
    between attempts.

    ``user_agent`` should carry a contact ``mailto:`` per arXiv's polite-access
    policy. ``sleep`` is injectable so tests can run without real backoff waits.
    """
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
