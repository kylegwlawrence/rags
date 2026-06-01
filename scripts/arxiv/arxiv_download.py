#!/usr/bin/env python3
"""CLI: download arxiv HTML bodies for papers in ``data/arxiv/arxiv.db``.

Usage::

    python scripts/arxiv_download.py [--db PATH] [--limit N] [--force]

Fetches ``https://arxiv.org/html/{id}`` for every paper with NULL or
``'retry'`` ``download_status``, newest-first by ``submitted_date``. Stores
the body in ``papers.html_content`` and sets ``download_status='downloaded'``
on success, ``'no_html'`` on 404 (arxiv has no HTML version for that paper),
or leaves ``download_status`` unchanged on transient errors so the next run
retries.

Rate-limited to one request per 3 s per arxiv's polite bulk-fetcher policy.
User-Agent includes a ``mailto:`` overridable via ``DATASETS_EMAIL``.

Restart uvicorn after this runs so the cached connection in ``api/db.py``
reopens against the new file.
"""

import argparse
import sqlite3
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import httpx

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from arxiv_oai import ARXIV_EMAIL  # noqa: E402

HTML_URL_TEMPLATE = "https://arxiv.org/html/{arxiv_id}"
USER_AGENT = f"datasets/0.1 (mailto:{ARXIV_EMAIL})"
REQUEST_TIMEOUT = 60.0
MAX_ATTEMPTS = 3
BACKOFF_BASE = 5.0
MIN_REQUEST_INTERVAL = 3.0
DEFAULT_DB = REPO_ROOT / "data" / "arxiv" / "arxiv.db"


def fetch_html(
    arxiv_id: str,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """Fetch the LaTeXML HTML body for ``arxiv_id``.

    Returns the body text on 200, ``None`` on 404 (no HTML version available),
    or raises ``httpx.HTTPStatusError`` after ``MAX_ATTEMPTS`` failed retries
    on persistent 429 / 5xx. Honors ``Retry-After`` on 429 / 5xx between
    attempts.
    """
    url = HTML_URL_TEMPLATE.format(arxiv_id=arxiv_id)
    headers = {"User-Agent": USER_AGENT}
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


def select_pending(
    conn: sqlite3.Connection,
    limit: int | None,
    *,
    force: bool,
    from_date: str | None = None,
    categories: list[str] | None = None,
    category_prefixes: list[str] | None = None,
) -> list[str]:
    """Return paper IDs that need HTML download, newest-first by ``submitted_date``.

    With ``force=True``, returns every paper id (re-download everything,
    capped by ``limit``). Otherwise returns only papers with NULL
    ``download_status`` or ``download_status='retry'``.

    ``from_date`` filters to papers with ``submitted_date >= from_date`` (ISO
    date string, e.g. ``'2023-01-01'``). ``categories`` filters to papers
    whose ``categories`` field contains at least one of the given tokens
    (e.g. ``['cs.LG', 'stat.ML']``). ``category_prefixes`` matches any
    category token that starts with the given prefix followed by a dot
    (e.g. ``['physics']`` matches ``physics.flu-dyn``, ``physics.optics``,
    etc.). Both filters are combined with OR when both are provided.
    """
    conditions: list[str] = []
    params: list = []

    if not force:
        conditions.append(
            "(download_status IS NULL OR download_status = 'retry')"
        )

    if from_date:
        conditions.append("submitted_date >= ?")
        params.append(from_date)

    cat_clauses: list[str] = []

    if categories:
        # categories column is space-separated; match each token with LIKE
        for cat in categories:
            cat_clauses.append(
                "(categories = ? OR categories LIKE ? OR categories LIKE ? OR categories LIKE ?)"
            )
            params += [cat, f"% {cat}", f"{cat} %", f"% {cat} %"]

    if category_prefixes:
        # Match any token that starts with "<prefix>." at the start of the
        # field or after a space boundary.
        for prefix in category_prefixes:
            cat_clauses.append(
                "(categories LIKE ? OR categories LIKE ?)"
            )
            params += [f"{prefix}.%", f"% {prefix}.%"]

    if cat_clauses:
        conditions.append(f"({' OR '.join(cat_clauses)})")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"SELECT id FROM papers {where} ORDER BY submitted_date DESC"

    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    return [r[0] for r in conn.execute(sql, params)]


def download_papers(
    conn: sqlite3.Connection,
    paper_ids: list[str],
    *,
    fetch_fn: Callable[[str], str | None] = fetch_html,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """Download HTML for each id; update ``papers.html_content`` + ``download_status``.

    Args:
        conn: Open writer connection.
        paper_ids: Ordered list of arxiv ids to process (see ``select_pending``).
        fetch_fn: Injectable HTTP fetcher. Production passes ``fetch_html``;
            tests pass a fake. The fake should return ``str`` (body) on
            success, ``None`` for 404, or raise for transient error.
        sleep: Inter-request delay. Tests pass a no-op.
        progress: Optional callback for periodic status lines.

    Returns:
        Stats dict with keys ``downloaded``, ``no_html``, ``error``.
    """
    stats = {"downloaded": 0, "no_html": 0, "error": 0}
    total = len(paper_ids)
    for i, paper_id in enumerate(paper_ids, 1):
        try:
            body = fetch_fn(paper_id)
        except Exception as exc:
            # Transient — leave download_status unchanged so the next run
            # retries. Print at error level rather than failing the whole run.
            print(f"  error on {paper_id}: {exc}", file=sys.stderr)
            stats["error"] += 1
            sleep(MIN_REQUEST_INTERVAL)
            continue
        now_iso = datetime.now(timezone.utc).isoformat()
        if body is None:
            conn.execute(
                "UPDATE papers SET download_status = 'no_html', downloaded_at = ? WHERE id = ?",
                (now_iso, paper_id),
            )
            stats["no_html"] += 1
        else:
            conn.execute(
                "UPDATE papers SET html_content = ?, "
                "download_status = 'downloaded', downloaded_at = ? WHERE id = ?",
                (body, now_iso, paper_id),
            )
            stats["downloaded"] += 1
        conn.commit()
        sleep(MIN_REQUEST_INTERVAL)
        if progress is not None and i % 10 == 0:
            progress(
                f"  {i}/{total} processed: "
                f"{stats['downloaded']} downloaded / {stats['no_html']} no_html / "
                f"{stats['error']} errors"
            )
    return stats


def _print_stderr(line: str) -> None:
    print(line, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Path to arxiv.db (default: {DEFAULT_DB.relative_to(REPO_ROOT)}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N papers (testing).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download every paper, ignoring existing download_status.",
    )
    parser.add_argument(
        "--from-date",
        dest="from_date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Only process papers submitted on or after this date.",
    )
    parser.add_argument(
        "--category",
        dest="categories",
        action="append",
        default=None,
        metavar="CAT",
        help=(
            "Only process papers in this category (e.g. cs.LG). "
            "Repeat to allow multiple categories."
        ),
    )
    parser.add_argument(
        "--category-prefix",
        dest="category_prefixes",
        action="append",
        default=None,
        metavar="PREFIX",
        help=(
            "Only process papers whose categories include any subcategory "
            "starting with PREFIX (e.g. physics matches physics.flu-dyn, "
            "physics.optics, etc.). Repeat to allow multiple prefixes."
        ),
    )
    args = parser.parse_args(argv)

    if not ARXIV_EMAIL:
        _print_stderr(
            "DATASETS_EMAIL env var is not set; arXiv requires a contact "
            "mailto: in the User-Agent. Set it and re-run."
        )
        return 1

    if not args.db.is_file():
        _print_stderr(f"missing DB: {args.db}")
        return 1

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    paper_ids = select_pending(
        conn,
        args.limit,
        force=args.force,
        from_date=args.from_date,
        categories=args.categories,
        category_prefixes=args.category_prefixes,
    )
    _print_stderr(f"Processing {len(paper_ids)} papers...")

    stats = download_papers(conn, paper_ids, progress=_print_stderr)

    conn.close()
    _print_stderr(
        f"Done. downloaded={stats['downloaded']} no_html={stats['no_html']} "
        f"error={stats['error']}"
    )
    _print_stderr("(Restart uvicorn so the cached connection picks up the new file.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
