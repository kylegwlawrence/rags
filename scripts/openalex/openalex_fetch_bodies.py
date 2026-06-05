#!/usr/bin/env python3
"""Fetch open-access PDFs for OpenAlex works, into data/openalex/bodies/.

Reads the open-access pointers (``pdf_url`` / ``oa_url``) that
``openalex_download.py`` stored on each work and downloads the actual PDF for
the open-access subset. OpenAlex never serves the body itself — only those
links to a free copy hosted elsewhere — so this is a separate, polite fetch.

Mirrors ``loc_fetch_bodies.py``: it only *downloads files*, it builds no index.
The PDFs are meant to flow into the self-contained ``pdfs`` scripts, kept under
``data/openalex/``:

    python scripts/pdfs/pdfs_ingest.py    --db data/openalex/openalex_pdfs.db --incoming data/openalex/bodies
    python scripts/pdfs/pdfs_index_fts.py --db data/openalex/openalex_pdfs.db
    python scripts/pdfs/pdfs_index_rag.py ...   # page-aware RAG, optional

The fetch logic lives in ``rag/openalex_fetch.py`` so the API's on-demand
download route (``POST /openalex/works/{id}/download``) goes through identical
request logic — the same split as ``rag/arxiv_fetch.py`` and ``rag/sec_filing.py``.

Resumable: per-work outcomes are recorded in a ``body_status`` table, so re-runs
skip works already fetched or already known to have no accessible PDF, without
re-hitting the network. Transient ``error`` rows are retried on the next run
(pass --skip-errors to leave them alone).

Usage:
    python scripts/openalex/openalex_fetch_bodies.py --limit 50
"""

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

# Make `rag` importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv

from rag.openalex_fetch import (  # noqa: E402  (after sys.path tweak)
    TERMINAL_STATUSES,
    NoPdfAvailable,
    ensure_body_status_table,
    fetch_work_pdf,
    record_body_status,
)

load_dotenv()

DEFAULT_DB = "./data/openalex/openalex.db"
DEFAULT_OUT_DIR = "./data/openalex/bodies"
REQUEST_DELAY = 3  # seconds between works; be polite to publisher hosts
_EMAIL = os.environ.get("DATASETS_EMAIL")
USER_AGENT = f"datasets-bot/1.0 (mailto:{_EMAIL})"


def short_id(full_id: str) -> str:
    """Derive the bare OpenAlex id (the filename stem) from a work's full URL.

    e.g. https://openalex.org/W2741809807 -> "W2741809807"
    """
    return full_id.rsplit("/", 1)[-1] if full_id else full_id


def already_done(con: sqlite3.Connection, skip_errors: bool) -> set:
    """Return work_ids a resumed run should skip.

    Always skips terminal outcomes (fetched / no_pdf). Also skips prior errors
    when skip_errors is set; otherwise errors are retried.
    """
    statuses = list(TERMINAL_STATUSES)
    if skip_errors:
        statuses.append("error")
    placeholders = ",".join("?" * len(statuses))
    rows = con.execute(
        f"SELECT work_id FROM body_status WHERE status IN ({placeholders})",
        statuses,
    ).fetchall()
    return {r[0] for r in rows}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", default=DEFAULT_DB, help="OpenAlex metadata DB")
    parser.add_argument("--limit", type=int, default=50,
                        help="number of PDFs to download this run")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="where to save PDFs")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY,
                        help="seconds between works")
    parser.add_argument("--skip-errors", action="store_true",
                        help="leave prior 'error' rows alone instead of retrying them")
    args = parser.parse_args()

    if not _EMAIL:
        parser.error("DATASETS_EMAIL env var is required for the User-Agent contact address.")
    if not os.path.exists(args.db):
        parser.error(f"metadata DB not found: {args.db} (run openalex_download.py first)")

    os.makedirs(args.out_dir, exist_ok=True)

    con = sqlite3.connect(args.db, timeout=30)
    con.execute("PRAGMA busy_timeout = 30000")
    con.row_factory = sqlite3.Row
    ensure_body_status_table(con)

    done = already_done(con, args.skip_errors)
    # Highest-cited open-access works first — the most worth having a full text for.
    rows = con.execute(
        "SELECT id, title, pdf_url, oa_url FROM works "
        "WHERE is_oa = 1 AND (pdf_url IS NOT NULL OR oa_url IS NOT NULL) "
        "ORDER BY cited_by_count DESC"
    ).fetchall()

    fetched = scanned = skipped = errors = resumed = 0
    print(f"{len(rows)} open-access works in {args.db}; {len(done)} already processed "
          f"(skipping). Looking for {args.limit} more PDFs.")

    for row in rows:
        if fetched >= args.limit:
            break
        wid = short_id(row["id"])
        if wid in done:
            resumed += 1
            continue

        dest = os.path.join(args.out_dir, f"{wid}.pdf")
        scanned += 1
        print(f"[{scanned}] {(row['title'] or '')[:70]}")

        try:
            size, src = fetch_work_pdf(
                [row["pdf_url"], row["oa_url"]], dest, user_agent=USER_AGENT
            )
        except NoPdfAvailable as exc:
            skipped += 1
            print(f"  - no accessible PDF — skipped ({exc})")
            record_body_status(con, wid, "no_pdf", note=str(exc))
            time.sleep(args.delay)
            continue
        except Exception as exc:  # network/timeout/persistent 5xx — retryable
            errors += 1
            print(f"  ! download failed: {exc}")
            record_body_status(con, wid, "error", note=str(exc))
            time.sleep(args.delay)
            continue

        fetched += 1
        print(f"  + {dest}  ({size / 1024:.0f} KB)  <- {src}")
        record_body_status(con, wid, "fetched", pdf_path=f"{wid}.pdf",
                           nbytes=size, source_url=src)
        time.sleep(args.delay)

    con.close()
    print(f"\nDone. fetched={fetched}  no_pdf={skipped}  errors={errors}  "
          f"(scanned this run={scanned}, resume-skipped={resumed})")
    print(f"PDFs in: {args.out_dir}")


if __name__ == "__main__":
    main()
