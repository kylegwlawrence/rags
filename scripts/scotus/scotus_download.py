#!/usr/bin/env python3

"""
Supreme Court Database (SCDB) Downloader
Downloads the Washington University Supreme Court Database — structured data
on every SCOTUS case since 1791. Small, clean CSV, no registration needed.
Requires: requests
"""

import argparse
import csv
import io
import os
import sqlite3
import sys
import zipfile

import requests

# SCDB case-centered dataset (one row per case)
# Full list of available files: http://scdb.wustl.edu/data.php
SCDB_URL = "https://scdb.wustl.edu/data/SCDB_2025_01_caseCentered_Citation.csv.zip"
SCDB_URL_ALT = "https://scdb.wustl.edu/data/SCDB_Legacy_07_caseCentered_Citation.csv.zip"


def main() -> None:
    parser = argparse.ArgumentParser(description="Download SCDB into SQLite")
    parser.add_argument("--db", default="data/scotus/scotus.db")
    parser.add_argument("--download-dir", default="data/scotus/raw")
    args = parser.parse_args()

    db_dir = os.path.dirname(args.db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    os.makedirs(args.download_dir, exist_ok=True)
    csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))

    session = requests.Session()
    session.headers.update({"User-Agent": "scdb-fetcher"})

    # --- Download ---
    zip_path = os.path.join(args.download_dir, "scdb.csv.zip")
    tmp_path = zip_path + ".tmp"
    # Re-download a missing file, or an existing one that isn't a valid zip
    # (a complete-but-corrupt archive from an earlier run).
    if os.path.exists(zip_path) and not zipfile.is_zipfile(zip_path):
        print("Existing archive is not a valid zip — re-downloading.")
        os.remove(zip_path)
    if not os.path.exists(zip_path):
        print("Downloading SCDB dataset...")
        r = session.get(SCDB_URL, timeout=120)
        if r.status_code != 200:
            print(f"  Primary URL failed ({r.status_code}), trying alternate...")
            r = session.get(SCDB_URL_ALT, timeout=120)
        r.raise_for_status()
        with open(tmp_path, "wb") as f:
            f.write(r.content)
        os.replace(tmp_path, zip_path)
        print("Download complete.")
    else:
        print("Already downloaded.")

    # --- Extract and parse ---
    con = sqlite3.connect(args.db)
    try:
        cur = con.cursor()
        total = 0

        with zipfile.ZipFile(zip_path, "r") as z:
            csv_names = [n for n in z.namelist() if n.endswith(".csv")]
            if not csv_names:
                raise SystemExit("No CSV found inside the SCDB zip — check the download URL.")
            csv_name = csv_names[0]
            print(f"Parsing {csv_name}...")

            with z.open(csv_name) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                columns = [c.strip() for c in reader.fieldnames]

                col_defs = ", ".join(f'"{c}" TEXT' for c in columns)
                # caseId is the SCDB unique key; without it INSERT OR IGNORE has no effect
                cur.execute(
                    f'CREATE TABLE IF NOT EXISTS cases ({col_defs}, '
                    f'PRIMARY KEY ("caseId"))'
                )
                con.commit()

                placeholders = ", ".join("?" for _ in columns)
                col_list = ", ".join(f'"{c}"' for c in columns)
                insert_sql = f"INSERT OR IGNORE INTO cases ({col_list}) VALUES ({placeholders})"

                for row in reader:
                    # DictReader keys are the raw (unstripped) field names.
                    values = [row.get(raw, "") for raw in reader.fieldnames]
                    cur.execute(insert_sql, values)
                    total += 1
                    if total % 1000 == 0:
                        con.commit()

        con.commit()

        # Add useful indexes
        cur.execute('CREATE INDEX IF NOT EXISTS idx_term ON cases("term")')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_casename ON cases("caseName")')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_issue ON cases("issue")')
        con.commit()
    finally:
        con.close()

    print(f"\nDone. {total} cases inserted into {args.db}")
    print("Key fields: caseName, term, dateDecision, issue, issueArea,")
    print("            decisionDirection, majority, minVotes, majVotes, caseDisposition")
    print("\nNote: If the URL has changed, visit http://scdb.wustl.edu/data.php")
    print("and update SCDB_URL to the latest case-centered citation CSV.")


if __name__ == "__main__":
    main()
