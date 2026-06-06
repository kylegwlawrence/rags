#!/usr/bin/env python3
"""Parse the Justice Canada laws-lois-xml corpus into SQLite.

Reads the XML files produced by download.py and populates two tables:
  - acts         (one row per consolidated Act)
  - regulations  (one row per consolidated Regulation)

Re-runnable: INSERT OR REPLACE makes it idempotent.
Run after download.py; then run the FTS/RAG indexers.
"""

import argparse
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "justice_canada" / "justice_canada.db"
DEFAULT_CORPUS = REPO_ROOT / "data" / "justice_canada" / "laws-lois-xml"

LIMS = "http://justice.gc.ca/lims"

SCHEMA = """
CREATE TABLE IF NOT EXISTS acts (
    chapter_number     TEXT PRIMARY KEY,
    short_title        TEXT,
    long_title         TEXT,
    running_head       TEXT,
    bill_origin        TEXT,
    bill_type          TEXT,
    in_force           TEXT,
    inforce_start_date TEXT,
    last_amended_date  TEXT,
    current_date       TEXT,
    filename           TEXT
);

CREATE TABLE IF NOT EXISTS regulations (
    instrument_number  TEXT PRIMARY KEY,
    short_title        TEXT,
    long_title         TEXT,
    regulation_type    TEXT,
    enabling_authority TEXT,
    inforce_start_date TEXT,
    last_amended_date  TEXT,
    current_date       TEXT,
    filename           TEXT
);

CREATE INDEX IF NOT EXISTS idx_acts_in_force ON acts(in_force);
CREATE INDEX IF NOT EXISTS idx_regs_type ON regulations(regulation_type);
"""


def lims_attr(element: ET.Element, name: str) -> str:
    """Return a lims-namespace attribute value, or empty string."""
    return element.get(f"{{{LIMS}}}{name}", "") or ""


def elem_text(element: ET.Element, path: str) -> str:
    """Return stripped text of the first match for path, or empty string."""
    found = element.find(path)
    return (found.text or "").strip() if found is not None else ""


def parse_act(xml_file: Path) -> tuple | None:
    """Parse one Act XML file; return a row tuple or None on failure."""
    try:
        root = ET.parse(xml_file).getroot()
    except ET.ParseError as exc:
        print(f"  Warning: {xml_file.name}: {exc}")
        return None

    ident = root.find("Identification")
    if ident is None:
        print(f"  Warning: {xml_file.name}: no Identification element")
        return None

    # Fall back to lims:id when the chapter number is absent.
    chapter_number = elem_text(ident, "Chapter/ConsolidatedNumber") or lims_attr(root, "id")
    return (
        chapter_number,
        elem_text(ident, "ShortTitle"),
        elem_text(ident, "LongTitle"),
        elem_text(ident, "RunningHead"),
        root.get("bill-origin", ""),
        root.get("bill-type", ""),
        root.get("in-force", ""),
        lims_attr(root, "inforce-start-date"),
        lims_attr(root, "lastAmendedDate"),
        lims_attr(root, "current-date"),
        xml_file.name,
    )


def parse_regulation(xml_file: Path) -> tuple | None:
    """Parse one Regulation XML file; return a row tuple or None on failure."""
    try:
        root = ET.parse(xml_file).getroot()
    except ET.ParseError as exc:
        print(f"  Warning: {xml_file.name}: {exc}")
        return None

    ident = root.find("Identification")
    if ident is None:
        print(f"  Warning: {xml_file.name}: no Identification element")
        return None

    # Fall back to lims:id when the instrument number is absent.
    instrument_number = elem_text(ident, "InstrumentNumber") or lims_attr(root, "id")

    # EnablingAuthority may reference multiple acts via XRefExternal elements.
    enabling_el = ident.find("EnablingAuthority")
    enabling_authority = ""
    if enabling_el is not None:
        parts = [x.text.strip() for x in enabling_el.iter("XRefExternal") if x.text]
        if not parts and enabling_el.text:
            parts = [enabling_el.text.strip()]
        enabling_authority = "; ".join(parts)

    return (
        instrument_number,
        elem_text(ident, "ShortTitle"),
        elem_text(ident, "LongTitle"),
        root.get("regulation-type", ""),
        enabling_authority,
        lims_attr(root, "inforce-start-date"),
        lims_attr(root, "lastAmendedDate"),
        lims_attr(root, "current-date"),
        xml_file.name,
    )


def ingest_acts(cur: sqlite3.Cursor, acts_dir: Path) -> int:
    """Parse and insert all act XML files; return count inserted."""
    files = sorted(acts_dir.glob("*.xml"))
    print(f"Parsing {len(files)} act files ...")
    count = 0
    for xml_file in files:
        row = parse_act(xml_file)
        if row is None:
            continue
        cur.execute(
            """
            INSERT OR REPLACE INTO acts
              (chapter_number, short_title, long_title, running_head,
               bill_origin, bill_type, in_force,
               inforce_start_date, last_amended_date, current_date, filename)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        count += 1
    return count


def ingest_regulations(cur: sqlite3.Cursor, regs_dir: Path) -> int:
    """Parse and insert all regulation XML files; return count inserted."""
    files = sorted(regs_dir.glob("*.xml"))
    print(f"Parsing {len(files)} regulation files ...")
    count = 0
    for xml_file in files:
        row = parse_regulation(xml_file)
        if row is None:
            continue
        cur.execute(
            """
            INSERT OR REPLACE INTO regulations
              (instrument_number, short_title, long_title, regulation_type,
               enabling_authority, inforce_start_date, last_amended_date,
               current_date, filename)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse the Justice Canada XML corpus into SQLite."
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help=f"SQLite database path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--corpus-dir",
        default=str(DEFAULT_CORPUS),
        help=f"Root of the downloaded laws-lois-xml corpus (default: {DEFAULT_CORPUS})",
    )
    parser.add_argument(
        "--language",
        choices=["en", "fr", "both"],
        default="en",
        help="Language(s) to parse: en, fr, or both (default: en)",
    )
    parser.add_argument(
        "--type",
        dest="doc_type",
        choices=["acts", "regulations", "both"],
        default="both",
        help="Document type(s) to parse: acts, regulations, or both (default: both)",
    )
    args = parser.parse_args()

    corpus = Path(args.corpus_dir)
    if not corpus.is_dir():
        sys.exit(f"Corpus directory not found: {corpus}\nRun download.py first.")

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.executescript(SCHEMA)
    con.commit()

    lang_codes = {"en": ["eng"], "fr": ["fra"], "both": ["eng", "fra"]}
    total_acts = total_regs = 0

    for lang in lang_codes[args.language]:
        lang_dir = corpus / lang
        if not lang_dir.is_dir():
            print(f"Warning: {lang_dir} not found — run download.py with matching --language flag")
            continue

        if args.doc_type in ("acts", "both"):
            acts_dir = lang_dir / "acts"
            if acts_dir.is_dir():
                total_acts += ingest_acts(cur, acts_dir)
            else:
                print(f"Warning: {acts_dir} not found.")

        if args.doc_type in ("regulations", "both"):
            regs_dir = lang_dir / "regulations"
            if regs_dir.is_dir():
                total_regs += ingest_regulations(cur, regs_dir)
            else:
                print(f"Warning: {regs_dir} not found.")

    con.commit()
    con.close()

    print("\nDone.")
    if total_acts:
        print(f"  Acts inserted:        {total_acts:,}")
    if total_regs:
        print(f"  Regulations inserted: {total_regs:,}")
    print(f"  Database:             {db_path.resolve()}")


if __name__ == "__main__":
    main()
