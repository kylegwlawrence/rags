"""Shared FTS5 index rebuilder. Each per-source script collapses to ~10 lines by calling run_fts_indexer."""

import sqlite3
import sys
import time
from pathlib import Path


def run_fts_indexer(
    *,
    db_path: Path,
    virtual_table: str,
    content_table: str,
    columns: tuple[str, ...],
    content_rowid: str = "rowid",
    where: str | None = None,
    row_label: str = "rows",
    tokenize: str = "porter unicode61",
) -> int:
    """Drop and rebuild a FTS5 external-content virtual table. Returns 0 on success, 1 if DB missing."""
    if not db_path.is_file():
        print(f"missing: {db_path}", file=sys.stderr)
        return 1

    cols_list = ", ".join(columns)
    where_clause = f" WHERE {where}" if where else ""

    con = sqlite3.connect(db_path)
    cur = con.cursor()

    t0 = time.time()
    cur.execute(f"DROP TABLE IF EXISTS {virtual_table}")
    cur.execute(
        f"CREATE VIRTUAL TABLE {virtual_table} USING fts5("
        f"{cols_list}, "
        f"content='{content_table}', "
        f"content_rowid='{content_rowid}', "
        f"tokenize='{tokenize}')"
    )
    cur.execute(
        f"INSERT INTO {virtual_table}(rowid, {cols_list}) "
        f"SELECT {content_rowid}, {cols_list} FROM {content_table}{where_clause}"
    )
    con.commit()

    # `SELECT COUNT(*) FROM <fts_table>` returns the *content* table's total
    # for external-content tables, not the indexed-row count. Count the
    # source rows we actually inserted instead.
    indexed = cur.execute(
        f"SELECT COUNT(*) FROM {content_table}{where_clause}"
    ).fetchone()[0]
    db_size = db_path.stat().st_size

    con.close()
    elapsed = time.time() - t0
    print(
        f"Done in {elapsed:.1f}s. "
        f"Indexed {indexed} {row_label}. "
        f"DB file is now {db_size / (1024**2):.1f} MB."
    )
    return 0
