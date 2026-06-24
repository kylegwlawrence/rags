#!/usr/bin/env python3
"""CLI: download arxiv HTML bodies for papers in ``data/arxiv/arxiv.db``.

Usage::

    python scripts/arxiv/arxiv_download.py [--db PATH] [--limit N] [--force]

Fetches ``https://arxiv.org/html/{id}`` for every paper with NULL or
``'retry'`` ``download_status``, newest-first by ``submitted_date``. Stores
the body in ``papers.html_content`` and sets ``download_status='downloaded'``
on success.

When arXiv has no HTML version (404), falls back to the PDF at
``https://arxiv.org/pdf/{id}``: the raw PDF is saved under ``--bodies-dir``
(default ``<db parent>/bodies/``, kept for debugging/re-extraction) and its
extracted plain text goes in ``papers.pdf_text`` with
``download_status='downloaded_pdf'``. If neither HTML nor PDF exists,
``download_status='no_body'``. Transient errors leave ``download_status``
unchanged so the next run retries.

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

import httpx  # noqa: F401  (kept so tests can monkeypatch the shared httpx module)

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from arxiv_oai import ARXIV_EMAIL  # noqa: E402

# Fetch logic lives in rag/arxiv_fetch.py so the API's on-demand download
# route shares it (same split as rag/sec_filing.py). Re-exported here for
# back-compat with callers/tests that import them from this module.
from rag.arxiv_fetch import (  # noqa: E402
    HTML_URL_TEMPLATE,
    body_filename,
    extract_pdf_text,
    fetch_paper_html,
    fetch_paper_pdf,
)

USER_AGENT = f"datasets/0.1 (mailto:{ARXIV_EMAIL})"
MIN_REQUEST_INTERVAL = 3.0
DEFAULT_DB = REPO_ROOT / "data" / "arxiv" / "arxiv.db"


def fetch_html(
    arxiv_id: str,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """Fetch the LaTeXML HTML body for ``arxiv_id`` with this script's User-Agent.

    Thin wrapper over ``rag.arxiv_fetch.fetch_paper_html``: returns the body on
    200, ``None`` on 404, or raises on persistent 429 / 5xx.
    """
    return fetch_paper_html(arxiv_id, user_agent=USER_AGENT, sleep=sleep)


def fetch_pdf(
    arxiv_id: str,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes | None:
    """Fetch the PDF bytes for ``arxiv_id`` with this script's User-Agent.

    Thin wrapper over ``rag.arxiv_fetch.fetch_paper_pdf``: returns the raw PDF
    on 200, ``None`` on 404 (or a non-PDF body), or raises on persistent 429 / 5xx.
    """
    return fetch_paper_pdf(arxiv_id, user_agent=USER_AGENT, sleep=sleep)


def ensure_pdf_text_column(conn: sqlite3.Connection) -> None:
    """Add ``papers.pdf_text`` if a pre-existing DB lacks it.

    The bulk downloader connects with a plain ``sqlite3.connect`` (it doesn't
    run ``arxiv_ingest.create_schema``), so on an older DB the ``pdf_text``
    column may be missing. Adding a nullable column is metadata-only — instant
    even on the multi-GB monolith — and idempotent.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(papers)")}
    if "pdf_text" not in cols:
        conn.execute("ALTER TABLE papers ADD COLUMN pdf_text TEXT")
        conn.commit()


def select_pending(
    conn: sqlite3.Connection,
    limit: int | None,
    *,
    force: bool,
    from_date: str | None = None,
    oai_date: str | None = None,
    categories: list[str] | None = None,
    category_prefixes: list[str] | None = None,
) -> list[str]:
    """Return paper IDs that need HTML download, newest-first by ``submitted_date``.

    With ``force=True``, returns every paper id (re-download everything,
    capped by ``limit``). Otherwise returns only papers with NULL
    ``download_status`` or ``download_status='retry'``.

    ``from_date`` filters to papers with ``submitted_date >= from_date`` (ISO
    date string, e.g. ``'2023-01-01'``). ``oai_date`` filters to papers whose
    ``oai_datestamp`` falls on exactly that date (ISO ``'YYYY-MM-DD'``) — this
    is the field the OAI-PMH ingest scopes on, so it selects precisely one
    harvest day's papers (the daily DAG passes yesterday). It differs from
    ``submitted_date`` by the arXiv announce lag, so the two are not
    interchangeable. ``categories`` filters to papers
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

    if oai_date:
        # substr() guards against a stored timestamp form, not just a date.
        conditions.append("substr(oai_datestamp, 1, 10) = ?")
        params.append(oai_date)

    cat_clauses: list[str] = []

    if categories:
        # categories column is space-separated; match each token with LIKE.
        for cat in categories:
            cat_clauses.append(
                "(categories = ? OR categories LIKE ? OR categories LIKE ? OR categories LIKE ?)"
            )
            params += [cat, f"% {cat}", f"{cat} %", f"% {cat} %"]

    if category_prefixes:
        # Match any token starting with "<prefix>." at field start or after a space.
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
    bodies_dir: Path,
    fetch_fn: Callable[[str], str | None] = fetch_html,
    pdf_fetch_fn: Callable[[str], bytes | None] = fetch_pdf,
    extract_fn: Callable[[bytes], str] = extract_pdf_text,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """Download each paper's body; update ``papers`` and save PDFs to ``bodies_dir``.

    Tries the HTML version first; on a 404 (``fetch_fn`` returns ``None``) falls
    back to the PDF, saving the raw file under ``bodies_dir`` and its extracted
    text into ``papers.pdf_text``.

    Args:
        conn: Open writer connection.
        paper_ids: Ordered list of arxiv ids to process (see ``select_pending``).
        bodies_dir: Directory the fallback PDFs are written to (created lazily).
        fetch_fn: Injectable HTML fetcher. Production passes ``fetch_html``;
            tests pass a fake. The fake should return ``str`` (body) on
            success, ``None`` for 404, or raise for transient error.
        pdf_fetch_fn: Injectable PDF fetcher (``fetch_pdf`` in production):
            ``bytes`` on success, ``None`` when arXiv has no PDF, or raise.
        extract_fn: PDF-bytes -> text (``extract_pdf_text`` in production).
        sleep: Inter-request delay. Tests pass a no-op.
        progress: Optional callback for periodic status lines.

    Returns:
        Stats dict with keys ``downloaded``, ``downloaded_pdf``, ``no_body``,
        ``error``.
    """
    stats = {"downloaded": 0, "downloaded_pdf": 0, "no_body": 0, "error": 0}
    total = len(paper_ids)
    for i, paper_id in enumerate(paper_ids, 1):
        try:
            body = fetch_fn(paper_id)
        except Exception as exc:
            # Transient: leave download_status unchanged so the next run retries.
            print(f"  error on {paper_id}: {exc}", file=sys.stderr)
            stats["error"] += 1
            sleep(MIN_REQUEST_INTERVAL)
            continue

        now_iso = datetime.now(timezone.utc).isoformat()

        if body is not None:
            conn.execute(
                "UPDATE papers SET html_content = ?, "
                "download_status = 'downloaded', downloaded_at = ? WHERE id = ?",
                (body, now_iso, paper_id),
            )
            stats["downloaded"] += 1
            conn.commit()
            sleep(MIN_REQUEST_INTERVAL)
            _maybe_progress(progress, i, total, stats)
            continue

        # No HTML — second arXiv hit for the PDF, so stay polite first.
        sleep(MIN_REQUEST_INTERVAL)
        try:
            pdf_bytes = pdf_fetch_fn(paper_id)
        except Exception as exc:
            print(f"  pdf error on {paper_id}: {exc}", file=sys.stderr)
            stats["error"] += 1
            sleep(MIN_REQUEST_INTERVAL)
            continue

        now_iso = datetime.now(timezone.utc).isoformat()
        if pdf_bytes is None:
            conn.execute(
                "UPDATE papers SET download_status = 'no_body', downloaded_at = ? "
                "WHERE id = ?",
                (now_iso, paper_id),
            )
            stats["no_body"] += 1
        else:
            bodies_dir.mkdir(parents=True, exist_ok=True)
            (bodies_dir / body_filename(paper_id)).write_bytes(pdf_bytes)
            try:
                text = extract_fn(pdf_bytes)
            except Exception as exc:
                # Keep the saved PDF for debugging; store empty text so the row
                # still resolves (RAG/content fall back to the abstract).
                print(f"  pdf parse error on {paper_id}: {exc}", file=sys.stderr)
                text = ""
            conn.execute(
                "UPDATE papers SET pdf_text = ?, "
                "download_status = 'downloaded_pdf', downloaded_at = ? WHERE id = ?",
                (text, now_iso, paper_id),
            )
            stats["downloaded_pdf"] += 1
        conn.commit()
        sleep(MIN_REQUEST_INTERVAL)
        _maybe_progress(progress, i, total, stats)
    return stats


def _maybe_progress(
    progress: Callable[[str], None] | None,
    i: int,
    total: int,
    stats: dict[str, int],
) -> None:
    """Emit a periodic status line every 10 papers, if a callback is set."""
    if progress is not None and i % 10 == 0:
        progress(
            f"  {i}/{total} processed: "
            f"{stats['downloaded']} downloaded / "
            f"{stats['downloaded_pdf']} pdf / {stats['no_body']} no_body / "
            f"{stats['error']} errors"
        )


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
        "--bodies-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Directory to save fallback PDFs in (default: a 'bodies' folder "
            "next to --db, e.g. /datasets/arxiv/bodies when --db points there)."
        ),
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
        "--oai-date",
        dest="oai_date",
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "Only process papers whose OAI-PMH datestamp is exactly this date "
            "— the field the ingest scopes on. Use to limit a run to one "
            "harvest day (the daily DAG passes yesterday)."
        ),
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

    bodies_dir = args.bodies_dir or args.db.parent / "bodies"

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    ensure_pdf_text_column(conn)

    paper_ids = select_pending(
        conn,
        args.limit,
        force=args.force,
        from_date=args.from_date,
        oai_date=args.oai_date,
        categories=args.categories,
        category_prefixes=args.category_prefixes,
    )
    _print_stderr(f"Processing {len(paper_ids)} papers...")
    _print_stderr(f"Fallback PDFs -> {bodies_dir}")

    stats = download_papers(
        conn, paper_ids, bodies_dir=bodies_dir, progress=_print_stderr
    )

    conn.close()
    _print_stderr(
        f"Done. downloaded={stats['downloaded']} "
        f"downloaded_pdf={stats['downloaded_pdf']} no_body={stats['no_body']} "
        f"error={stats['error']}"
    )
    _print_stderr("(Restart uvicorn so the cached connection picks up the new file.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
