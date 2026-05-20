#!/usr/bin/env python3
"""Download the official Python docs (plain-text archive) into SQLite."""

import argparse
import os
import sqlite3
import tarfile

import requests

DEFAULT_DB = "./data/pydocs/python_docs.db"
DEFAULT_DOWNLOAD_DIR = "./data/pydocs/raw"
DEFAULT_PYTHON_VERSION = "3"  # "3" redirects to current stable; pin e.g. "3.13"


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS docs (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_path TEXT UNIQUE,
            section  TEXT,
            title    TEXT,
            content  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_section ON docs(section);
    """)


def download_archive(url: str, dest: str) -> None:
    """Download the docs archive, skipping if already present."""
    if os.path.exists(dest):
        print("Archive already downloaded.")
        return
    print(f"Downloading {url}...")
    r = requests.get(url, stream=True, timeout=300)
    if r.status_code != 200:
        raise SystemExit(
            f"Error {r.status_code} fetching archive.\n"
            "The generic URL may not resolve — visit https://docs.python.org/3/download.html\n"
            "to find the exact text archive URL and pass it via --python-version."
        )
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
    print("Download complete.")


def parse_archive(archive_path: str, cur: sqlite3.Cursor, con: sqlite3.Connection) -> int:
    """Extract .txt files from the archive and insert them into docs."""
    total = 0
    with tarfile.open(archive_path, "r:bz2") as tar:
        for member in tar.getmembers():
            if not member.isfile() or not member.name.endswith(".txt"):
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            content = f.read().decode("utf-8", errors="replace")

            # "python-3.13.1-docs-text/library/os.txt" → "library/os"
            parts = member.name.split("/", 1)
            rel_path = parts[1] if len(parts) > 1 else member.name
            doc_path = rel_path.removesuffix(".txt")

            section = doc_path.split("/")[0] if "/" in doc_path else "root"

            title = next((l.strip() for l in content.splitlines() if l.strip()), "")

            cur.execute("""
                INSERT OR IGNORE INTO docs (doc_path, section, title, content)
                VALUES (?, ?, ?, ?)
            """, (doc_path, section, title, content))
            total += cur.rowcount

            if total % 100 == 0 and total > 0:
                con.commit()

    con.commit()
    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Python docs plain-text archive into SQLite."
    )
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"Path to SQLite database (default: {DEFAULT_DB})")
    parser.add_argument("--download-dir", default=DEFAULT_DOWNLOAD_DIR,
                        help=f"Directory for downloaded archive (default: {DEFAULT_DOWNLOAD_DIR})")
    parser.add_argument("--python-version", default=DEFAULT_PYTHON_VERSION,
                        help=f"Python version to fetch, e.g. '3' or '3.13' (default: {DEFAULT_PYTHON_VERSION})")
    args = parser.parse_args()

    db_dir = os.path.dirname(args.db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    os.makedirs(args.download_dir, exist_ok=True)

    version = args.python_version
    archive_url = f"https://docs.python.org/{version}/archives/python-{version}-docs-text.tar.bz2"
    archive_path = os.path.join(args.download_dir, "python-docs-text.tar.bz2")

    download_archive(archive_url, archive_path)

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    create_schema(cur)
    con.commit()

    print("Extracting and parsing documentation...")
    try:
        total = parse_archive(archive_path, cur, con)
    finally:
        con.close()

    print(f"\nDone. {total} documents inserted into {args.db}")
    print("Sections include: tutorial, library, reference, howto, faq, etc.")


if __name__ == "__main__":
    main()
