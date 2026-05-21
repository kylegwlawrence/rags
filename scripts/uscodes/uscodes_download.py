#!/usr/bin/env python3
"""Download the full United States Code in USLM XML format.

Fetches the current-release zip from uscode.house.gov, extracts each title's
XML, parses every section's heading and text, and writes rows into a SQLite
`sections` table. Re-runnable: uses INSERT OR REPLACE with a unique constraint
on (title_num, section_num).

The release-point URL changes with each public-law update (current: 119-90).
If the download fails or returns HTML, visit https://uscode.house.gov/download/download.shtml,
copy the current 'zip' link for 'All titles (XML)', and pass it via --zip-url.
"""

import argparse
import os
import re
import sqlite3
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag.cleaner import normalize_whitespace  # noqa: E402

ZIP_URL = (
    "https://uscode.house.gov/download/releasepoints/us/pl/119/90/"
    "xml_uscAll@119-90.zip"
)
CHUNK_BYTES = 1 << 20  # 1 MiB


def local_tag(tag: str) -> str:
    """Strip the Clark-notation namespace prefix from an ElementTree tag."""
    return tag.split("}")[-1] if "}" in tag else tag


def extract_title_num(filename: str) -> str:
    """Return the title number from a filename like usc01.xml → '01'."""
    m = re.search(r"usc(\w+)\.xml", filename)
    return m.group(1) if m else filename


def download_zip(url: str, dest: Path) -> None:
    """Stream url to dest via a .tmp sibling; rename atomically on success."""
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        print(f"Downloading {url}")
        r = requests.get(url, stream=True, timeout=600)
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_content(chunk_size=CHUNK_BYTES):
                f.write(chunk)
        os.replace(tmp, dest)
        print("Download complete.")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def parse_uslm(title_num: str, xml_bytes: bytes) -> list[tuple[str, str, str, str]]:
    """Parse a USLM title XML; return (title_num, section_num, heading, content) rows."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        print(f"    Parse error in title {title_num}: {e}")
        return []

    rows: list[tuple[str, str, str, str]] = []
    for elem in root.iter():
        if local_tag(elem.tag) != "section":
            continue

        num_el = None
        heading_el = None
        for child in elem:
            lt = local_tag(child.tag)
            if lt == "num" and num_el is None:
                num_el = child
            elif lt == "heading" and heading_el is None:
                heading_el = child

        section_num = "".join(num_el.itertext()).strip() if num_el is not None else ""
        heading = "".join(heading_el.itertext()).strip() if heading_el is not None else ""
        content = normalize_whitespace("".join(elem.itertext()))

        if not content:
            continue

        rows.append((title_num, section_num, heading, content))

    return rows


def create_schema(con: sqlite3.Connection) -> None:
    """Create the sections table and index if they don't already exist."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS sections (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            title_num  TEXT,
            section_num TEXT,
            heading  TEXT,
            content  TEXT,
            UNIQUE (title_num, section_num)
        );
        CREATE INDEX IF NOT EXISTS idx_title ON sections(title_num);
    """)
    con.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=REPO_ROOT / "data" / "uscode" / "uscode.db",
    )
    parser.add_argument(
        "--download-dir",
        type=Path,
        default=REPO_ROOT / "data" / "uscode" / "raw",
    )
    parser.add_argument(
        "--zip-url",
        default=ZIP_URL,
        help="URL of the current USLM all-titles zip.",
    )
    args = parser.parse_args(argv)

    args.db.parent.mkdir(parents=True, exist_ok=True)
    args.download_dir.mkdir(parents=True, exist_ok=True)

    zip_path = args.download_dir / "uscAll.zip"
    if not zip_path.exists():
        download_zip(args.zip_url, zip_path)
    else:
        print("Zip already downloaded, skipping.")

    con = sqlite3.connect(args.db)
    create_schema(con)

    print("\nParsing US Code titles...")
    total = 0
    with zipfile.ZipFile(zip_path, "r") as z:
        xml_files = [n for n in z.namelist() if n.endswith(".xml") and "usc" in n.lower()]
        for name in sorted(xml_files):
            title_num = extract_title_num(os.path.basename(name))
            print(f"  Title {title_num} ({os.path.basename(name)})...", end=" ", flush=True)
            with z.open(name) as f:
                rows = parse_uslm(title_num, f.read())
            con.executemany(
                "INSERT OR REPLACE INTO sections (title_num, section_num, heading, content) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )
            con.commit()
            total += len(rows)
            print(f"{len(rows)} sections (total: {total})")

    con.close()
    print(f"\nDone. {total} sections → {args.db}")
    print(f"Zip kept at {zip_path} — delete manually if not needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
