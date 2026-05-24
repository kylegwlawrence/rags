#!/usr/bin/env python3
"""Build an FTS5 index over products.product_name + ingredients_text + categories_en.

`products.id` is an INTEGER PK alias for the rowid, so the FTS table uses it
as its content_rowid directly.

Re-runnable: drops `products_fts` and rebuilds from scratch.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag.fts import run_fts_indexer  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "openfoodfacts" / "openfoodfacts.db"


if __name__ == "__main__":
    sys.exit(run_fts_indexer(
        db_path=DB_PATH,
        virtual_table="products_fts",
        content_table="products",
        columns=("product_name", "ingredients_text", "categories_en"),
        content_rowid="id",
        row_label="products",
    ))
