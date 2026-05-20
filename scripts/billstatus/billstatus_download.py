#!/usr/bin/env python3
"""Download BILLSTATUS XML bulk data from GPO and extract bill summaries into SQLite.

Covers 108th Congress (2003) to present.
Requires: requests
"""

import argparse
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from typing import Optional

import requests

DEFAULT_DB = "./data/congress/bill_summaries.db"
DEFAULT_DOWNLOAD_DIR = "./data/congress/raw"
START_CONGRESS = 108   # 108th = 2003 (earliest available)
DEFAULT_END_CONGRESS = 119

BILL_TYPES = ["hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres"]
BASE_URL = "https://www.govinfo.gov/bulkdata/BILLSTATUS"


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS bills (
            bill_id         TEXT PRIMARY KEY,
            congress        INTEGER,
            bill_type       TEXT,
            bill_number     TEXT,
            title           TEXT,
            sponsor         TEXT,
            introduced_date TEXT,
            latest_action   TEXT,
            policy_area     TEXT,
            subjects        TEXT,
            summary         TEXT
        );
        CREATE TABLE IF NOT EXISTS ingest_state (
            id                      INTEGER PRIMARY KEY CHECK (id = 1),
            last_completed_congress INTEGER,
            last_completed_bill_type TEXT
        );
        INSERT OR IGNORE INTO ingest_state (id) VALUES (1);
    """)


def get_last_completed(cur: sqlite3.Cursor) -> tuple[Optional[int], Optional[str]]:
    row = cur.execute(
        "SELECT last_completed_congress, last_completed_bill_type FROM ingest_state WHERE id = 1"
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def get_text(element: ET.Element, path: str) -> str:
    found = element.find(path)
    return found.text.strip() if found is not None and found.text else ""


def parse_bill_xml(xml_content: bytes) -> Optional[tuple]:
    """Parse a single BILLSTATUS XML file and return a row tuple, or None on failure."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    bill = root.find("bill")
    if bill is None:
        return None

    congress    = get_text(bill, "congress")
    bill_type   = get_text(bill, "billType") or get_text(bill, "type")
    bill_number = get_text(bill, "billNumber") or get_text(bill, "number")
    title       = get_text(bill, "title")

    sponsor = ""
    sponsors = bill.find("sponsors")
    if sponsors is not None:
        first = sponsors.find("item")
        if first is not None:
            sponsor = get_text(first, "fullName")

    introduced_date = get_text(bill, "introducedDate")

    latest_action = ""
    la = bill.find("latestAction")
    if la is not None:
        latest_action = get_text(la, "text")

    policy_area = ""
    pa = bill.find("policyArea")
    if pa is not None:
        policy_area = get_text(pa, "name")

    subjects: list[str] = []
    subj_container = bill.find("subjects")
    if subj_container is not None:
        for item in subj_container.iter("name"):
            if item.text:
                subjects.append(item.text.strip())
    subjects_str = "; ".join(set(subjects))

    summary_text = ""
    summaries = bill.find("summaries")
    if summaries is not None:
        summary_items = summaries.findall(".//summary")
        if summary_items:
            raw = get_text(summary_items[-1], "text")
            summary_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()

    bill_id = f"{congress}-{bill_type}-{bill_number}"
    return (
        bill_id, int(congress) if congress.isdigit() else None,
        bill_type, bill_number, title, sponsor, introduced_date,
        latest_action, policy_area, subjects_str, summary_text,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download GPO BILLSTATUS XML bulk data into SQLite."
    )
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"Path to SQLite database (default: {DEFAULT_DB})")
    parser.add_argument("--download-dir", default=DEFAULT_DOWNLOAD_DIR,
                        help=f"Directory for downloaded zip files (default: {DEFAULT_DOWNLOAD_DIR})")
    parser.add_argument("--congress-from", type=int, default=None,
                        help=f"Start Congress number (default: resume from last run, or {START_CONGRESS})")
    parser.add_argument("--congress-to", type=int, default=DEFAULT_END_CONGRESS,
                        help=f"End Congress number inclusive (default: {DEFAULT_END_CONGRESS})")
    args = parser.parse_args()

    db_dir = os.path.dirname(args.db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    os.makedirs(args.download_dir, exist_ok=True)

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    create_schema(cur)
    con.commit()

    last_congress, last_bill_type = get_last_completed(cur)

    if args.congress_from is not None:
        congress_from = args.congress_from
        skip = False
    else:
        congress_from = last_congress if last_congress is not None else START_CONGRESS
        skip = last_congress is not None
        if skip:
            print(f"Resuming after {last_congress}/{last_bill_type}")

    total_inserted = 0

    for congress in range(congress_from, args.congress_to + 1):
        for bill_type in BILL_TYPES:
            # Fast-forward to the position after the last completed pair
            if skip:
                if congress == last_congress and bill_type == last_bill_type:
                    skip = False
                continue

            url = f"{BASE_URL}/{congress}/{bill_type}/BILLSTATUS-{congress}-{bill_type}.zip"
            zip_path = os.path.join(args.download_dir, f"BILLSTATUS-{congress}-{bill_type}.zip")
            print(f"\nFetching {congress}/{bill_type}...")

            try:
                response = requests.get(url, stream=True, timeout=120)
                if response.status_code == 404:
                    print("  Not found — skipping")
                    continue
                if response.status_code != 200:
                    print(f"  HTTP {response.status_code} — skipping")
                    continue
                with open(zip_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
            except requests.RequestException as e:
                print(f"  Request failed: {e} — skipping")
                continue

            count = 0
            try:
                with zipfile.ZipFile(zip_path, "r") as z:
                    for filename in z.namelist():
                        if not filename.endswith(".xml"):
                            continue
                        with z.open(filename) as f:
                            parsed = parse_bill_xml(f.read())
                        if parsed is None:
                            continue
                        cur.execute("""
                            INSERT OR IGNORE INTO bills
                            (bill_id, congress, bill_type, bill_number, title,
                             sponsor, introduced_date, latest_action, policy_area,
                             subjects, summary)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, parsed)
                        count += cur.rowcount
            except zipfile.BadZipFile:
                print("  Bad zip file — skipping")
                os.remove(zip_path)
                continue
            finally:
                if os.path.exists(zip_path):
                    os.remove(zip_path)

            con.commit()
            cur.execute(
                "UPDATE ingest_state SET last_completed_congress=?, last_completed_bill_type=? WHERE id=1",
                (congress, bill_type),
            )
            con.commit()

            total_inserted += count
            print(f"  {count} bills inserted (total: {total_inserted})")

    con.close()
    print(f"\nDone. Total bills inserted: {total_inserted}")


if __name__ == "__main__":
    main()
