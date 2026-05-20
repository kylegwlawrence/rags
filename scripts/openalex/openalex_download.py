#!/usr/bin/env python3
"""Download top-cited OpenAlex works into data/openalex/openalex.db.

Paginates the /works API with cursor pagination, reconstructs abstracts from
the inverted-index format, and writes rows to a `works` table. Re-runnable
via `INSERT OR IGNORE`. Uses the OpenAlex polite pool (`mailto=` param) for
rate-limit favoritism — the `EMAIL` constant matters.

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

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag import retry  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "openalex" / "openalex.db"
MIN_CITATIONS = 500
EMAIL = os.environ.get("OPENALEX_EMAIL", "sagansagansagan@protonmail.com")


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
            venue TEXT
        )
    """)
    con.commit()

    base_url = "https://api.openalex.org/works"
    params = {
        "filter": f"cited_by_count:>{MIN_CITATIONS},has_abstract:true",
        "select": "id,title,abstract_inverted_index,publication_year,cited_by_count,doi,authorships,primary_location",
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

            cur.execute("""
                INSERT OR IGNORE INTO works (id, title, abstract, year, cited_by_count, doi, authors, venue)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                work.get("id"),
                work.get("title"),
                abstract,
                work.get("publication_year"),
                work.get("cited_by_count"),
                work.get("doi"),
                authors,
                venue,
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
    con.commit()

    con.close()
    print(f"Done. Total records inserted: {total_inserted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
