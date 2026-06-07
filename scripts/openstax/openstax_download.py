#!/usr/bin/env python3
"""Download OpenStax textbooks from their GitHub `osbooks-*` repos into
`data/openstax/openstax.db`.

OpenStax publishes every book as a public, CC-licensed GitHub repo of XML:
`collections/<slug>.collection.xml` is the table of contents and
`modules/<mNNNNN>/index.cnxml` is each section's content (see `rag.openstax`).
For each repo this script shallow-clones it into a temp working dir, parses the
COLLXML for chapter/section order, renders each section's CNXML to plain text
with inline `$…$` LaTeX (formulas are presentation MathML, rebuilt by
`rag.mathml`), loads three tables — `books`, `chapters`, `sections` — and then
deletes the clone so nothing large lands on the (often full) `/home` disk.

Idempotent: re-running replaces every row for each book. After a run, build the
search index with `openstax_index_fts.py` and restart uvicorn.

Covers every English OpenStax title across all shelves; add or remove repos in
`OPENSTAX_REPOS` (or pass `--repos`) to change the set. The clone is shallow +
sparse (`collections/`, `modules/` and `media/`). Each repo's images are copied
to `data/openstax/media/{repo}/` and referenced from the body as Markdown image
links (`![alt](/openstax/media/{repo}/file.jpg)`, served by the API). Pass
`--skip-images` for the old text-only behaviour.
"""

import argparse
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag.openstax import cnxml_to_markdown, parse_collection  # noqa: E402
from rag.schema import connect_rag  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "openstax" / "openstax.db"
RAG_DB_PATH = REPO_ROOT / "data" / "openstax" / "openstax_rag.db"
MEDIA_DIR = REPO_ROOT / "data" / "openstax" / "media"

# Image file extensions copied out of each repo's media/ folder. Other media
# (video/audio) is never referenced by the rendered body, so it is left behind.
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp"}

GITHUB_BASE = "https://github.com/openstax"

# (repo, subject). Slugs are auto-discovered from each repo's collections/
# folder, so adding a subject is just adding its osbooks-* repo here. The full
# set of English OpenStax titles across every shelf; non-English translation
# repos (calculo, precalculo, fizyka, makroekonomia, …) and the non-book
# `playground` repo are deliberately omitted.
OPENSTAX_REPOS = [
    # --- mathematics & statistics ---
    ("osbooks-prealgebra-bundle", "mathematics"),
    ("osbooks-college-algebra-bundle", "mathematics"),
    ("osbooks-calculus-bundle", "mathematics"),
    ("osbooks-introductory-statistics-bundle", "mathematics"),
    ("osbooks-statistics", "mathematics"),
    ("osbooks-contemporary-mathematics", "mathematics"),
    ("osbooks-algebra-1", "mathematics"),
    ("osbooks-principles-data-science", "mathematics"),
    # --- science ---
    ("osbooks-anatomy-physiology", "science"),
    ("osbooks-astronomy", "science"),
    ("osbooks-biology-bundle", "science"),
    ("osbooks-chemistry-bundle", "science"),
    ("osbooks-college-physics-bundle", "science"),
    ("osbooks-microbiology", "science"),
    ("osbooks-neuroscience", "science"),
    ("osbooks-organic-chemistry", "science"),
    ("osbooks-physics", "science"),
    ("osbooks-university-physics-bundle", "science"),
    # --- social sciences ---
    ("osbooks-american-government", "social-sciences"),
    ("osbooks-introduction-anthropology", "social-sciences"),
    ("osbooks-introduction-political-science", "social-sciences"),
    ("osbooks-introduction-sociology", "social-sciences"),
    ("osbooks-life-liberty-and-pursuit-happiness", "social-sciences"),
    ("osbooks-lifespan-development", "social-sciences"),
    ("osbooks-psychology", "social-sciences"),
    # --- business & economics ---
    ("osbooks-business-ethics", "business"),
    ("osbooks-business-law", "business"),
    ("osbooks-entrepreneurship", "business"),
    ("osbooks-foundations-information-systems", "business"),
    ("osbooks-introduction-business", "business"),
    ("osbooks-introduction-intellectual-property", "business"),
    ("osbooks-principles-accounting-bundle", "business"),
    ("osbooks-principles-economics-bundle", "economics"),
    ("osbooks-principles-finance", "business"),
    ("osbooks-principles-marketing", "business"),
    ("osbooks-principles-of-management-bundle", "business"),
    # --- humanities ---
    ("osbooks-introduction-philosophy", "humanities"),
    ("osbooks-us-history", "humanities"),
    ("osbooks-world-history", "humanities"),
    ("osbooks-writing-guide", "humanities"),
    # --- computer science / career / engineering ---
    ("osbooks-introduction-python-programming", "computer-science"),
    ("osbooks-workplace-software-skills", "computer-science"),
    ("osbooks-additive-manufacturing", "engineering"),
    # --- other ---
    ("osbooks-college-success-bundle", "college-success"),
    ("osbooks-nursing-external-bundle", "nursing"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    book_id      TEXT PRIMARY KEY,   -- collection slug, e.g. 'calculus-volume-1'
    title        TEXT NOT NULL,
    subject      TEXT NOT NULL,
    repo         TEXT NOT NULL,
    uuid         TEXT,
    license      TEXT,
    commit_sha   TEXT,
    num_chapters INTEGER NOT NULL DEFAULT 0,
    num_sections INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chapters (
    chapter_id   TEXT PRIMARY KEY,   -- '{book_id}#ch{seq}'
    book_id      TEXT NOT NULL REFERENCES books(book_id),
    number       INTEGER,            -- chapter ordinal; NULL for front/back matter
    title        TEXT,
    seq          INTEGER NOT NULL    -- absolute order within the book
);

CREATE TABLE IF NOT EXISTS sections (
    id             INTEGER PRIMARY KEY,  -- rowid the FTS index keys on
    section_id     TEXT UNIQUE NOT NULL, -- '{book_id}/{module_id}'
    book_id        TEXT NOT NULL REFERENCES books(book_id),
    chapter_id     TEXT NOT NULL REFERENCES chapters(chapter_id),
    chapter_number INTEGER,
    chapter_title  TEXT,
    module_id      TEXT NOT NULL,
    title          TEXT,
    objectives     TEXT,                 -- one learning objective per line
    body           TEXT NOT NULL,        -- section prose w/ inline $…$ LaTeX
    seq            INTEGER NOT NULL      -- absolute order within the book
);

CREATE INDEX IF NOT EXISTS idx_chapters_book ON chapters(book_id, seq);
CREATE INDEX IF NOT EXISTS idx_sections_book ON sections(book_id, seq);
CREATE INDEX IF NOT EXISTS idx_sections_chapter ON sections(chapter_id, seq);
CREATE INDEX IF NOT EXISTS idx_sections_subject ON sections(book_id);
"""


def _clone(repo: str, dest: Path, with_media: bool = True) -> str:
    """Shallow + sparse clone `repo` into `dest`; return its HEAD commit sha.

    Uses a blobless partial clone with a sparse checkout limited to
    `collections/`, `modules/` and (when `with_media`) the repo's `media/`
    image folder. Falls back to a plain shallow clone if the git version
    doesn't support partial/sparse clone.
    """
    url = f"{GITHUB_BASE}/{repo}.git"
    sparse = ["collections", "modules"] + (["media"] if with_media else [])
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--filter=blob:none",
             "--sparse", url, str(dest)],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(dest), "sparse-checkout", "set", *sparse],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError:
        # Older git: fall back to a full shallow clone (pulls media too).
        if dest.exists():
            shutil.rmtree(dest)
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            check=True,
        )
    sha = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return sha


def _copy_media(clone_dir: Path, repo: str) -> str | None:
    """Copy a repo's image files from its `media/` folder → `data/openstax/media/{repo}/`.

    Images are shared across the volumes of a bundle, so they are namespaced by
    repo (one copy per repo). Returns the URL prefix for this repo's images, or
    None when the repo has no media folder (nothing to reference).
    """
    src = clone_dir / "media"
    if not src.is_dir():
        return None
    dest = MEDIA_DIR / repo
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in src.iterdir():
        if path.is_file() and path.suffix.lower() in _IMAGE_EXTS:
            shutil.copy2(path, dest / path.name)
            count += 1
    print(f"  copied {count} images → {dest}")
    return f"/openstax/media/{repo}"


def _load_book(
    cur: sqlite3.Cursor,
    clone_dir: Path,
    collection_file: Path,
    repo: str,
    subject: str,
    commit_sha: str,
    media_prefix: str | None,
) -> tuple[str, int, int]:
    """Parse one collection + its modules and (re)insert its rows.

    Returns `(book_id, num_chapters, num_sections)`.
    """
    info = parse_collection(collection_file.read_text(encoding="utf-8"))
    book_id = info.slug or collection_file.stem.replace(".collection", "")

    # Idempotent: clear any prior rows for this book first.
    cur.execute("DELETE FROM sections WHERE book_id = ?", (book_id,))
    cur.execute("DELETE FROM chapters WHERE book_id = ?", (book_id,))
    cur.execute("DELETE FROM books WHERE book_id = ?", (book_id,))

    cur.execute(
        "INSERT INTO books (book_id, title, subject, repo, uuid, license, "
        "commit_sha, num_chapters, num_sections) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)",
        (book_id, info.title, subject, repo, info.uuid, info.license, commit_sha),
    )

    modules_root = clone_dir / "modules"
    seq = 0
    section_count = 0
    for ch_idx, chapter in enumerate(info.chapters, start=1):
        chapter_id = f"{book_id}#ch{ch_idx}"
        cur.execute(
            "INSERT INTO chapters (chapter_id, book_id, number, title, seq) "
            "VALUES (?, ?, ?, ?, ?)",
            (chapter_id, book_id, chapter.number,
             chapter.title or "Front/Back Matter", ch_idx),
        )
        for module_id in chapter.module_ids:
            cnxml_path = modules_root / module_id / "index.cnxml"
            if not cnxml_path.is_file():
                print(f"  ! missing module {module_id} in {book_id}", file=sys.stderr)
                continue
            parsed = cnxml_to_markdown(
                cnxml_path.read_text(encoding="utf-8"), media_prefix=media_prefix
            )
            if not parsed.body.strip():
                continue  # nothing useful to store (e.g. a stub)
            seq += 1
            cur.execute(
                "INSERT INTO sections (section_id, book_id, chapter_id, "
                "chapter_number, chapter_title, module_id, title, objectives, "
                "body, seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"{book_id}/{module_id}", book_id, chapter_id, chapter.number,
                 chapter.title, module_id, parsed.title, parsed.objectives,
                 parsed.body, seq),
            )
            section_count += 1

    num_chapters = len(info.chapters)
    cur.execute(
        "UPDATE books SET num_chapters = ?, num_sections = ? WHERE book_id = ?",
        (num_chapters, section_count, book_id),
    )
    return book_id, num_chapters, section_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH,
                        help=f"Output SQLite DB (default {DB_PATH}).")
    parser.add_argument("--repos", nargs="*",
                        help="Override the repo list (subject defaults to "
                             "'mathematics' for repos passed here).")
    parser.add_argument("--work-dir", type=Path, default=None,
                        help="Temp dir for clones (default: a system temp dir on "
                             "the root filesystem). Removed on success.")
    parser.add_argument("--keep-clones", action="store_true",
                        help="Don't delete the cloned repos (for debugging).")
    parser.add_argument("--skip-images", action="store_true",
                        help="Don't fetch or copy images; produce text-only "
                             "bodies (the old behaviour).")
    args = parser.parse_args()

    repos = ([(r, "mathematics") for r in args.repos]
             if args.repos else OPENSTAX_REPOS)

    args.db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(args.db)
    con.executescript(SCHEMA)
    cur = con.cursor()

    # Clone into a temp dir on the root filesystem (where there's space), one
    # repo at a time so peak disk use is a single repo's clone.
    work_root = Path(tempfile.mkdtemp(prefix="openstax_", dir=args.work_dir))
    total_books = total_sections = 0
    try:
        for repo, subject in repos:
            print(f"\n=== {repo} ({subject}) ===")
            clone_dir = work_root / repo
            sha = _clone(repo, clone_dir, with_media=not args.skip_images)
            media_prefix = (None if args.skip_images
                            else _copy_media(clone_dir, repo))
            collections = sorted((clone_dir / "collections").glob("*.collection.xml"))
            if not collections:
                print(f"  ! no collections found in {repo}", file=sys.stderr)
            for coll in collections:
                book_id, n_ch, n_sec = _load_book(
                    cur, clone_dir, coll, repo, subject, sha, media_prefix
                )
                con.commit()
                total_books += 1
                total_sections += n_sec
                print(f"  {book_id}: {n_ch} chapters, {n_sec} sections")
            if not args.keep_clones:
                shutil.rmtree(clone_dir, ignore_errors=True)
    finally:
        con.close()
        if not args.keep_clones:
            shutil.rmtree(work_root, ignore_errors=True)

    print(f"\nDone. {total_books} books, {total_sections} sections → {args.db}")

    # Create the RAG DB (schema only) so the read-only opener and /health stay
    # green before the first batch-index or live embed — same as eCFR.
    RAG_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connect_rag(RAG_DB_PATH).close()
    print(f"Ensured empty RAG DB at {RAG_DB_PATH}")
    print("Next: run scripts/openstax/openstax_index_fts.py, then restart uvicorn.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
