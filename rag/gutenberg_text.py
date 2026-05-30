"""Gutenberg .txt body helpers: read from disk, strip PG banners, fingerprint.

Shared by the batch indexer (`scripts/gutenberg/gutenberg_rag_extract.py`) and
the API's live-embed route (`api.routers.gutenberg.embed_text`). Lives in
`rag/` rather than `scripts/gutenberg/` because both a script and the API
need to import it — the same reasoning as `rag.wikitext` and
`rag.sec_filing` (see the rag/__init__.py docstring).
"""

import hashlib
import re
from pathlib import Path

from rag.cleaner import strip_markdown

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


CHARS_PER_PAGE = 2000


def read_text(path: Path) -> str:
    """Read `path` as UTF-8, falling back to UTF-8-sig and Latin-1 for older files."""
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def strip_banners(text: str) -> str:
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


def file_fingerprint(path: Path) -> str:
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
