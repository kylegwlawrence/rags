"""Extract one Doc per Gutenberg text file for the RAG indexer.

Reads each `.txt` body from disk under `data/gutenberg/<path>`, strips the
Project Gutenberg start/end banner blocks, and yields the cleaned body as
`Doc.text`. No section structure — the chunker (default `chunk_doc`) splits
at paragraph boundaries.

Filter defaults: `language='en'` and `--limit 100` (full corpus is ~50k
books / millions of chunks / many hours on local Ollama; the small default
proves the pipeline without committing to that runtime).

Version key combines `size_bytes` with a SHA-256 prefix of the file's first
and last 4 KB. mtime isn't reliable because the gutenberg mirror rsync can
touch every file on each sync.
"""

import hashlib
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from rag import Doc
from rag.cleaner import CLEANER_VERSION, strip_markdown

# Project Gutenberg has used several banner formats over the years. The
# audit found 5/14 chunks still containing "Project Gutenberg" after the old
# single-pattern regex — these alternates cover the older "Small-Print"
# preamble and the bare-prose "End of the Project Gutenberg..." footer. A
# defensive line-level scrub below catches any stray "Project Gutenberg"
# references that survive the structural banner removal.
_PG_START_RE = re.compile(
    r"\*\*\*\s*START\s+OF\s+(?:THIS|THE)?\s*PROJECT\s+GUTENBERG[^\n*]*\*\*\*"
    r"|\*END\*THE\s+SMALL\s+PRINT[^*\n]*\*"
    r"|\*\s*START\s+OF\s+THE\s+PROJECT\s+GUTENBERG[^\n]*",
    re.IGNORECASE,
)
_PG_END_RE = re.compile(
    r"\*\*\*\s*END\s+OF\s+(?:THIS|THE)?\s*PROJECT\s+GUTENBERG[^\n*]*\*\*\*"
    r"|\*END\s+THE\s+SMALL\s+PRINT[^*\n]*\*"
    r"|End\s+of\s+(?:the\s+)?Project\s+Gutenberg.*?(?=\n\n|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_PG_MENTION_LINE_RE = re.compile(
    r"^.*Project\s+Gutenberg.*$",
    re.MULTILINE | re.IGNORECASE,
)
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def iter_docs(
    gutenberg_conn: sqlite3.Connection,
    *,
    gutenberg_root: Path,
    language: str = "en",
    limit: int = 100,
) -> Iterator[Doc]:
    """Yield one Doc per Gutenberg text matching `language`, capped to `limit`.

    Args:
        gutenberg_conn: Read-only connection to `data/gutenberg/gutenberg.db`.
        gutenberg_root: On-disk root for resolving `texts.path`
            (`data/gutenberg/`).
        language: ISO language code to filter on. Default `en`.
        limit: Max number of texts to yield, ordered by `texts.id`.
    """
    cursor = gutenberg_conn.execute(
        "SELECT id, title, author, path, size_bytes FROM texts "
        "WHERE language = ? ORDER BY id LIMIT ?",
        (language, limit),
    )
    for row in cursor:
        path = gutenberg_root / row["path"]
        if not path.is_file():
            continue  # rsync may not have pulled every file; skip silently
        body = _strip_banners(_read_text(path))
        if not body:
            continue
        title = row["title"] or row["author"] or str(row["id"])
        yield Doc(
            doc_id=str(row["id"]),
            title=title,
            version=f"{_file_fingerprint(path, row['size_bytes'])}-{CLEANER_VERSION}",
            text=body,
            section=None,
        )


def _read_text(path: Path) -> str:
    """Read `path` as UTF-8, falling back to UTF-8-sig and Latin-1 for older files."""
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _strip_banners(text: str) -> str:
    """Remove PG start/end banner blocks plus stray "Project Gutenberg" mentions.

    Sequence: snip everything before the start banner and after the end
    banner, then defensively delete any remaining line that mentions
    "Project Gutenberg" (catches Small-Print remnants and footer prose the
    structural regexes miss), then drop markdown emphasis runs (`**`) the
    older PG text uses for inline emphasis, then collapse blank-line runs.
    """
    start = _PG_START_RE.search(text)
    end = _PG_END_RE.search(text)
    if start:
        text = text[start.end():]
    if end:
        text = text[: end.start()]
    text = _PG_MENTION_LINE_RE.sub("", text)
    text = strip_markdown(text)
    text = _BLANK_RUN_RE.sub("\n\n", text)
    return text.strip()


def _file_fingerprint(path: Path, size_bytes: int | None) -> str:
    """`{size}-{hex}` where hex is SHA-256 prefix over first+last 4 KB.

    mtime drifts on rsync mirrors; size+endpoint-hash is a stable change-detection
    signal that doesn't require reading the whole file.
    """
    actual_size = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(4096)
        tail = b""
        if actual_size > 8192:
            f.seek(actual_size - 4096)
            tail = f.read(4096)
    digest = hashlib.sha256(head + tail).hexdigest()[:16]
    return f"{actual_size}-{digest}"
