#!/usr/bin/env python3
"""Download Stack Exchange site archives from archive.org and load posts into SQLite.

Downloads each per-site .7z archive, extracts Posts.xml, and inserts questions
and answers. Archives are deleted after processing to keep disk usage low.

NOTE: The archive.org dump structure has shifted over time. Run with --dry-run
first to confirm the site list looks correct before committing to downloads.
Stack Overflow alone is tens of GB compressed — use --skip to exclude it.

Requires: requests, py7zr  (pip install requests py7zr)
"""

import argparse
import os
import sqlite3
import time
import xml.etree.ElementTree as ET

import requests

try:
    import py7zr
except ImportError:
    raise SystemExit(
        "py7zr is required but not installed.\n"
        "Run: pip install py7zr"
    )

DEFAULT_DB = "./data/stackexchange/stackexchange.db"
DEFAULT_DOWNLOAD_DIR = "./data/stackexchange/raw"
ARCHIVE_BASE = "https://archive.org/download/stackexchange"


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            site          TEXT,
            post_id       INTEGER,
            post_type     INTEGER,
            parent_id     INTEGER,
            title         TEXT,
            body          TEXT,
            tags          TEXT,
            score         INTEGER,
            creation_date TEXT,
            PRIMARY KEY (site, post_id)
        );
        CREATE INDEX IF NOT EXISTS idx_site ON posts(site);
    """)


def get_site_list() -> list[str]:
    """Fetch .7z filenames from the archive.org stackexchange metadata."""
    r = requests.get("https://archive.org/metadata/stackexchange", timeout=60)
    r.raise_for_status()
    files = r.json().get("files", [])
    return [
        f["name"] for f in files
        if f.get("name", "").endswith(".7z") and not f["name"].startswith("Sites")
    ]


def download_archive(filename: str, dest: str) -> bool:
    """Stream a .7z archive to disk. Returns True on success."""
    url = f"{ARCHIVE_BASE}/{filename}"
    print(f"  Downloading {filename}...")
    with requests.get(url, stream=True, timeout=600) as r:
        if r.status_code != 200:
            print(f"  HTTP {r.status_code} — skipping")
            return False
        with open(dest, "wb") as out:
            for chunk in r.iter_content(chunk_size=1 << 20):
                out.write(chunk)
    return True


def process_posts(
    site_name: str,
    posts_xml_path: str,
    cur: sqlite3.Cursor,
    con: sqlite3.Connection,
) -> int:
    """Parse Posts.xml with iterparse and insert rows. Returns actual insert count."""
    count = 0
    for _, elem in ET.iterparse(posts_xml_path, events=("end",)):
        if elem.tag != "row":
            elem.clear()
            continue
        try:
            post_id   = int(elem.get("Id"))
            post_type = int(elem.get("PostTypeId", 0))
            parent_id = elem.get("ParentId")
            parent_id = int(parent_id) if parent_id else None
            title     = elem.get("Title", "")
            body      = elem.get("Body", "")
            tags      = elem.get("Tags", "")
            score     = int(elem.get("Score", 0))
            created   = elem.get("CreationDate", "")

            cur.execute("""
                INSERT OR IGNORE INTO posts
                (site, post_id, post_type, parent_id, title, body, tags, score, creation_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (site_name, post_id, post_type, parent_id, title, body, tags, score, created))
            count += cur.rowcount

            if count % 10000 == 0:
                con.commit()
        except (ValueError, TypeError):
            pass
        finally:
            elem.clear()

    con.commit()
    return count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Stack Exchange archives from archive.org into SQLite."
    )
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"Path to SQLite database (default: {DEFAULT_DB})")
    parser.add_argument("--download-dir", default=DEFAULT_DOWNLOAD_DIR,
                        help=f"Temp directory for archives (default: {DEFAULT_DOWNLOAD_DIR})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and print the site list then exit without downloading")
    parser.add_argument("--skip", action="append", default=[], metavar="SITE",
                        help="Skip a site archive by name (repeatable); "
                             "e.g. --skip stackoverflow.com to avoid its 30 GB+ download")
    args = parser.parse_args()

    db_dir = os.path.dirname(args.db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    os.makedirs(args.download_dir, exist_ok=True)

    print("Fetching site list from archive.org...")
    try:
        sites = get_site_list()
    except requests.RequestException as e:
        raise SystemExit(f"Could not fetch site list: {e}")

    print(f"Found {len(sites)} site archives.\n")

    if args.dry_run:
        for name in sites:
            print(f"  {name}")
        return

    # Accept --skip with or without the .7z suffix
    skip_set = {s.removesuffix(".7z") for s in args.skip}

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    create_schema(cur)
    con.commit()

    total_posts = 0
    try:
        for i, filename in enumerate(sites, 1):
            site_name = filename.removesuffix(".7z")

            if site_name in skip_set:
                print(f"[{i}/{len(sites)}] {site_name} — skipped via --skip")
                continue

            print(f"[{i}/{len(sites)}] {site_name}")

            cur.execute("SELECT 1 FROM posts WHERE site = ? LIMIT 1", (site_name,))
            if cur.fetchone():
                print("  Already in DB — skipping")
                continue

            archive_path = os.path.join(args.download_dir, filename)
            if not download_archive(filename, archive_path):
                continue

            try:
                with py7zr.SevenZipFile(archive_path, mode="r") as z:
                    if "Posts.xml" not in z.getnames():
                        print("  No Posts.xml in archive — skipping")
                        os.remove(archive_path)
                        continue
                    z.extract(targets=["Posts.xml"], path=args.download_dir)
            except Exception as e:
                print(f"  Extraction failed: {e}")
                if os.path.exists(archive_path):
                    os.remove(archive_path)
                continue

            posts_xml_path = os.path.join(args.download_dir, "Posts.xml")
            if os.path.exists(posts_xml_path):
                count = process_posts(site_name, posts_xml_path, cur, con)
                total_posts += count
                print(f"  {count} posts inserted (total: {total_posts})")
                os.remove(posts_xml_path)

            if os.path.exists(archive_path):
                os.remove(archive_path)

            time.sleep(1)
    finally:
        con.close()

    print(f"\nDone. Total posts inserted: {total_posts}")


if __name__ == "__main__":
    main()
