"""Fetch one arXiv paper's LaTeXML HTML body. Shared by the bulk downloader and the API route."""

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
