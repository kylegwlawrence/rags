#!/usr/bin/env python3
"""Stream a simplewiki ``.xml.bz2`` dump into ``data/simplewiki/simplewiki.db``.

Writes to ``simplewiki.db.tmp`` first and atomically renames on success so an
interrupted parse can never corrupt the destination file. Schema is identical
to the existing local_wikipedia parse output (articles + articles_fts trigram
index over title + parse_metadata + articles_archive + db_metadata) so a DB
parsed by this script is interchangeable with one parsed by the upstream
project — the API code reads the same columns.

By default filters to namespace 0 (main article namespace). Pass
``--all-namespaces`` to include Talk:, User:, Wikipedia:, File:, etc.

Source-specific: ported from local_wikipedia/parse/ but trimmed to a single
wiki. The full upstream pipeline supports incremental refresh against an
``articles_archive`` revision-history table; that's out of scope for this
hobbyist API — re-parse from scratch by deleting the DB and running again.
"""

import argparse
import bz2
import os
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DUMPS_DIR = REPO_ROOT / "data" / "simplewiki" / "dumps"
DB_PATH = REPO_ROOT / "data" / "simplewiki" / "simplewiki.db"

# MediaWiki XML export namespace. Bumped occasionally upstream — if Wikimedia
# moves to 0.12 this constant + PAGE_TAG need to follow. The 0.11 schema has
# been stable for years.
MW_NS = "http://www.mediawiki.org/xml/export-0.11/"
NS = {"mw": MW_NS}
PAGE_TAG = f"{{{MW_NS}}}page"

BATCH_SIZE = 1000
DEFAULT_NAMESPACE = 0  # main article namespace


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the simplewiki schema. Matches the local_wikipedia parse layout."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            page_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            namespace INTEGER NOT NULL DEFAULT 0,
            revision_id INTEGER NOT NULL,
            parent_revision_id INTEGER,
            timestamp TEXT NOT NULL,
            contributor_username TEXT,
            contributor_id INTEGER,
            comment TEXT,
            text_bytes INTEGER,
            text_content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_title ON articles(title)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_namespace ON articles(namespace)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_timestamp ON articles(timestamp)")
    cur.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
            title,
            content=articles,
            content_rowid=page_id,
            tokenize='trigram'
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS db_metadata (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS parse_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wiki TEXT NOT NULL,
            source_file TEXT NOT NULL,
            total_pages INTEGER NOT NULL,
            articles_count INTEGER NOT NULL,
            parse_started_at TEXT NOT NULL,
            parse_completed_at TEXT NOT NULL,
            parse_duration_seconds REAL NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS articles_archive (
            archive_id           INTEGER PRIMARY KEY AUTOINCREMENT,
            archived_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            page_id              INTEGER NOT NULL,
            title                TEXT    NOT NULL,
            namespace            INTEGER NOT NULL DEFAULT 0,
            revision_id          INTEGER NOT NULL,
            parent_revision_id   INTEGER,
            timestamp            TEXT    NOT NULL,
            contributor_username TEXT,
            contributor_id       INTEGER,
            comment              TEXT,
            text_bytes           INTEGER,
            text_content         TEXT    NOT NULL,
            created_at           TEXT    NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_archive_page_id ON articles_archive(page_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_archive_archived_at ON articles_archive(archived_at)")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA page_size=4096")
    cur.execute("PRAGMA synchronous=NORMAL")
    conn.commit()


def _get_text(elem: ET.Element, tag: str) -> str | None:
    child = elem.find(tag, NS)
    return child.text if child is not None else None


def parse_page_element(page_elem: ET.Element) -> dict[str, Any] | None:
    """Extract one ``<page>``'s fields into a dict suitable for INSERT.

    Returns None when required fields are missing — the parse loop skips
    those rather than insert NULLs that would later fail NOT NULL checks.
    """
    try:
        title = _get_text(page_elem, "mw:title")
        if not title:
            return None
        page_id = _get_text(page_elem, "mw:id")
        namespace = _get_text(page_elem, "mw:ns")
        if page_id is None or namespace is None:
            return None
        revision = page_elem.find("mw:revision", NS)
        if revision is None:
            return None
        revision_id = _get_text(revision, "mw:id")
        if not revision_id:
            return None
        parent_revision_id = _get_text(revision, "mw:parentid")
        timestamp = _get_text(revision, "mw:timestamp")
        comment = _get_text(revision, "mw:comment")

        contributor = revision.find("mw:contributor", NS)
        contributor_username = None
        contributor_id: int | None = None
        if contributor is not None:
            contributor_username = _get_text(contributor, "mw:username")
            contrib_id = _get_text(contributor, "mw:id")
            contributor_id = int(contrib_id) if contrib_id else None

        text_elem = revision.find("mw:text", NS)
        if text_elem is None:
            return None
        text_content = text_elem.text or ""
        text_bytes_attr = text_elem.get("bytes")

        return {
            "page_id": int(page_id),
            "title": title,
            "namespace": int(namespace),
            "revision_id": int(revision_id),
            "parent_revision_id": int(parent_revision_id) if parent_revision_id else None,
            "timestamp": timestamp or "",
            "contributor_username": contributor_username,
            "contributor_id": contributor_id,
            "comment": comment,
            "text_bytes": int(text_bytes_attr) if text_bytes_attr else len(text_content),
            "text_content": text_content,
        }
    except (ValueError, AttributeError):
        return None


def _batch_insert(conn: sqlite3.Connection, batch: list[dict[str, Any]]) -> None:
    """Insert / replace a batch of articles."""
    if not batch:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO articles (
            page_id, title, namespace, revision_id, parent_revision_id,
            timestamp, contributor_username, contributor_id, comment,
            text_bytes, text_content
        ) VALUES (
            :page_id, :title, :namespace, :revision_id, :parent_revision_id,
            :timestamp, :contributor_username, :contributor_id, :comment,
            :text_bytes, :text_content
        )
        """,
        batch,
    )


def parse_dump(
    dump_path: Path,
    db_path: Path,
    namespace_filter: int | None = DEFAULT_NAMESPACE,
) -> tuple[int, int]:
    """Parse ``dump_path`` into ``db_path``. Returns ``(total_pages, articles_inserted)``.

    Args:
        dump_path: Path to the ``.xml.bz2`` Wikimedia dump.
        db_path: Destination SQLite path. Final write is atomic.
        namespace_filter: Only insert pages whose ns matches this value. Pass
            ``None`` to insert every namespace.
    """
    if not dump_path.exists():
        raise RuntimeError(f"Dump file not found: {dump_path}")

    tmp_db = db_path.with_suffix(".db.tmp")
    if tmp_db.exists():
        tmp_db.unlink()
    completed = False
    conn: sqlite3.Connection | None = None

    try:
        start = time.time()
        conn = sqlite3.connect(tmp_db)
        create_schema(conn)

        batch: list[dict[str, Any]] = []
        total_pages = 0
        inserted = 0
        truncated = False

        print(f"Parsing {dump_path.name} ...", file=sys.stderr, flush=True)
        with bz2.open(dump_path, "rb") as f:
            context = ET.iterparse(f, events=("end",))
            try:
                for _ev, elem in context:
                    if elem.tag != PAGE_TAG:
                        continue
                    total_pages += 1
                    page = parse_page_element(elem)
                    elem.clear()
                    if not page:
                        continue
                    if namespace_filter is not None and page["namespace"] != namespace_filter:
                        continue
                    batch.append(page)
                    inserted += 1
                    if len(batch) >= BATCH_SIZE:
                        _batch_insert(conn, batch)
                        conn.commit()
                        batch.clear()
                    if total_pages % 10_000 == 0:
                        print(
                            f"  {total_pages:,} pages seen / {inserted:,} inserted",
                            file=sys.stderr,
                            flush=True,
                        )
            except (ET.ParseError, EOFError):
                truncated = True

        if truncated:
            print(
                f"Warning: dump truncated — saving {inserted:,} articles parsed before EOF",
                file=sys.stderr,
                flush=True,
            )
        if batch:
            _batch_insert(conn, batch)
            conn.commit()

        print("Building articles_fts index...", file=sys.stderr, flush=True)
        conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
        conn.commit()

        conn.execute(
            "INSERT OR REPLACE INTO db_metadata (key, value) VALUES ('article_count', ?)",
            (str(inserted),),
        )
        end = time.time()
        conn.execute(
            """
            INSERT INTO parse_metadata (
                wiki, source_file, total_pages, articles_count,
                parse_started_at, parse_completed_at, parse_duration_seconds
            ) VALUES (?, ?, ?, ?, datetime(?, 'unixepoch'), datetime(?, 'unixepoch'), ?)
            """,
            ("simplewiki", dump_path.name, total_pages, inserted,
             start, end, end - start),
        )
        conn.commit()
        conn.close()
        conn = None

        os.replace(tmp_db, db_path)
        completed = True
        return total_pages, inserted

    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        if not completed:
            tmp_db.unlink(missing_ok=True)


def _find_latest_dump() -> Path | None:
    """Return the newest ``*-pages-articles-multistream.xml.bz2`` under DUMPS_DIR, or None."""
    candidates = sorted(
        DUMPS_DIR.glob("simplewiki-*-pages-articles-multistream.xml.bz2"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dump",
        type=Path,
        default=None,
        help="Path to the .xml.bz2 dump. Defaults to the newest matching file in data/simplewiki/dumps/.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help=f"Destination SQLite path (default: {DB_PATH}).",
    )
    parser.add_argument(
        "--all-namespaces",
        action="store_true",
        help="Insert every page namespace (default: only namespace 0, main articles).",
    )
    args = parser.parse_args(argv)

    dump = args.dump or _find_latest_dump()
    if dump is None:
        print(
            f"No dump found. Run simplewiki_download.py or pass --dump.\n"
            f"(searched {DUMPS_DIR})",
            file=sys.stderr,
        )
        return 1

    ns_filter = None if args.all_namespaces else DEFAULT_NAMESPACE
    args.db.parent.mkdir(parents=True, exist_ok=True)
    total_pages, inserted = parse_dump(dump, args.db, namespace_filter=ns_filter)
    print(f"Parsed {total_pages:,} pages, inserted {inserted:,} articles into {args.db}")
    print(f"(Restart uvicorn so api.db.simplewiki() reopens the new file.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
