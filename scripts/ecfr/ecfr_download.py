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
DELAY = 0.5                         # Seconds between API calls
MAILTO = os.environ.get("DATASETS_EMAIL")
if not MAILTO:
    raise SystemExit(
        "DATASETS_EMAIL env var is not set; required for the eCFR API "
        "User-Agent. Set it and re-run."
    )

API_BASE = "https://www.ecfr.gov/api"

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Setup SQLite
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
session.headers.update({"User-Agent": f"ecfr-fetcher mailto:{MAILTO}"})

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

            # Gather all paragraph text within the section
            paragraphs = []
            for p in div.iter("P"):
                text = "".join(p.itertext()).strip()
                if text:
                    paragraphs.append(text)
            content = "\n".join(paragraphs)

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

# --- Main ---
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