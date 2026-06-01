#!/usr/bin/env python3
"""Download top-cited OpenAlex works into data/openalex/openalex.db.

Paginates the /works API with cursor pagination, reconstructs abstracts from
the inverted-index format, and writes rows to a `works` table. Re-runnable
via an upsert (`INSERT ... ON CONFLICT DO UPDATE`), so a re-run refreshes
every field — including the open-access location columns — on existing rows.
Uses the OpenAlex polite pool (`mailto=` param) for rate-limit favoritism —
the `EMAIL` constant matters.

OpenAlex serves only metadata + the abstract, never the full body text. What
it *does* expose is where an open-access copy lives, which we capture in four
columns (`is_oa`, `oa_status`, `oa_url`, `pdf_url`) so a later fetcher can
download the actual PDF/HTML for the open-access subset.

Author normalization (the `authors` / `work_authors` tables) is handled by a
separate one-shot, `scripts/openalex_normalize_authors.py`; FTS over title +
abstract is built by `scripts/openalex_index_fts.py`. Run both after this.
"""

import os
import sqlite3
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag import retry  # noqa: E402

load_dotenv()  # read DATASETS_EMAIL etc. from .env before the constants below

DB_PATH = REPO_ROOT / "data" / "openalex" / "openalex.db"
MIN_CITATIONS = 500
EMAIL = os.environ.get("DATASETS_EMAIL")


def fetch_with_retry(url: str, params: dict) -> requests.Response:
    """GET with exponential backoff; raises the last exception after retries.

    Delegates the retry/backoff policy to `rag.retry.with_retry`. A single
    transient 5xx used to break the cursor loop entirely (losing the page-
    pagination position); now it costs up to ~6 seconds of backoff before
    either recovering or surfacing the error.
    """
    def _call() -> requests.Response:
        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()
        return response

    return retry.with_retry(_call, requests.RequestException)


def main() -> int:
    if not EMAIL:
        print(
            "DATASETS_EMAIL env var is not set; required for the OpenAlex "
            "polite pool. Set it and re-run.",
            file=sys.stderr,
        )
        return 1

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS works (
            id TEXT PRIMARY KEY,
            title TEXT,
            abstract TEXT,
            year INTEGER,
            cited_by_count INTEGER,
            doi TEXT,
            authors TEXT,
            venue TEXT,
            is_oa INTEGER,
            oa_status TEXT,
            oa_url TEXT,
            pdf_url TEXT
        )
    """)
    # Migrate DBs created before the open-access columns existed. CREATE TABLE
    # IF NOT EXISTS leaves an existing table untouched, so add any missing
    # columns explicitly (idempotent — skip the ones already present).
    existing_cols = {row[1] for row in cur.execute("PRAGMA table_info(works)")}
    for col in ("is_oa", "oa_status", "oa_url", "pdf_url"):
        if col not in existing_cols:
            col_type = "INTEGER" if col == "is_oa" else "TEXT"
            cur.execute(f"ALTER TABLE works ADD COLUMN {col} {col_type}")
    con.commit()

    base_url = "https://api.openalex.org/works"
    params = {
        "filter": f"cited_by_count:>{MIN_CITATIONS},has_abstract:true",
        "select": "id,title,abstract_inverted_index,publication_year,cited_by_count,doi,authorships,primary_location,open_access,best_oa_location",
        "per_page": 200,
        "cursor": "*",
        "mailto": EMAIL,
    }

    page = 0
    total_inserted = 0

    while True:
        try:
            response = fetch_with_retry(base_url, params)
        except requests.RequestException as e:
            print(f"Error after {retry.MAX_ATTEMPTS} attempts: {e}", file=sys.stderr)
            return 1

        data = response.json()
        results = data.get("results", [])
        if not results:
            break

        for work in results:
            # Reconstruct abstract from inverted index
            abstract = ""
            inv_index = work.get("abstract_inverted_index")
            if inv_index:
                word_positions = []
                for word, positions in inv_index.items():
                    for pos in positions:
                        word_positions.append((pos, word))
                word_positions.sort()
                abstract = " ".join(w for _, w in word_positions)

            # Authors
            authors = ", ".join(
                a.get("author", {}).get("display_name", "")
                for a in work.get("authorships", [])
            )

            # Venue
            primary = work.get("primary_location") or {}
            source = primary.get("source") or {}
            venue = source.get("display_name", "")

            # Open-access location. OpenAlex never serves the body itself, but
            # it tells us whether a free copy exists and where: `oa_url` is the
            # best free landing/PDF URL, `pdf_url` (from best_oa_location) is a
            # direct PDF link when one is known. Both may be absent.
            open_access = work.get("open_access") or {}
            is_oa = 1 if open_access.get("is_oa") else 0
            oa_status = open_access.get("oa_status")
            oa_url = open_access.get("oa_url")
            best_oa = work.get("best_oa_location") or {}
            pdf_url = best_oa.get("pdf_url")

            # Upsert so a re-run refreshes every column (including the OA
            # fields) on rows that already exist — `INSERT OR IGNORE` would
            # silently skip them and leave the OA columns NULL forever.
            cur.execute("""
                INSERT INTO works (
                    id, title, abstract, year, cited_by_count, doi, authors, venue,
                    is_oa, oa_status, oa_url, pdf_url
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    abstract=excluded.abstract,
                    year=excluded.year,
                    cited_by_count=excluded.cited_by_count,
                    doi=excluded.doi,
                    authors=excluded.authors,
                    venue=excluded.venue,
                    is_oa=excluded.is_oa,
                    oa_status=excluded.oa_status,
                    oa_url=excluded.oa_url,
                    pdf_url=excluded.pdf_url
            """, (
                work.get("id"),
                work.get("title"),
                abstract,
                work.get("publication_year"),
                work.get("cited_by_count"),
                work.get("doi"),
                authors,
                venue,
                is_oa,
                oa_status,
                oa_url,
                pdf_url,
            ))

        con.commit()
        total_inserted += len(results)
        page += 1
        print(f"Page {page} done — total records: {total_inserted}")

        # Next cursor
        next_cursor = data.get("meta", {}).get("next_cursor")
        if not next_cursor:
            break
        params["cursor"] = next_cursor

        time.sleep(0.1)  # Polite pool allows up to 10 req/sec

    # Indexes for the API's list-with-filters endpoint.
    cur.execute("CREATE INDEX IF NOT EXISTS idx_works_cited_by_count ON works(cited_by_count)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_works_year ON works(year)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_works_venue ON works(venue)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_works_is_oa ON works(is_oa)")
    con.commit()

    con.close()
    print(f"Done. Total records inserted: {total_inserted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
