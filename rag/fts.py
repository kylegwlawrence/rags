"""Shared FTS5 index rebuilder for `scripts/<source>/<source>_index_fts.py`.

Each per-source FTS script does the same three SQL statements with different
identifiers: DROP IF EXISTS the virtual table, CREATE it as external-content
against a backing table, INSERT-SELECT to populate it. The variation is just
table/column names, the rowid alias when the backing PK is INTEGER, and an
optional WHERE filter (sec_edgar / github_readmes only index `status='fetched'`
rows). Everything else — the tokenizer, the print summary, the missing-file
guard — is identical.

This module factors all that out so each script collapses to ~10 lines.
"""

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
    """Drop and rebuild a FTS5 virtual table over an existing content table.

    Args:
        db_path: SQLite file. Returns 1 (with a message to stderr) if absent.
        virtual_table: e.g. ``"papers_fts"``. Always dropped first.
        content_table: e.g. ``"papers"``. The backing table; not modified.
        columns: column names indexed and selected. Must exist on
            ``content_table``.
        content_rowid: column in ``content_table`` used as the FTS rowid.
            Default ``"rowid"`` matches SQLite's implicit rowid (works for
            any table). Pass an INTEGER PK alias like ``"id"`` when the
            primary key already is the rowid — keeps SELECTs consistent.
        where: optional WHERE clause appended to the INSERT-SELECT (and the
            row count). Use for source-specific filters like
            ``"status = 'fetched' AND body IS NOT NULL"``.
        row_label: noun used in the summary (``"papers"``, ``"filings"``).
            Defaults to ``"rows"`` if not given.
        tokenize: FTS5 tokenizer spec. The whole project uses
            ``porter unicode61`` so this rarely needs overriding.

    Returns:
        0 on success, 1 if ``db_path`` doesn't exist. Suitable for use as a
        ``sys.exit`` argument from a ``main()``.
    """
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
