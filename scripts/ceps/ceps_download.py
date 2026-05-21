#!/usr/bin/env python3
"""Download the CEPS EurLex dataset (142k EU laws, 1952–2019) from Harvard Dataverse into SQLite."""

import argparse
import csv
import os
import re
import sqlite3
import sys

import requests

DEFAULT_DB = "./data/eurlex/eurlex.db"
DEFAULT_DOWNLOAD_DIR = "./data/eurlex/raw"
PERSISTENT_ID = "doi:10.7910/DVN/0EGYWY"
DATAVERSE_BASE = "https://dataverse.harvard.edu"


def sanitize_column(name: str) -> str:
    """Return a safe SQL identifier: alphanumeric + underscores, no leading digit."""
    safe = re.sub(r"[^\w]", "_", name.strip())
    if safe and safe[0].isdigit():
        safe = f"col_{safe}"
    return safe or "col"


def fetch_file_list() -> list[dict]:
    """Return the list of file metadata dicts from the Dataverse API."""
    url = f"{DATAVERSE_BASE}/api/datasets/:persistentId/"
    resp = requests.get(url, params={"persistentId": PERSISTENT_ID}, timeout=60)
    resp.raise_for_status()
    return resp.json()["data"]["latestVersion"]["files"]


def download_csv_files(files: list[dict], download_dir: str) -> list[str]:
    """Download CSV/tab files from Dataverse, skipping already-present ones."""
    paths = []
    for f in files:
        filename = f["dataFile"]["filename"]
        file_id = f["dataFile"]["id"]
        size = f["dataFile"].get("filesize", 0)

        if not (filename.endswith(".csv") or filename.endswith(".tab")):
            print(f"  Skipping non-CSV file: {filename}")
            continue

        dest = os.path.join(download_dir, filename)
        if os.path.exists(dest):
            print(f"  Already downloaded: {filename}")
            paths.append(dest)
            continue

        tmp = dest + ".tmp"
        print(f"  Downloading {filename} ({size / 1e6:.1f} MB)...")
        r = requests.get(
            f"{DATAVERSE_BASE}/api/access/datafile/{file_id}",
            params={"format": "original"},
            stream=True,
            timeout=600,
        )
        r.raise_for_status()
        try:
            with open(tmp, "wb") as out:
                for chunk in r.iter_content(chunk_size=8192):
                    out.write(chunk)
            os.replace(tmp, dest)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        paths.append(dest)

    return paths


def load_csv_into_db(
    csv_path: str, cur: sqlite3.Cursor, con: sqlite3.Connection, reset: bool
) -> int:
    """Parse a CSV/tab file and insert rows into the laws table."""
    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        sample = f.read(8192)
        f.seek(0)
        delimiter = "\t" if "\t" in sample and csv_path.endswith(".tab") else ","
        reader = csv.DictReader(f, delimiter=delimiter)
        columns = reader.fieldnames

        if not columns:
            print("  No columns detected — skipping")
            return 0

        safe_cols = [sanitize_column(c) for c in columns]
        col_defs = ", ".join(f'"{c}" TEXT' for c in safe_cols)

        if reset:
            cur.execute("DROP TABLE IF EXISTS laws")
        cur.execute(f"CREATE TABLE IF NOT EXISTS laws ({col_defs})")
        con.commit()

        # Without --reset, skip if the table already has data
        if not reset:
            existing = cur.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
            if existing > 0:
                print(f"  laws table already has {existing:,} rows — use --reset to reimport")
                return 0

        quoted_cols = ", ".join(f'"{c}"' for c in safe_cols)
        placeholders = ", ".join("?" for _ in safe_cols)
        insert_sql = f"INSERT INTO laws ({quoted_cols}) VALUES ({placeholders})"

        count = 0
        for row in reader:
            cur.execute(insert_sql, [row.get(c, "") for c in columns])
            count += 1
            if count % 1000 == 0:
                con.commit()
                print(f"  {count:,} rows inserted...")

    con.commit()
    return count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download CEPS EurLex dataset from Harvard Dataverse into SQLite."
    )
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"Path to SQLite database (default: {DEFAULT_DB})")
    parser.add_argument("--download-dir", default=DEFAULT_DOWNLOAD_DIR,
                        help=f"Directory for downloaded files (default: {DEFAULT_DOWNLOAD_DIR})")
    parser.add_argument("--reset", action="store_true",
                        help="Drop and recreate the laws table before importing")
    args = parser.parse_args()

    db_dir = os.path.dirname(args.db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    os.makedirs(args.download_dir, exist_ok=True)

    # sys.maxsize overflows the C long limit on Linux; cap at 2^31-1
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

    print("Fetching dataset file list from Harvard Dataverse...")
    files = fetch_file_list()
    print(f"Found {len(files)} file(s) in dataset.")

    print("\nDownloading CSV files...")
    csv_files = download_csv_files(files, args.download_dir)

    if not csv_files:
        raise SystemExit("No CSV files found to import.")

    con = sqlite3.connect(args.db)
    cur = con.cursor()

    total = 0
    try:
        for csv_path in csv_files:
            print(f"\nImporting {os.path.basename(csv_path)}...")
            total += load_csv_into_db(csv_path, cur, con, args.reset)
    finally:
        con.close()

    print(f"\nDone. Total rows inserted: {total:,} into {args.db}")


if __name__ == "__main__":
    main()
