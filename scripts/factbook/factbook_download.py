#!/usr/bin/env python3
"""Clone github.com/factbook/factbook.json into a temp dir, walk per-region
subdirectories, and insert each country as one row (with the full JSON blob
in a `data` column) into `data/factbook/factbook.db`.

Re-runnable via `INSERT OR REPLACE`. Temp clone is removed on success.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "factbook" / "factbook.db"
TMP_DIR = "/tmp/factbook_json"


def main() -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Clone the factbook JSON repo
    if os.path.exists(TMP_DIR):
        shutil.rmtree(TMP_DIR)
    subprocess.run(
        ["git", "clone", "https://github.com/factbook/factbook.json.git", TMP_DIR],
        check=True,
    )

    # Setup SQLite
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS countries (
            id TEXT PRIMARY KEY,
            name TEXT,
            region TEXT,
            data JSON
        )
    """)
    con.commit()

    # Regions in the factbook repo
    regions = [
        "africa", "antarctica", "australia-oceania",
        "central-america-n-caribbean", "central-asia",
        "east-n-southeast-asia", "europe", "middle-east",
        "north-america", "oceans", "south-america", "south-asia",
        "world",
    ]

    total = 0
    for region in regions:
        region_dir = Path(TMP_DIR) / region
        if not region_dir.exists():
            print(f"Skipping {region} — directory not found")
            continue
        for json_file in region_dir.glob("*.json"):
            country_code = json_file.stem
            with open(json_file, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    print(f"Skipping {json_file} — invalid JSON")
                    continue

            # Try to extract country name
            try:
                name = data["Government"]["Country name"]["conventional short form"]["text"]
            except (KeyError, TypeError):
                try:
                    name = data["Government"]["Country name"]["conventional long form"]["text"]
                except (KeyError, TypeError):
                    name = country_code

            cur.execute("""
                INSERT OR REPLACE INTO countries (id, name, region, data)
                VALUES (?, ?, ?, ?)
            """, (country_code, name, region, json.dumps(data)))
            total += 1
            print(f"Inserted: {name} ({country_code})")

    con.commit()
    con.close()
    print(f"\nDone. {total} countries inserted into {DB_PATH}")

    # Clean up
    shutil.rmtree(TMP_DIR)
    print("Cleaned up temp files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
