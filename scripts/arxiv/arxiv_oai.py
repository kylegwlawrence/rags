"""OAI-PMH client for harvesting arXiv metadata.

Walks ``ListRecords`` pages from arxiv's OAI-PMH endpoint with the ``arXiv``
metadata prefix, caches every raw response to disk so re-parses during schema
iteration don't re-hit the network, and yields one parsed dict per
non-deleted record. The dict shape matches the columns this repo's ingest
script writes to ``arxiv.db`` (plus an ``oai_datestamp`` lifted from the
record header so the embed pipeline can detect "this paper changed").

arXiv asks bulk harvesters for >= 3 s between requests and a contact
``mailto:`` in the User-Agent. Both are honored; the email is overridable
via the ``DATASETS_EMAIL`` environment variable (the project-wide contact
address, shared with ``scripts/arxiv_download.py`` and the other sources).

Ported from ``local_wikipedia/arxiv/oai.py``. Two intentional deviations:

* **Structured author fields preserved.** Per WORK.md section 2.1, the
  parser no longer collapses ``<keyname>`` / ``<forenames>`` / ``<suffix>`` /
  ``<affiliation>`` into a single string. ``_parse_authors`` returns
  ``list[dict]`` with the structured fields plus a convenience
  ``display_name``.
* **Email is env-var driven.** Reads the project-wide ``DATASETS_EMAIL``,
  the shared contact address used by every source's downloader.

The HTTP layer (retry with ``Retry-After``, 5xx exponential backoff)
is kept as a local helper rather than routed through ``rag.retry.with_retry``
because the retry semantics depend on response headers (``Retry-After``)
that ``with_retry`` doesn't model.

See https://www.openarchives.org/OAI/openarchivesprotocol.html for the
protocol and https://info.arxiv.org/help/oa/index.html for arXiv's metadata
format definitions.
"""

import hashlib
import os
import pathlib
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

OAI_ENDPOINT = "https://oaipmh.arxiv.org/oai"
# Required, no fallback. May be None here (e.g. for --from-cache runs that make
# no network calls); the requirement is enforced in _fetch_with_retry, the only
# place an actual request — and thus a User-Agent — is built.
ARXIV_EMAIL = os.environ.get("DATASETS_EMAIL")
MIN_REQUEST_INTERVAL = 3.0
MAX_ATTEMPTS = 3
BACKOFF_BASE = 5.0
REQUEST_TIMEOUT = 60.0

NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "arXiv": "http://arxiv.org/OAI/arXiv/",
}


class OAIError(RuntimeError):
    """Raised on a non-recoverable OAI-PMH error response."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(
            f"OAI-PMH error {code}: {message}" if message else f"OAI-PMH error {code}"
        )
        self.code = code
        self.message = message


def cache_filename(params: dict[str, str]) -> str:
    """Stable cache-file name for a given OAI request.

    Hashes params in sorted order so the key is independent of dict
    insertion order or URL-encoding choices.
    """
    blob = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest() + ".xml"


def harvest_records(
    from_date: str,
    until_date: str | None = None,
    set_spec: str | None = None,
    cache_dir: pathlib.Path | None = None,
    endpoint: str = OAI_ENDPOINT,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[dict[str, Any]]:
    """Yield parsed paper dicts by walking ``ListRecords`` pages.

    Args:
        from_date: ISO ``YYYY-MM-DD``; records modified on/after this date.
        until_date: Optional upper bound, inclusive.
        set_spec: OAI-PMH set name for server-side subject filtering, e.g.
            ``"cs"`` for all CS papers or ``"cs:LG"`` for just cs.LG.
            See https://info.arxiv.org/help/oa/index.html for the full list.
        cache_dir: If provided, raw XML responses are cached here keyed by
            request params; replayed transparently on the next run.
        endpoint: OAI-PMH base URL (overridable for tests).
        sleep: Injectable sleep for rate-limit / retry backoff (tests pass
            a no-op).
    """
    params: dict[str, str] = {
        "verb": "ListRecords",
        "metadataPrefix": "arXiv",
        "from": from_date,
    }
    if until_date:
        params["until"] = until_date
    if set_spec:
        params["set"] = set_spec

    while True:
        xml_text = fetch_page(endpoint, params, cache_dir=cache_dir, sleep=sleep)
        root = ET.fromstring(xml_text)

        error_el = root.find("oai:error", NS)
        if error_el is not None:
            code = error_el.get("code", "")
            if code == "noRecordsMatch":
                return
            raise OAIError(code, (error_el.text or "").strip())

        records_node = root.find("oai:ListRecords", NS)
        if records_node is None:
            return

        for record_el in records_node.findall("oai:record", NS):
            parsed = parse_record(record_el)
            if parsed is not None:
                yield parsed

        token_el = records_node.find("oai:resumptionToken", NS)
        token = ""
        if token_el is not None and token_el.text:
            token = token_el.text.strip()
        if not token:
            return
        # Per OAI-PMH spec: when continuing, only verb + resumptionToken are sent.
        params = {"verb": "ListRecords", "resumptionToken": token}


def fetch_page(
    endpoint: str,
    params: dict[str, str],
    cache_dir: pathlib.Path | None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Fetch one OAI-PMH page; replay from cache if present, else GET + cache."""
    cache_path: pathlib.Path | None = None
    if cache_dir is not None:
        cache_path = cache_dir / cache_filename(params)
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8")

    text = _fetch_with_retry(endpoint, params, sleep=sleep)

    if cache_path is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")

    sleep(MIN_REQUEST_INTERVAL)
    return text


def _fetch_with_retry(
    endpoint: str,
    params: dict[str, str],
    sleep: Callable[[float], None],
) -> str:
    """GET with retry on 429 / 5xx; honors ``Retry-After`` header.

    Kept as a local helper rather than ``rag.retry.with_retry`` because
    the retry strategy depends on response headers (``Retry-After``)
    that ``with_retry`` doesn't model.
    """
    if not ARXIV_EMAIL:
        raise RuntimeError(
            "DATASETS_EMAIL env var is not set; arXiv requires a contact "
            "mailto: in the User-Agent. Set it and re-run."
        )
    headers = {"User-Agent": f"datasets/0.1 (mailto:{ARXIV_EMAIL})"}
    for attempt in range(MAX_ATTEMPTS):
        with httpx.Client(timeout=REQUEST_TIMEOUT, headers=headers) as client:
            resp = client.get(endpoint, params=params)
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


def iter_cached_records(cache_dir: pathlib.Path) -> Iterator[dict[str, Any]]:
    """Yield records parsed from every cached XML file in ``cache_dir``.

    Used by the ingest CLI's ``--from-cache`` mode to re-process a previous
    harvest without re-hitting the network. Files are iterated in sorted
    name order so behavior is deterministic across runs.
    """
    for xml_path in sorted(cache_dir.glob("*.xml")):
        root = ET.fromstring(xml_path.read_text(encoding="utf-8"))
        records_node = root.find("oai:ListRecords", NS)
        if records_node is None:
            continue
        for record_el in records_node.findall("oai:record", NS):
            parsed = parse_record(record_el)
            if parsed is not None:
                yield parsed


def parse_record(record_el: ET.Element) -> dict[str, Any] | None:
    """Parse one ``<record>``. Returns ``None`` for unusable records.

    A record is treated as unusable (None returned) when any of these hold:

    * The header is missing or marked ``status="deleted"``.
    * The header has no ``<datestamp>`` (or it's empty) — without it, the
      ingest step has no way to detect "this paper changed" for incremental
      re-embed.
    * The metadata wrapper or the ``<arXiv>`` element is missing.
    * The ``<arXiv:id>`` element is missing or empty — the arxiv id is the
      primary key on ``papers``, so a missing id would either crash the
      insert or collide with other malformed records.
    * ``<title>`` or ``<abstract>`` is missing or empty — arxiv requires both
      on every submission, so an empty value indicates a malformed feed
      entry rather than a real paper. Dropping at parse time avoids polluting
      the API responses with blank rows.
    """
    header = record_el.find("oai:header", NS)
    if header is None:
        return None
    if header.get("status") == "deleted":
        return None

    datestamp_el = header.find("oai:datestamp", NS)
    datestamp = ""
    if datestamp_el is not None and datestamp_el.text:
        datestamp = datestamp_el.text.strip()
    if not datestamp:
        return None

    metadata = record_el.find("oai:metadata", NS)
    if metadata is None:
        return None
    arxiv_el = metadata.find("arXiv:arXiv", NS)
    if arxiv_el is None:
        return None

    def text_of(name: str) -> str:
        node = arxiv_el.find(f"arXiv:{name}", NS)
        if node is None or node.text is None:
            return ""
        return node.text.strip()

    paper_id = text_of("id")
    if not paper_id:
        return None

    title = _collapse_ws(text_of("title"))
    if not title:
        return None
    abstract = _collapse_ws(text_of("abstract"))
    if not abstract:
        return None

    authors = _parse_authors(arxiv_el)
    categories = text_of("categories")
    primary_category = categories.split()[0] if categories else ""

    return {
        "id": paper_id,
        "oai_datestamp": datestamp,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "categories": categories,
        "primary_category": primary_category,
        "submitted_date": text_of("created"),
        "updated_date": text_of("updated") or None,
        "doi": text_of("doi") or None,
        "journal_ref": text_of("journal-ref") or None,
        "comments": text_of("comments") or None,
    }


def _parse_authors(arxiv_el: ET.Element) -> list[dict[str, str | None]]:
    """Parse ``<author>`` children, preserving structured name fields.

    Returns a list of dicts with keys:

    * ``keyname`` (str): surname; required, never empty.
    * ``forenames`` (str): given names; empty string when absent.
    * ``affiliation`` (str | None): captured when the OAI feed provides it;
      None otherwise.
    * ``display_name`` (str): convenience composite of
      ``forenames keyname suffix`` joined on single spaces, with empty
      tokens dropped. Used as the ``authors.display_name`` column at ingest
      time and as the back-compat string surface in the API response.

    Diverges from ``local_wikipedia/arxiv/oai.py:227-240`` (which collapsed
    ``f"{forenames} {keyname}"``) so the structured fields survive ingest —
    fixing the WORK.md section 2.1 carry-over.

    An ``<author>`` element with no usable fields (no name parts AND no
    affiliation) is treated as junk and skipped. An author with affiliation
    but no name parts is kept (its ``display_name`` will be empty but the
    affiliation is preserved); this case is vanishingly rare in real arxiv
    data but the explicit policy avoids silent data loss.
    """
    authors_el = arxiv_el.find("arXiv:authors", NS)
    if authors_el is None:
        return []
    out: list[dict[str, str | None]] = []
    for author in authors_el.findall("arXiv:author", NS):
        keyname = _child_text(author, "keyname")
        forenames = _child_text(author, "forenames")
        suffix = _child_text(author, "suffix")
        affiliation = _child_text(author, "affiliation") or None
        display_name = " ".join(part for part in (forenames, keyname, suffix) if part)
        if not display_name and not affiliation:
            continue
        out.append(
            {
                "keyname": keyname,
                "forenames": forenames,
                "affiliation": affiliation,
                "display_name": display_name,
            }
        )
    return out


def _child_text(parent: ET.Element, name: str) -> str:
    """Return the stripped text of a child element in the arXiv namespace, or empty string."""
    node = parent.find(f"arXiv:{name}", NS)
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _collapse_ws(text: str) -> str:
    """Collapse internal whitespace runs (incl. line wraps) to a single space."""
    return " ".join(text.split())
