#!/usr/bin/env python3
"""Build an FTS5 index over products.product_name + ingredients_text + categories_en.

Creates `products_fts` as an external-content FTS5 table backed by the
`products` table. `content_rowid='id'` works here because `id` is an
INTEGER PRIMARY KEY (a rowid alias). Tokenizer is `porter unicode61` for
stemming + diacritic folding (matches other sources in this repo).

Re-runnable: drops the virtual table and rebuilds from scratch. Run after
every refresh of openfoodfacts.db.
"""

import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "openfoodfacts" / "openfoodfacts.db"


def main() -> int:
    if not DB_PATH.is_file():
        print(f"missing: {DB_PATH}", file=sys.stderr)
        return 1

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    t0 = time.time()
    cur.execute("DROP TABLE IF EXISTS products_fts")
    cur.execute("""
        CREATE VIRTUAL TABLE products_fts USING fts5(
            product_name,
            ingredients_text,
            categories_en,
            content='products',
            content_rowid='id',
            tokenize='porter unicode61'
        )
    """)
    cur.execute(
        "INSERT INTO products_fts(rowid, product_name, ingredients_text, categories_en) "
        "SELECT id, product_name, ingredients_text, categories_en FROM products"
    )
    con.commit()

    indexed = cur.execute("SELECT COUNT(*) FROM products_fts").fetchone()[0]
    db_size = DB_PATH.stat().st_size

    con.close()
    elapsed = time.time() - t0
    print(
        f"Done in {elapsed:.1f}s. "
        f"Indexed {indexed} products. "
        f"DB file is now {db_size / (1024**2):.1f} MB."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
