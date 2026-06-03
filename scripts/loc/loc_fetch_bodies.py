#!/usr/bin/env python3
"""Fetch whole-item PDFs for LOC manuscript items, into data/loc/bodies/.

Reads item URLs from the LOC metadata DB (the ``loc_download.py`` output),
resolves each item's JSON (``{url}?fo=json``), and downloads its whole-item
PDF derivative for items that actually expose one.  Items whose only online
formats are video/audio/image are skipped — they carry no PDF body.

Mirrors ``sec_edgar_fetch_bodies.py``: it only *downloads files*, it does not
build any index.  The downloaded PDFs are meant to flow into the existing
``pdfs`` indexer scripts, kept self-contained under ``data/loc/``:

    python scripts/pdfs/pdfs_ingest.py    --db data/loc/loc_pdfs.db --incoming data/loc/bodies
    python scripts/pdfs/pdfs_index_fts.py --db data/loc/loc_pdfs.db
    python scripts/pdfs/pdfs_index_rag.py ...   # (page-aware RAG, optional)

Resumable: per-item outcomes are recorded in a ``body_status`` table in the
metadata DB, so re-runs skip items already fetched or already known to have no
PDF, without re-hitting the LOC API.  Transient ``error`` rows are retried on
the next run (pass --skip-errors to leave them alone).

Note: a LOC item's ``fulltext`` field is usually null even when its PDF has an
embedded text layer, so we don't rely on it — we download the PDF and let
pdfplumber (in pdfs_ingest) decide what text exists.

Usage:
    python scripts/loc/loc_fetch_bodies.py --limit 50
"""

import argparse
import os
import re
import sqlite3
import time
import urllib.parse
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_DB = "./data/loc/loc_manuscript_mixed_material.db"
DEFAULT_OUT_DIR = "./data/loc/bodies"
REQUEST_DELAY = 3  # seconds between items; be polite to loc.gov
DOWNLOAD_TIMEOUT = 180
_EMAIL = os.environ.get("DATASETS_EMAIL")
USER_AGENT = f"datasets-bot/1.0 (mailto:{_EMAIL})"

# Terminal outcomes that a resumed run skips by default.
TERMINAL_STATUSES = ("fetched", "no_pdf")


def item_stem(item_url: str) -> str:
    """Derive a filesystem-safe stem from an item URL.

    e.g. https://www.loc.gov/item/s1229l09005/ -> "s1229l09005"
    """
    path = urllib.parse.urlparse(item_url).path.strip("/")
    last = path.split("/")[-1] if path else item_url
    return re.sub(r"[^A-Za-z0-9._-]+", "_", last).strip("_") or "item"


def create_status_table(con: sqlite3.Connection) -> None:
    """Create the resumability bookkeeping table if it doesn't exist."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS body_status (
            item_id    TEXT PRIMARY KEY,
            status     TEXT NOT NULL,   -- 'fetched' | 'no_pdf' | 'error'
            pdf_path   TEXT,            -- path of the saved PDF (when fetched)
            bytes      INTEGER,
            note       TEXT,            -- error message / skip reason
            updated_at TEXT NOT NULL
        );
    """)
    con.commit()


def record_status(
    con: sqlite3.Connection,
    item_id: str,
    status: str,
    pdf_path: Optional[str] = None,
    nbytes: Optional[int] = None,
    note: Optional[str] = None,
) -> None:
    """Upsert one item's fetch outcome."""
    con.execute(
        """
        INSERT OR REPLACE INTO body_status
            (item_id, status, pdf_path, bytes, note, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (item_id, status, pdf_path, nbytes, note,
         time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
    )
    con.commit()


def already_done(con: sqlite3.Connection, skip_errors: bool) -> set:
    """Return item_ids that a resumed run should skip.

    Always skips terminal outcomes (fetched / no_pdf).  Also skips prior
    errors when skip_errors is set; otherwise errors are retried.
    """
    statuses = list(TERMINAL_STATUSES)
    if skip_errors:
        statuses.append("error")
    placeholders = ",".join("?" * len(statuses))
    rows = con.execute(
        f"SELECT item_id FROM body_status WHERE status IN ({placeholders})",
        statuses,
    ).fetchall()
    return {r[0] for r in rows}


def find_pdf_url(item_json: dict) -> Optional[str]:
    """Return the whole-item PDF URL for an item, or None if it has no PDF.

    Prefers the per-resource ``pdf`` link; falls back to scanning each
    resource's ``files`` derivatives for an ``application/pdf`` entry.
    """
    item = item_json.get("item", {})
    online = item.get("online_format", []) or []
    if "pdf" not in [str(f).lower() for f in online]:
        return None

    for res in item_json.get("resources", []) or []:
        if res.get("pdf"):
            return res["pdf"]
        for page in res.get("files", []) or []:
            for derivative in page or []:
                if derivative.get("mimetype") == "application/pdf" and derivative.get("url"):
                    return derivative["url"]
    return None


def fetch_item_json(session: requests.Session, item_url: str) -> Optional[dict]:
    """Fetch an item's JSON record, or None on error."""
    url = item_url.rstrip("/") + "/?fo=json"
    try:
        resp = session.get(url, timeout=60, allow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"  ! item JSON failed: {exc}")
        return None


def download_pdf(session: requests.Session, pdf_url: str, dest: str) -> int:
    """Stream a PDF to ``dest``; return bytes written.

    Writes to a ``.part`` temp file and only renames into place once the
    download finishes and looks like a real PDF, so an interrupted or failed
    download never leaves a poisoned 0-byte ``.pdf`` for ``pdfs_ingest`` to
    pick up.  Raises ValueError if the result is empty or not a PDF.
    """
    tmp = dest + ".part"
    written = 0
    try:
        with session.get(pdf_url, timeout=DOWNLOAD_TIMEOUT, stream=True) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)
                    written += len(chunk)
        if written == 0:
            raise ValueError("empty download")
        with open(tmp, "rb") as fh:
            if fh.read(5) != b"%PDF-":
                raise ValueError("not a PDF (bad magic bytes)")
        os.replace(tmp, dest)
        return written
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DEFAULT_DB, help="LOC metadata DB to read item URLs from")
    parser.add_argument("--limit", type=int, default=50, help="number of PDFs to download this run")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="where to save PDFs")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY, help="seconds between items")
    parser.add_argument("--skip-errors", action="store_true",
                        help="leave prior 'error' rows alone instead of retrying them")
    args = parser.parse_args()

    if not _EMAIL:
        parser.error("DATASETS_EMAIL env var is required for the User-Agent contact address.")
    if not os.path.exists(args.db):
        parser.error(f"metadata DB not found: {args.db} (run loc_download.py first)")

    os.makedirs(args.out_dir, exist_ok=True)

    con = sqlite3.connect(args.db, timeout=30)
    con.execute("PRAGMA busy_timeout = 30000")
    con.row_factory = sqlite3.Row
    create_status_table(con)

    done = already_done(con, args.skip_errors)
    rows = con.execute("SELECT item_id, title, url FROM items ORDER BY id").fetchall()

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    fetched = scanned = skipped = errors = resumed = 0
    print(f"{len(rows)} items in {args.db}; {len(done)} already processed (skipping). "
          f"Looking for {args.limit} more PDFs.")

    for row in rows:
        if fetched >= args.limit:
            break
        item_id = row["item_id"]
        item_url = row["url"] or item_id
        if not item_url:
            continue
        if item_id in done:
            resumed += 1
            continue

        dest = os.path.join(args.out_dir, item_stem(item_url) + ".pdf")

        scanned += 1
        print(f"[{scanned}] {(row['title'] or '')[:70]}")
        data = fetch_item_json(session, item_url)
        time.sleep(args.delay)
        if data is None:
            errors += 1
            record_status(con, item_id, "error", note="item JSON fetch failed")
            continue

        pdf_url = find_pdf_url(data)
        if not pdf_url:
            skipped += 1
            online = ", ".join(data.get("item", {}).get("online_format", [])) or "none"
            print(f"  - no PDF (online_format: {online}) — skipped")
            record_status(con, item_id, "no_pdf", note=f"online_format: {online}")
            continue

        try:
            size = download_pdf(session, pdf_url, dest)
        except (requests.RequestException, ValueError, OSError) as exc:
            errors += 1
            print(f"  ! download failed: {exc}")
            record_status(con, item_id, "error", note=str(exc))
            continue
        fetched += 1
        rel = os.path.relpath(dest, args.out_dir)
        print(f"  + {dest}  ({size / 1024:.0f} KB)")
        record_status(con, item_id, "fetched", pdf_path=rel, nbytes=size)
        time.sleep(args.delay)

    con.close()
    print(f"\nDone. fetched={fetched}  no_pdf={skipped}  errors={errors}  "
          f"(scanned this run={scanned}, resume-skipped={resumed})")
    print(f"PDFs in: {args.out_dir}")


if __name__ == "__main__":
    main()
