#!/usr/bin/env python3
"""Parse LOC MARC bulk records from local files into SQLite.

Download MARC files manually from https://loc.gov/cds/products/marcDist.php
(files named like BooksAll.2014.part01.utf8.gz), place them in --download-dir,
then run this script.

Requires: pymarc  (pip install pymarc)
"""

import argparse
import glob
import gzip
import os
import shutil
import sqlite3
import tempfile

try:
    from pymarc import MARCReader
except ImportError:
    raise SystemExit(
        "pymarc is required but not installed.\n"
        "Run: pip install pymarc"
    )

DEFAULT_DB = "./data/loc/loc_books.db"
DEFAULT_DOWNLOAD_DIR = "./data/loc/raw"


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS books (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            lccn             TEXT UNIQUE,
            title            TEXT,
            author           TEXT,
            publication_date TEXT,
            publisher        TEXT,
            subject          TEXT,
            summary          TEXT,
            language         TEXT,
            item_type        TEXT
        );
    """)


def parse_marc_record(record) -> tuple:
    """Extract fields from a MARC record."""
    def get_field(tag: str, subfields: list | None = None) -> str:
        field = record.get(tag)
        if not field:
            return ""
        if subfields:
            return " ".join(field.get_subfields(*subfields)).strip()
        return str(field.value()).strip()

    def get_all_fields(tag: str, subfield: str) -> str:
        return "; ".join(
            f.get_subfields(subfield)[0]
            for f in record.get_fields(tag)
            if f.get_subfields(subfield)
        )

    lccn      = get_field("010", ["a"])
    title     = get_field("245", ["a", "b"])
    author    = get_field("100", ["a"]) or get_field("110", ["a"]) or get_field("111", ["a"])
    pub_date  = get_field("260", ["c"]) or get_field("264", ["c"])
    publisher = get_field("260", ["b"]) or get_field("264", ["b"])
    subject   = get_all_fields("650", "a")
    summary   = get_field("520", ["a"])
    language  = get_field("041", ["a"]) or (record.leader[17] if len(record.leader) > 17 else "")
    item_type = get_field("655", ["a"])

    return lccn, title, author, pub_date, publisher, subject, summary, language, item_type


def process_marc_file(filepath: str, cur: sqlite3.Cursor, con: sqlite3.Connection) -> int:
    """Parse a MARC file and insert English records into SQLite."""
    count = 0
    with open(filepath, "rb") as f:
        reader = MARCReader(f, to_unicode=True, force_utf8=True)
        for record in reader:
            try:
                if record is None:
                    continue
                lccn, title, author, pub_date, publisher, subject, summary, language, item_type = parse_marc_record(record)

                if language and language.strip() not in ("eng", "en"):
                    continue

                cur.execute("""
                    INSERT OR IGNORE INTO books
                    (lccn, title, author, publication_date, publisher, subject, summary, language, item_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (lccn, title, author, pub_date, publisher, subject, summary, language, item_type))
                inserted = cur.rowcount
                count += inserted

                # Commit periodically; gate on a real insert so a run of
                # duplicates can't re-trigger at the same count.
                if inserted and count % 10000 == 0:
                    con.commit()
                    print(f"  {count} records inserted...")

            except Exception as e:
                print(f"  Warning: skipped record — {e}")

    con.commit()
    return count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse LOC MARC bulk files into SQLite."
    )
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"Path to SQLite database (default: {DEFAULT_DB})")
    parser.add_argument("--download-dir", default=DEFAULT_DOWNLOAD_DIR,
                        help=f"Directory containing MARC files (default: {DEFAULT_DOWNLOAD_DIR})")
    args = parser.parse_args()

    db_dir = os.path.dirname(args.db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    os.makedirs(args.download_dir, exist_ok=True)

    marc_files = (
        glob.glob(os.path.join(args.download_dir, "*.mrc")) +
        glob.glob(os.path.join(args.download_dir, "*.gz")) +
        glob.glob(os.path.join(args.download_dir, "*.utf8"))
    )

    if not marc_files:
        raise SystemExit(
            f"No MARC files found in {args.download_dir}\n\n"
            "Download bulk files from:\n"
            "  https://loc.gov/cds/products/marcDist.php\n\n"
            "Look for files named like:\n"
            "  BooksAll.2014.part01.utf8.gz\n\n"
            f"Place them in: {args.download_dir}"
        )

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    create_schema(cur)
    con.commit()

    total = 0
    try:
        for filepath in sorted(marc_files):
            print(f"\nProcessing: {os.path.basename(filepath)}")

            if filepath.endswith(".gz"):
                # Stream decompression to avoid loading multi-GB files into memory
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mrc") as tmp:
                    tmp_path = tmp.name
                try:
                    with gzip.open(filepath, "rb") as gz_in, open(tmp_path, "wb") as out:
                        shutil.copyfileobj(gz_in, out)
                    count = process_marc_file(tmp_path, cur, con)
                finally:
                    os.remove(tmp_path)
            else:
                count = process_marc_file(filepath, cur, con)

            print(f"  Done — {count} records from {os.path.basename(filepath)}")
            total += count
    finally:
        con.close()

    print(f"\nDone. Total records inserted: {total}")


if __name__ == "__main__":
    main()
