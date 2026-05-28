#!/usr/bin/env python3
"""Ingest PDFs from data/pdfs/incoming/ into data/pdfs/pdfs.db.

Two tables:
- documents: one row per PDF (doc_id = filename stem) with PDF metadata.
- pages: one row per page, text extracted via pdfplumber. Empty pages are
  stored as empty strings so page_no stays aligned with the source PDF.

Re-runnable: PDFs already in the documents table are skipped unless --force
is passed. PDFs live in the drop folder; this script does not move them.
"""

import argparse
import hashlib
import sqlite3
import sys
import time
from pathlib import Path

import pdfplumber

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PDFS_ROOT = REPO_ROOT / "data" / "pdfs"
INCOMING_DIR = PDFS_ROOT / "incoming"
DB_PATH = PDFS_ROOT / "pdfs.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    title TEXT,
    author TEXT,
    subject TEXT,
    keywords TEXT,
    creator TEXT,
    producer TEXT,
    creation_date TEXT,
    mod_date TEXT,
    num_pages INTEGER,
    file_size INTEGER,
    sha256 TEXT,
    source_path TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    doc_id TEXT NOT NULL,
    page_no INTEGER NOT NULL,
    text TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    PRIMARY KEY (doc_id, page_no),
    FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
);

CREATE INDEX IF NOT EXISTS idx_documents_title ON documents(title);
CREATE INDEX IF NOT EXISTS idx_documents_author ON documents(author);
"""


def sha256_of(path: Path) -> str:
    """Return hex SHA-256 of the file's bytes (streamed in 1 MB chunks)."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _meta_str(meta: dict, key: str) -> str | None:
    """Pull a string field from pdfplumber's metadata dict, coercing safely."""
    val = meta.get(key)
    if val is None:
        return None
    if isinstance(val, str):
        return val.strip() or None
    return str(val)


def extract_pdf(path: Path) -> tuple[dict, list[tuple[int, str]]]:
    """Return (document_metadata, [(page_no, text), ...]) for a single PDF.

    page_no is 1-based to match how humans (and the PDF UI) count pages.
    Empty pages are kept as empty strings.
    """
    with pdfplumber.open(path) as pdf:
        meta = pdf.metadata or {}
        doc_meta = {
            "title": _meta_str(meta, "Title"),
            "author": _meta_str(meta, "Author"),
            "subject": _meta_str(meta, "Subject"),
            "keywords": _meta_str(meta, "Keywords"),
            "creator": _meta_str(meta, "Creator"),
            "producer": _meta_str(meta, "Producer"),
            "creation_date": _meta_str(meta, "CreationDate"),
            "mod_date": _meta_str(meta, "ModDate"),
            "num_pages": len(pdf.pages),
        }
        pages: list[tuple[int, str]] = []
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages.append((i, text))
    return doc_meta, pages


def existing_doc_ids(con: sqlite3.Connection) -> set[str]:
    cur = con.execute("SELECT doc_id FROM documents")
    return {row[0] for row in cur.fetchall()}


def ingest_one(
    con: sqlite3.Connection,
    pdf_path: Path,
    incoming_dir: Path,
) -> tuple[int, int]:
    """Ingest a single PDF. Returns (num_pages, total_chars)."""
    doc_id = pdf_path.stem
    doc_meta, pages = extract_pdf(pdf_path)
    file_size = pdf_path.stat().st_size
    digest = sha256_of(pdf_path)
    source_path = str(pdf_path.relative_to(incoming_dir))
    ingested_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Remove any prior page rows for this doc (e.g. --force re-ingest) so
    # we don't leave stale pages behind if the page count shrank.
    con.execute("DELETE FROM pages WHERE doc_id = ?", (doc_id,))
    con.execute(
        """
        INSERT OR REPLACE INTO documents (
            doc_id, title, author, subject, keywords, creator, producer,
            creation_date, mod_date, num_pages, file_size, sha256,
            source_path, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            doc_meta["title"],
            doc_meta["author"],
            doc_meta["subject"],
            doc_meta["keywords"],
            doc_meta["creator"],
            doc_meta["producer"],
            doc_meta["creation_date"],
            doc_meta["mod_date"],
            doc_meta["num_pages"],
            file_size,
            digest,
            source_path,
            ingested_at,
        ),
    )
    con.executemany(
        "INSERT INTO pages (doc_id, page_no, text, char_count) VALUES (?, ?, ?, ?)",
        [(doc_id, page_no, text, len(text)) for page_no, text in pages],
    )
    con.commit()
    total_chars = sum(len(t) for _, t in pages)
    return len(pages), total_chars


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help=f"SQLite DB path (default: {DB_PATH})",
    )
    parser.add_argument(
        "--incoming",
        type=Path,
        default=INCOMING_DIR,
        help=f"Drop folder to scan (default: {INCOMING_DIR})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-ingest PDFs whose doc_id already exists.",
    )
    args = parser.parse_args()

    incoming: Path = args.incoming
    if not incoming.is_dir():
        print(
            f"missing drop folder: {incoming}\n"
            f"create it and drop PDFs in, e.g.: mkdir -p {incoming}",
            file=sys.stderr,
        )
        return 1

    args.db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(args.db)
    con.executescript(SCHEMA)

    already = existing_doc_ids(con)
    pdfs = sorted(p for p in incoming.rglob("*.pdf") if p.is_file())
    if not pdfs:
        print(f"no PDFs found under {incoming}")
        con.close()
        return 0

    t0 = time.time()
    ingested = skipped = failed = 0
    for pdf_path in pdfs:
        doc_id = pdf_path.stem
        if doc_id in already and not args.force:
            skipped += 1
            continue
        try:
            num_pages, total_chars = ingest_one(con, pdf_path, incoming)
        except Exception as exc:  # broad: PDF parsing surfaces many error types
            failed += 1
            print(f"  FAIL {pdf_path.name}: {exc}", file=sys.stderr)
            continue
        ingested += 1
        print(
            f"  {pdf_path.name}: {num_pages} pages, {total_chars} chars",
            flush=True,
        )

    con.close()
    elapsed = time.time() - t0
    print(
        f"Done in {elapsed:.1f}s. "
        f"ingested={ingested} skipped={skipped} failed={failed}"
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
