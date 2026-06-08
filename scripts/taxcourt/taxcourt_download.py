#!/usr/bin/env python3

"""
US Tax Court Opinions Downloader
Downloads Tax Court opinions from the United States Tax Court website.
Uses CourtListener's Tax Court bulk data as the primary source.
Requires: requests

API token: free registration at courtlistener.com/profile/
Set via COURTLISTENER_API_TOKEN env var or --token flag.
"""

import argparse
import os
import sqlite3
import time
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://www.courtlistener.com/api/rest/v3"
DELAY = 0.2

# US Tax Court + Board of Tax Appeals (predecessor court)
TAX_COURT_IDS = ["tax", "bta"]


def fetch_page(
    session: requests.Session,
    url: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """GET a paginated API endpoint, retrying on rate-limit and transient errors."""
    while True:
        try:
            r = session.get(url, params=params, timeout=60)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 60))
                print(f"  Rate limited — sleeping {wait}s")
                time.sleep(wait)
                continue
            if not r.ok:
                print(f"  HTTP {r.status_code}: {r.text[:300]}")
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            print(f"  Request error: {exc} — retrying in 10s")
            time.sleep(10)


def fetch_cluster(session: requests.Session, cluster_url: str) -> dict[str, Any]:
    """Fetch OpinionCluster metadata (case name, date, judges, citations)."""
    try:
        r = session.get(cluster_url, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        print(f"  Cluster fetch failed ({cluster_url}): {exc}")
        return {}


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS opinions (
            id INTEGER PRIMARY KEY,
            case_name TEXT,
            docket_number TEXT,
            date_filed TEXT,
            citation TEXT,
            judges TEXT,
            precedential_status TEXT,
            plain_text TEXT,
            opinion_type TEXT,
            court TEXT,
            download_url TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_date ON opinions(date_filed)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_docket ON opinions(docket_number)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_court ON opinions(court)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download US Tax Court opinions from CourtListener into SQLite"
    )
    parser.add_argument("--db", default="data/taxcourt/taxcourt.db")
    parser.add_argument(
        "--token",
        default=os.environ.get("COURTLISTENER_API_TOKEN", ""),
        help="CourtListener API token (default: COURTLISTENER_API_TOKEN env var)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Stop after N opinions (default: all)"
    )
    parser.add_argument(
        "--reset", action="store_true", help="Drop and recreate table before downloading"
    )
    args = parser.parse_args()

    if not args.token:
        parser.error(
            "CourtListener API token required. Set COURTLISTENER_API_TOKEN or pass --token."
        )

    email = os.environ.get("DATASETS_EMAIL")
    if not email:
        parser.error("DATASETS_EMAIL env var is required for the User-Agent contact address.")

    os.makedirs(os.path.dirname(args.db), exist_ok=True)

    con = sqlite3.connect(args.db)
    cur = con.cursor()

    if args.reset:
        cur.execute("DROP TABLE IF EXISTS opinions")
        con.commit()

    create_schema(cur)
    con.commit()

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Token {args.token}",
        "User-Agent": f"taxcourt-fetcher/1.0 ({email})",
    })

    total = 0

    for court_id in TAX_COURT_IDS:
        if args.limit is not None and total >= args.limit:
            break

        print(f"\nDownloading court: {court_id}")
        url = f"{API_BASE}/opinions/"
        params: dict[str, Any] = {
            "format": "json",
            "page_size": 100,
            "cluster__docket__court__id": court_id,
            "order_by": "date_created",
        }

        while url:
            if args.limit is not None and total >= args.limit:
                print(f"  Reached --limit {args.limit}, stopping.")
                break

            data = fetch_page(session, url, params if "?" not in url else None)
            results = data.get("results", [])
            if not results:
                break

            for op in results:
                if args.limit is not None and total >= args.limit:
                    break

                op_id = op.get("id")
                if op_id is None:  # skip rows with no primary key
                    continue

                cur.execute("SELECT 1 FROM opinions WHERE id = ?", (op_id,))
                if cur.fetchone():
                    continue

                cluster: dict[str, Any] = {}
                cluster_url = op.get("cluster")
                if cluster_url:
                    cluster = fetch_cluster(session, cluster_url)
                    time.sleep(0.05)

                case_name = cluster.get("case_name", "")
                date_filed = cluster.get("date_filed", "")
                judges = cluster.get("judges", "")
                precedential_status = cluster.get("precedential_status", "")
                citations = cluster.get("citations", [])
                citation = citations[0].get("cite", "") if citations else ""
                # docket_number lives on the Docket object (cluster["docket"] URL),
                # not the cluster itself — fetching it would require an extra call per
                # opinion. Left empty; run a separate backfill if needed.
                docket_number = ""

                plain_text = op.get("plain_text", "") or ""
                opinion_type = op.get("type", "")
                download_url = op.get("download_url", "") or ""

                cur.execute("""
                    INSERT OR IGNORE INTO opinions
                    (id, case_name, docket_number, date_filed, citation,
                     judges, precedential_status, plain_text, opinion_type,
                     court, download_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    op_id, case_name, docket_number, date_filed, citation,
                    judges, precedential_status, plain_text, opinion_type,
                    court_id, download_url,
                ))
                total += 1

            con.commit()
            print(f"  {total} opinions inserted...")

            url = data.get("next")
            params = {}
            time.sleep(DELAY)

    con.close()
    print(f"\nDone. Total Tax Court opinions inserted: {total}")
    print(f"DB: {args.db}")


if __name__ == "__main__":
    main()
