#!/usr/bin/env python3
"""
Open Food Facts Downloader
Downloads the full JSONL export and extracts 55 selected fields into SQLite.
Source: https://world.openfoodfacts.org/data
Requires: requests
"""

import argparse
import gzip
import json
import os
import sqlite3
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

JSONL_URL = "https://static.openfoodfacts.org/data/openfoodfacts-products.jsonl.gz"
# static.openfoodfacts.org blocks the default python-requests UA with a 403;
# a descriptive UA is also courteous per their bulk-data usage guidelines.
_EMAIL = os.environ.get("DATASETS_EMAIL")
USER_AGENT = f"openfoodfacts-downloader/1.0 (personal research; {_EMAIL})"

# 55 selected fields
FIELDS = [
    # Identity
    "code", "product_name", "generic_name", "brands", "brand_owner",
    "categories", "categories_en",
    # Origin & availability
    "countries", "countries_en", "origins", "manufacturing_places",
    "stores",
    # Ingredients & composition
    "ingredients_text", "ingredients_analysis_tags", "allergens",
    "allergens_en", "traces", "traces_en", "additives_n", "additives_tags",
    # Health scores
    "nutriscore_grade", "nutriscore_score", "nova_group",
    "ecoscore_grade", "ecoscore_score",
    # Macronutrients
    "energy-kcal_100g", "energy-kj_100g", "fat_100g", "saturated-fat_100g",
    "carbohydrates_100g", "sugars_100g", "fiber_100g", "proteins_100g",
    "salt_100g", "sodium_100g",
    # Micronutrients
    "calcium_100g", "iron_100g", "vitamin-c_100g", "vitamin-a_100g",
    "vitamin-d_100g", "potassium_100g", "magnesium_100g", "zinc_100g",
    "cholesterol_100g", "trans-fat_100g",
    # Packaging & labels
    "packaging", "packaging_tags", "labels", "labels_en", "serving_size",
    # Extended
    "quantity", "product_quantity", "packaging_recycling_tags",
    "expiration_date", "purchase_places",
]


def safe_col(name: str) -> str:
    """Convert a field name to a SQLite-safe column name (hyphens → underscores)."""
    return name.replace("-", "_")


SAFE_FIELDS = [safe_col(f) for f in FIELDS]


def create_schema(con: sqlite3.Connection) -> None:
    """Create the products table, indexes, and ingest_state table if they don't exist."""
    col_defs = ", ".join(f'"{c}" TEXT' for c in SAFE_FIELDS)
    con.execute(
        f"CREATE TABLE IF NOT EXISTS products ("
        f"  id INTEGER PRIMARY KEY AUTOINCREMENT, "
        f"  {col_defs}, "
        f"  UNIQUE (code)"
        f")"
    )
    con.execute('CREATE INDEX IF NOT EXISTS idx_product_name ON products ("product_name")')
    con.execute(
        "CREATE TABLE IF NOT EXISTS ingest_state (key TEXT PRIMARY KEY, value TEXT)"
    )
    con.commit()


def download(url: str, dest: Path) -> None:
    """Download url to dest atomically via a .tmp sibling; renames only on success."""
    tmp = dest.with_name(dest.name + ".tmp")
    print("Downloading Open Food Facts JSONL (~5 GB compressed)...")
    try:
        headers = {"User-Agent": USER_AGENT}
        with requests.get(url, stream=True, timeout=600, headers=headers) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                downloaded = 0
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if downloaded % (100 * 1 << 20) == 0:
                        print(f"  {downloaded // (1 << 20)} MB downloaded...")
        tmp.rename(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    print("Download complete.")


def extract_values(product: dict) -> list[str]:
    """Return the 55 tracked field values for one product dict, all as strings."""
    nutriments = product.get("nutriments", {})
    values = []
    for field in FIELDS:
        # Nutriment fields are nested under the "nutriments" key
        val = nutriments.get(field) if field.endswith("_100g") else product.get(field)
        if isinstance(val, list):
            val = ", ".join(str(v) for v in val)
        elif isinstance(val, dict):
            val = json.dumps(val)
        elif val is None:
            val = ""
        else:
            val = str(val)
        values.append(val)
    return values


def parse_and_insert(
    gz_path: Path, con: sqlite3.Connection, limit: int | None
) -> tuple[int, int]:
    """
    Stream gz_path line-by-line and insert products into the DB.

    Resumes from the last committed line recorded in ingest_state so an
    interrupted run can continue without reprocessing from the top.
    Products whose code already exists are silently skipped (INSERT OR IGNORE).
    Returns (total_inserted, total_skipped).
    """
    cur = con.cursor()

    row = cur.execute(
        "SELECT value FROM ingest_state WHERE key = 'lines_processed'"
    ).fetchone()
    resume_from = int(row[0]) if row else 0
    if resume_from:
        print(f"Resuming from line {resume_from:,}...")

    cols = ", ".join(f'"{c}"' for c in SAFE_FIELDS)
    placeholders = ", ".join("?" for _ in SAFE_FIELDS)
    insert_sql = f"INSERT OR IGNORE INTO products ({cols}) VALUES ({placeholders})"

    total = 0
    skipped = 0
    line_num = 0

    with gzip.open(gz_path, "rt", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line_num += 1

            # Fast-forward past lines already committed in a previous run
            if line_num <= resume_from:
                continue

            line = raw.strip()
            if not line:
                continue

            try:
                product = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            if not product.get("code") and not product.get("product_name"):
                skipped += 1
                continue

            cur.execute(insert_sql, extract_values(product))
            total += 1

            if limit is not None and total >= limit:
                break

            if total % 10_000 == 0:
                cur.execute(
                    "INSERT OR REPLACE INTO ingest_state VALUES ('lines_processed', ?)",
                    (str(line_num),),
                )
                con.commit()
                print(f"  {total:,} products inserted ({skipped:,} skipped)...")

    cur.execute(
        "INSERT OR REPLACE INTO ingest_state VALUES ('lines_processed', ?)",
        (str(line_num),),
    )
    con.commit()
    return total, skipped


def main() -> None:
    """Parse args, download if needed, then parse and insert into SQLite."""
    parser = argparse.ArgumentParser(description="Download Open Food Facts into SQLite.")
    parser.add_argument(
        "--db", default="data/openfoodfacts/openfoodfacts.db",
        help="Output SQLite database path."
    )
    parser.add_argument(
        "--download-dir", default="data/openfoodfacts/raw",
        help="Directory for the downloaded .jsonl.gz file."
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Stop after inserting this many products (useful for testing)."
    )
    args = parser.parse_args()

    if not _EMAIL:
        parser.error("DATASETS_EMAIL env var is required for the User-Agent contact address.")

    db_path = Path(args.db)
    download_dir = Path(args.download_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    download_dir.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(db_path)
    create_schema(con)

    gz_path = download_dir / "openfoodfacts-products.jsonl.gz"
    if not gz_path.exists():
        download(JSONL_URL, gz_path)
    else:
        print("Archive already present, skipping download.")

    print("Parsing and inserting into SQLite...")
    total, skipped = parse_and_insert(gz_path, con, args.limit)
    con.close()

    print(f"\nDone. {total:,} products inserted, {skipped:,} skipped → {db_path}")


if __name__ == "__main__":
    main()
