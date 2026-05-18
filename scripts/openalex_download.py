#!/usr/bin/env python3

import sqlite3
import requests
import os
import time

# Configuration
DB_PATH = os.path.expanduser("data/openalex/openalex.db")
MIN_CITATIONS = 500
EMAIL = "sagansagansagan@protonmail.com"

# Setup SQLite
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

# Fetch from OpenAlex API
base_url = "https://api.openalex.org/works"
params = {
    "filter": f"cited_by_count:>{MIN_CITATIONS},has_abstract:true",
    "select": "id,title,abstract_inverted_index,publication_year,cited_by_count,doi,authorships,primary_location",
    "per_page": 200,
    "cursor": "*",
    "mailto": EMAIL
}

page = 0
total_inserted = 0

while True:
    response = requests.get(base_url, params=params)
    if response.status_code != 200:
        print(f"Error: {response.status_code} - {response.text}")
        break

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
        venue = ""
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
            venue
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

con.close()
print(f"Done. Total records inserted: {total_inserted}")