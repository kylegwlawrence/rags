#!/usr/bin/env python3
"""Build FTS5 index over articles.title + articles.text_content (namespace 0 only).

Runs on raspberrypi6 where enwiki.db lives. Replaces the existing title-only
articles_fts with a title+body index using the trigram tokeniser.

Self-contained — no repo imports. Deploy alongside enwiki_remote_server.py:
    scp scripts/enwiki/enwiki_index_fts.py raspberrypi6:~/datasets/enwiki_index_fts.py

Run on the pi (expect a long runtime — ~6-7M rows of body text):
    source ~/datasets/.venv/bin/activate
    python3 ~/datasets/enwiki_index_fts.py

Restart the enwiki tmux session after:
    tmux kill-session -t enwiki
    tmux new-session -d -s enwiki 'cd ~/datasets && exec .venv/bin/uvicorn enwiki_remote_server:app --host 0.0.0.0 --port 8765 2>&1 | tee /tmp/enwiki.log'

Env:
    ENWIKI_DB_PATH  path to enwiki.db (default: ~/datasets/enwiki/enwiki.db)
"""

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

DB_PATH = Path(
    os.environ.get(
        "ENWIKI_DB_PATH",
        str(Path.home() / "datasets" / "enwiki" / "enwiki.db"),
    )
)

VIRTUAL_TABLE = "articles_fts"
CONTENT_TABLE = "articles"
COLUMNS = ("title", "text_content")
WHERE = "namespace = 0"
BATCH_SIZE = 5_000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Index only the first N namespace-0 articles (useful for timing tests).",
    )
    args = parser.parse_args()

    if not DB_PATH.is_file():
        print(f"missing: {DB_PATH}", file=sys.stderr)
        return 1

    cols = ", ".join(COLUMNS)

    print(f"Opening {DB_PATH} ...")
    con = sqlite3.connect(DB_PATH)

    con.execute(
        "CREATE TABLE IF NOT EXISTS fts_checkpoint "
        "(table_name TEXT PRIMARY KEY, last_rowid INTEGER)"
    )

    checkpoint_row = con.execute(
        "SELECT last_rowid FROM fts_checkpoint WHERE table_name = ?",
        [VIRTUAL_TABLE],
    ).fetchone()
    resuming = checkpoint_row is not None

    if resuming:
        last_rowid = checkpoint_row[0]
        print(f"Resuming from rowid {last_rowid:,} ...")
    else:
        last_rowid = 0
        print(f"Dropping {VIRTUAL_TABLE} ...")
        con.execute(f"DROP TABLE IF EXISTS {VIRTUAL_TABLE}")
        print(f"Creating {VIRTUAL_TABLE} (trigram, content={CONTENT_TABLE}, WHERE {WHERE}) ...")
        con.execute(
            f"CREATE VIRTUAL TABLE {VIRTUAL_TABLE} USING fts5("
            f"{cols}, "
            f"content='{CONTENT_TABLE}', "
            f"content_rowid='rowid', "
            f"tokenize='trigram')"
        )
        con.commit()

    rowids = [
        r[0]
        for r in con.execute(
            f"SELECT rowid FROM {CONTENT_TABLE} WHERE {WHERE} AND rowid > ? ORDER BY rowid",
            [last_rowid],
        )
    ]
    if args.limit is not None:
        rowids = rowids[: args.limit]

    remaining = len(rowids)
    total = con.execute(
        f"SELECT COUNT(*) FROM {CONTENT_TABLE} WHERE {WHERE}"
    ).fetchone()[0]
    limit_note = f" (limit {args.limit:,})" if args.limit is not None else ""
    print(f"Inserting {remaining:,} rows{limit_note} in batches of {BATCH_SIZE:,} (total {total:,}) ...")

    t0 = time.time()
    already_done = total - remaining
    indexed = 0
    for batch_start in range(0, len(rowids), BATCH_SIZE):
        batch = rowids[batch_start : batch_start + BATCH_SIZE]
        placeholders = ",".join("?" * len(batch))
        con.execute(
            f"INSERT INTO {VIRTUAL_TABLE}(rowid, {cols}) "
            f"SELECT rowid, {cols} FROM {CONTENT_TABLE} "
            f"WHERE rowid IN ({placeholders})",
            batch,
        )
        con.execute(
            "INSERT OR REPLACE INTO fts_checkpoint(table_name, last_rowid) VALUES (?, ?)",
            [VIRTUAL_TABLE, batch[-1]],
        )
        con.commit()
        indexed += len(batch)
        elapsed = time.time() - t0
        total_done = already_done + indexed
        pct = total_done / total * 100
        rate = indexed / elapsed if elapsed > 0 else 0
        eta = (remaining - indexed) / rate if rate > 0 else 0
        print(
            f"  {total_done:>9,} / {total:,}  ({pct:5.1f}%)  "
            f"{rate:,.0f} rows/s  ETA {eta / 60:.0f} min",
            flush=True,
        )

    con.execute("DELETE FROM fts_checkpoint WHERE table_name = ?", [VIRTUAL_TABLE])
    con.commit()

    elapsed = time.time() - t0
    db_size = DB_PATH.stat().st_size
    con.close()
    print(
        f"Done in {elapsed:.1f}s. "
        f"Indexed {already_done + indexed:,} articles. "
        f"DB file is now {db_size / (1024 ** 3):.1f} GB."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
