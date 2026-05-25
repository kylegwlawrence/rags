#!/usr/bin/env python3
"""Remove low-quality README rows from readmes.db, then rebuild its FTS index.

Four filters are applied in priority order (a row is assigned to the first
matching filter):

  1. Link-dump   — large READMEs (≥ 10 KB) with ≥ 8 markdown link markers per
                   KB.  These are curated "awesome" lists whose hundreds of
                   links produce low-signal embedding chunks.
  2. Too short   — stripped text under 150 bytes; no meaningful content.
  3. Image-only  — after stripping HTML and markdown image/badge syntax,
                   fewer than 100 plain-text characters remain.
  4. Non-English — more than 40 % of word characters in the first 2 000 bytes
                   are non-ASCII (catches CJK, Arabic, Cyrillic-dominant docs).

Rows in ``readmes.db`` that match any filter are deleted entirely.  The
``readmes_fts`` FTS5 index is rebuilt afterwards.

If ``data/github/github_readmes_rag.db`` exists, any indexed docs whose
``doc_id`` was just deleted are also removed from that DB (chunks, vectors,
FTS entries) via the shared ``rag.schema.delete_doc_chunks`` helper, and the
RAG ``chunks_fts`` index is rebuilt.

Run in dry-run mode first (default) to preview deletions, then pass
``--execute`` to commit them.

Usage:
    python scripts/github_readmes/github_readmes_prune.py            # dry run
    python scripts/github_readmes/github_readmes_prune.py --execute  # delete
"""

import argparse
import re
import sqlite3
import sys
import warnings
from pathlib import Path

from bs4 import XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag.cleaner import strip_html  # noqa: E402
from rag.fts import run_fts_indexer  # noqa: E402
from rag.schema import connect_rag, delete_doc_chunks  # noqa: E402

READMES_DB = REPO_ROOT / "data" / "github" / "readmes.db"
RAG_DB = REPO_ROOT / "data" / "github" / "github_readmes_rag.db"

# --- filter thresholds -------------------------------------------------------
_LINK_FILTER_MIN_BYTES = 10_000
_LINK_FILTER_MAX_PER_KB = 8
_SHORT_MIN_BYTES = 150
_IMAGE_ONLY_MIN_CHARS = 100
_NON_ENGLISH_THRESHOLD = 0.40
_NON_ENGLISH_SAMPLE = 2_000


def _is_link_dump(readme: str) -> bool:
    """Large README whose content is mostly markdown link markers."""
    n = len(readme)
    if n < _LINK_FILTER_MIN_BYTES:
        return False
    return readme.count("](") * 1000 >= _LINK_FILTER_MAX_PER_KB * n


def _is_too_short(readme: str) -> bool:
    """Stripped text is below the minimum useful length."""
    return len(readme.strip()) < _SHORT_MIN_BYTES


def _is_image_only(readme: str) -> bool:
    """After removing HTML, image tags, and badge links, negligible text remains."""
    cleaned = strip_html(readme)
    # Remove inline images: ![alt](url)
    no_images = re.sub(r"!\[.*?\]\(.*?\)", "", cleaned)
    # Remove badge links: [![alt](img-url)](link-url)
    no_badges = re.sub(r"\[!\[.*?\]\(.*?\)\]\(.*?\)", "", no_images)
    # Strip markdown punctuation and collapse whitespace
    plain = re.sub(r"[#*_\[\]()`~\->|]", "", " ".join(no_badges.split())).strip()
    return len(plain) < _IMAGE_ONLY_MIN_CHARS


def _is_non_english(readme: str) -> bool:
    """High proportion of non-ASCII word characters suggests a non-English README."""
    sample = readme[:_NON_ENGLISH_SAMPLE]
    words = re.findall(r"[^\s<>\"'=]+", sample)
    total = sum(len(w) for w in words)
    if total < 50:
        return False
    non_ascii = sum(1 for c in "".join(words) if ord(c) > 127)
    return (non_ascii / total) > _NON_ENGLISH_THRESHOLD


def _classify(readme: str) -> str | None:
    """Return the filter name that matches, or None if the README is acceptable."""
    if _is_link_dump(readme):
        return "link_dump"
    if _is_too_short(readme):
        return "too_short"
    if _is_image_only(readme):
        return "image_only"
    if _is_non_english(readme):
        return "non_english"
    return None


def _build_delete_sets(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Return a dict of filter_name → [repo, ...] for all rows that should be deleted."""
    rows = conn.execute(
        "SELECT repo, readme FROM readmes WHERE status = 'fetched' AND readme IS NOT NULL AND readme != ''"
    ).fetchall()

    buckets: dict[str, list[str]] = {
        "link_dump": [],
        "too_short": [],
        "image_only": [],
        "non_english": [],
    }
    for repo, readme in rows:
        reason = _classify(readme)
        if reason:
            buckets[reason].append(repo)
    return buckets


def _prune_readmes_db(conn: sqlite3.Connection, to_delete: list[str]) -> None:
    """Delete the given repos from readmes (batched to stay under SQLite limits)."""
    batch_size = 500
    for i in range(0, len(to_delete), batch_size):
        batch = to_delete[i : i + batch_size]
        placeholders = ",".join("?" * len(batch))
        conn.execute(f"DELETE FROM readmes WHERE repo IN ({placeholders})", batch)
    conn.commit()


def _prune_rag_db(doc_ids: set[str]) -> int:
    """Remove docs from github_readmes_rag.db that are in doc_ids. Returns count removed."""
    if not RAG_DB.exists() or not doc_ids:
        return 0

    rag_conn = connect_rag(RAG_DB)
    try:
        indexed = {
            r[0]
            for r in rag_conn.execute("SELECT doc_id FROM docs_meta").fetchall()
        }
        to_remove = doc_ids & indexed
        if not to_remove:
            return 0

        for doc_id in to_remove:
            delete_doc_chunks(rag_conn, doc_id, sync_fts=False)
        rag_conn.commit()

        print(f"  Rebuilding chunks_fts in {RAG_DB.name}...")
        rag_conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        rag_conn.commit()
        rag_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        return len(to_remove)
    finally:
        rag_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete rows and rebuild indexes. Without this flag, only a dry-run report is printed.",
    )
    parser.add_argument("--db", default=str(READMES_DB), help="Path to readmes.db")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"missing: {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path)

    print("Scanning READMEs...")
    buckets = _build_delete_sets(conn)
    total_delete = sum(len(v) for v in buckets.values())

    total_fetched = conn.execute(
        "SELECT COUNT(*) FROM readmes WHERE status = 'fetched'"
    ).fetchone()[0]

    print(f"\nTotal fetched rows : {total_fetched}")
    print(f"Would delete       : {total_delete} ({total_delete / max(total_fetched, 1):.1%})")
    print(f"Would keep         : {total_fetched - total_delete}")
    print()
    for reason, repos in buckets.items():
        if repos:
            examples = ", ".join(repos[:3])
            suffix = f" …+{len(repos) - 3}" if len(repos) > 3 else ""
            print(f"  {reason:<14} {len(repos):>4}  e.g. {examples}{suffix}")

    if not args.execute:
        print("\nDry run — pass --execute to delete.")
        conn.close()
        return

    print("\nDeleting rows...")
    all_repos = [repo for repos in buckets.values() for repo in repos]
    _prune_readmes_db(conn, all_repos)
    conn.close()
    print(f"  Deleted {total_delete} rows from {db_path.name}.")

    print(f"Rebuilding readmes_fts in {db_path.name}...")
    run_fts_indexer(
        db_path=db_path,
        virtual_table="readmes_fts",
        content_table="readmes",
        columns=("name", "readme"),
        where="status = 'fetched' AND readme IS NOT NULL",
        row_label="READMEs",
    )

    print(f"Checking {RAG_DB.name} for orphaned docs...")
    n_rag = _prune_rag_db(set(all_repos))
    if n_rag:
        print(f"  Removed {n_rag} docs from {RAG_DB.name}.")
    else:
        print(f"  No overlap with indexed docs — {RAG_DB.name} unchanged.")

    print("\nDone. Restart uvicorn so the API reopens the updated connections.")


if __name__ == "__main__":
    main()
