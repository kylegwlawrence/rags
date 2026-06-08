#!/usr/bin/env python3
"""Build a ``page_categories`` table from the inline ``[[Category:...]]`` links
in each article's wikitext.

The wiki DBs store only raw wikitext (no structured category data), so category
membership lives inside ``articles.text_content`` as link markup. This script
scans every namespace-0 article, extracts its category links, normalizes the
names, and writes one row per (page, category) pair into ``page_categories`` —
giving a real, indexable mapping you can join and group on.

Normalization (MediaWiki treats these as the same category):
  - drop the sort-key suffix after the pipe   ``[[Category:Months|*04]]`` -> Months
  - underscores become spaces                 ``basic_English``           -> basic English
  - collapse/trim surrounding whitespace
  - uppercase the first letter                ``art``                     -> Art

Reusable across wiki DBs with the same schema (simplewiki, enwiki): pass --db.
Idempotent — drops and rebuilds ``page_categories`` on each run. Restart the
API after running so the cached connection sees the new table.
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag.wikitext import normalize_category  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "simplewiki" / "simplewiki.db"

ARTICLE_NAMESPACE = 0
BATCH_SIZE = 1000

# Match [[Category:Name]] or [[Category:Name|sortkey]]. The namespace prefix is
# case-insensitive in MediaWiki; the name runs up to the first '|' or ']]'.
CATEGORY_RE = re.compile(r"\[\[\s*Category\s*:\s*([^\]|]+?)\s*(?:\|[^\]]*)?\]\]", re.IGNORECASE)


def extract_categories(text: str) -> set[str]:
    """Return the deduplicated, normalized categories referenced in wikitext."""
    categories = set()
    for raw in CATEGORY_RE.findall(text):
        name = normalize_category(raw)
        if name:
            categories.add(name)
    return categories


def create_schema(conn: sqlite3.Connection) -> None:
    """Drop and recreate page_categories plus its lookup index."""
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS page_categories")
    cur.execute("""
        CREATE TABLE page_categories (
            page_id  INTEGER NOT NULL,
            category TEXT    NOT NULL,
            PRIMARY KEY (page_id, category)
        )
    """)
    # PK already indexes page_id; add the reverse index for category lookups.
    cur.execute("CREATE INDEX idx_page_categories_category ON page_categories(category)")
    conn.commit()


def build(db_path: Path) -> None:
    if not db_path.exists():
        sys.exit(f"DB not found: {db_path}")

    conn = sqlite3.connect(db_path)
    create_schema(conn)

    total_articles = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE namespace = ?", (ARTICLE_NAMESPACE,)
    ).fetchone()[0]

    read = conn.cursor()
    read.execute(
        "SELECT page_id, text_content FROM articles WHERE namespace = ?",
        (ARTICLE_NAMESPACE,),
    )

    write = conn.cursor()
    rows: list[tuple[int, str]] = []
    seen_articles = 0
    with_cats = 0
    pair_count = 0

    for page_id, text in read:
        seen_articles += 1
        categories = extract_categories(text or "")
        if categories:
            with_cats += 1
            rows.extend((page_id, category) for category in categories)
            pair_count += len(categories)
        if len(rows) >= BATCH_SIZE:
            write.executemany("INSERT INTO page_categories VALUES (?, ?)", rows)
            rows.clear()
        if seen_articles % 50000 == 0:
            print(f"  {seen_articles:,}/{total_articles:,} articles scanned...")

    if rows:
        write.executemany("INSERT INTO page_categories VALUES (?, ?)", rows)
    conn.commit()

    distinct = conn.execute("SELECT COUNT(DISTINCT category) FROM page_categories").fetchone()[0]
    conn.close()

    print(f"\nDone. Scanned {seen_articles:,} articles ({with_cats:,} had categories).")
    print(f"  {pair_count:,} page-category rows, {distinct:,} distinct categories.")
    print(f"  Written to {db_path}")
    print("Restart the API so the cached connection sees the new table.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help=f"Wiki DB to index (default: {DB_PATH})",
    )
    args = parser.parse_args()
    build(args.db)


if __name__ == "__main__":
    main()
