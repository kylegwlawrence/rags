#!/usr/bin/env python3

"""
eCFR Downloader
Downloads the current Electronic Code of Federal Regulations using the
ecfr.gov API. Fetches the structure of each of the 50 titles, then the
full text of each part, into SQLite.
Requires: requests
"""

import json
import sqlite3
import requests
import os
import time
import xml.etree.ElementTree as ET

from dotenv import load_dotenv

load_dotenv()

# Configuration
DB_PATH = "data/ecfr/ecfr.db"
DELAY = 0.5  # seconds between API calls
API_BASE = "https://www.ecfr.gov/api"

# Set up in main() so importing this module has no side effects.
con = None
cur = None
session = None


def setup():
    """Open the DB, create the schema, and build the API session."""
    global con, cur, session
    mailto = os.environ.get("DATASETS_EMAIL")
    if not mailto:
        raise SystemExit(
            "DATASETS_EMAIL env var is not set; required for the eCFR API "
            "User-Agent. Set it and re-run."
        )
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS regulations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title_num INTEGER,
            title_name TEXT,
            chapter TEXT,
            part TEXT,
            section TEXT,
            heading TEXT,
            content TEXT,
            UNIQUE(title_num, section)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_title ON regulations(title_num)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ingest_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    con.commit()
    session = requests.Session()
    session.headers.update({"User-Agent": f"ecfr-fetcher mailto:{mailto}"})


def get_titles():
    """Fetch list of all CFR titles and their current dates."""
    url = f"{API_BASE}/versioner/v1/titles.json"
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json().get("titles", [])

def get_title_xml(title_num, date):
    """Fetch the full XML for a given title at a given date."""
    url = f"{API_BASE}/versioner/v1/full/{date}/title-{title_num}.xml"
    resp = session.get(url, timeout=300)
    if resp.status_code != 200:
        return None
    return resp.text

# eCFR DIV elements carry one of these on their TYPE attribute. A SECTION's
# paragraphs end where the next division begins.
DIVISION_TYPES = frozenset({
    "TITLE", "SUBTITLE", "CHAPTER", "SUBCHAPTER",
    "PART", "SUBPART", "SUBJGRP", "SECTION", "APPENDIX",
})


def section_paragraphs(section_div):
    """Yield non-empty <P> text under a SECTION, not descending into nested divisions.

    eCFR sections are flat today, so this matches a plain ``iter("P")``; the
    division-boundary guard keeps paragraphs from bleeding across sections if
    the schema ever nests them.
    """
    paragraphs = []

    def walk(elem):
        for child in elem:
            if child.get("TYPE", "") in DIVISION_TYPES:
                continue  # belongs to a nested division, not this section
            if child.tag == "P":
                text = "".join(child.itertext()).strip()
                if text:
                    paragraphs.append(text)
            walk(child)

    walk(section_div)
    return paragraphs


def parse_title_xml(title_num, title_name, xml_text):
    """Parse the eCFR XML and extract sections."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"    XML parse error: {e}")
        return 0

    count = 0
    current_chapter = ""
    current_part = ""

    # eCFR XML uses DIV elements with TYPE attributes
    for div in root.iter():
        div_type = div.get("TYPE", "")

        if div_type == "CHAPTER":
            head = div.find("HEAD")
            current_chapter = head.text.strip() if head is not None and head.text else ""
        elif div_type == "PART":
            head = div.find("HEAD")
            current_part = head.text.strip() if head is not None and head.text else ""
        elif div_type == "SECTION":
            head = div.find("HEAD")
            heading = head.text.strip() if head is not None and head.text else ""
            section_num = div.get("N", "")

            content = "\n".join(section_paragraphs(div))

            cur.execute("""
                INSERT OR REPLACE INTO regulations
                (title_num, title_name, chapter, part, section, heading, content)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (title_num, title_name, current_chapter, current_part,
                  section_num, heading, content))
            count += 1

            if count % 500 == 0:
                con.commit()

    con.commit()
    return count

def main():
    setup()

    row = cur.execute("SELECT value FROM ingest_state WHERE key='completed_titles'").fetchone()
    completed: set[int] = set(json.loads(row[0])) if row else set()
    if completed:
        print(f"Resuming — {len(completed)} titles already completed: {sorted(completed)}\n")

    print("Fetching list of CFR titles...")
    titles = get_titles()
    print(f"Found {len(titles)} titles.\n")

    total = 0
    for title in titles:
        title_num = title.get("number")
        title_name = title.get("name", "")
        date = title.get("up_to_date_as_of") or title.get("latest_issue_date")

        if title.get("reserved"):
            print(f"Title {title_num}: reserved — skipping")
            continue

        if title_num in completed:
            print(f"Title {title_num}: already done — skipping")
            continue

        print(f"Title {title_num}: {title_name} (as of {date})")

        xml_text = get_title_xml(title_num, date)
        if not xml_text:
            print("    Could not fetch — skipping")
            continue

        count = parse_title_xml(title_num, title_name, xml_text)
        total += count
        print(f"    {count} sections inserted (total: {total})")

        completed.add(title_num)
        cur.execute(
            "INSERT OR REPLACE INTO ingest_state (key, value) VALUES ('completed_titles', ?)",
            (json.dumps(sorted(completed)),),
        )
        con.commit()

        time.sleep(DELAY)

    con.close()
    print(f"\nDone. Total sections inserted: {total} into {DB_PATH}")


if __name__ == "__main__":
    main()