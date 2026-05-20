"""Extract one Doc per factbook country for the RAG indexer.

Each country's `data` JSON is walked depth-first into a markdown document
shaped like:

    ## Introduction
    Background: <text>

    ## Geography
    Location: <text>
    Area > total: <value>
    Area > note: <text>

    ...

The top-level keys (Introduction, Geography, …) become `##` headings — those
are what `rag.chunker.chunk_markdown` splits on. Nested keys are flattened
into `"a > b > c: value"` lines so the embed-time chunks preserve the
section/subsection context that the JSON tree encoded.

`{"text": v}` wrappers are unwrapped to just `v` (consistent with the
`_flatten` helper in `api/routers/factbook.py`); when the wrapper has sibling
keys (e.g. `{"text": "...", "note": "..."}`), both are emitted under the
parent path.
"""

import hashlib
import json
import sqlite3
from collections.abc import Iterator

from rag import Doc
from rag.cleaner import CLEANER_VERSION, normalize_whitespace, strip_html

# Top-level section keys that show up in factbook JSON. Used both as the
# canonical ordering and as the set of "## heading" producers — anything
# outside this list at the top level would also become a heading.
SECTION_ORDER = (
    "Introduction",
    "Geography",
    "People and Society",
    "Environment",
    "Government",
    "Economy",
    "Energy",
    "Communications",
    "Transportation",
    "Military and Security",
    "Space",
    "Terrorism",
    "Transnational Issues",
)


def iter_docs(factbook_conn: sqlite3.Connection, limit: int | None = None) -> Iterator[Doc]:
    """Yield one Doc per row in `factbook.countries`, optionally capped to `limit`.

    `Doc.text` is a markdown rendering of the JSON tree with `##` headings
    per top-level section. `Doc.version` is a SHA-256 hex prefix of the raw
    JSON string — the only edit-detection signal factbook exposes (no
    per-row `updated_at` column).
    """
    if limit is not None:
        cursor = factbook_conn.execute(
            "SELECT id, name, data FROM countries ORDER BY id LIMIT ?",
            (limit,),
        )
    else:
        cursor = factbook_conn.execute(
            "SELECT id, name, data FROM countries ORDER BY id"
        )
    for row in cursor:
        raw = row["data"]
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Malformed JSON shouldn't happen in practice (downloader writes
            # valid JSON), but skip rather than crash the whole run.
            continue
        text = _render_markdown(data)
        if not text.strip():
            continue
        # Inline (not `rag.content_hash`) because the shared helper uses
        # trailing-NUL separators for multi-arg boundary safety, which
        # produces a different digest for the single-arg JSON-blob case and
        # would invalidate every previously-stored factbook version. The
        # CLEANER_VERSION suffix invalidates on any cleaning-behaviour change.
        version = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        yield Doc(
            doc_id=row["id"],
            title=row["name"] or row["id"],
            version=f"{version}-{CLEANER_VERSION}",
            text=text,
            section=None,
        )


def _render_markdown(data: dict) -> str:
    """Walk the country JSON depth-first and render it as `## Section`-style markdown."""
    parts: list[str] = []
    seen: set[str] = set()
    # Emit sections in canonical order first, then any unexpected ones in
    # dict-iteration order. Keeps output deterministic across factbook versions.
    for section_name in SECTION_ORDER:
        if section_name not in data:
            continue
        seen.add(section_name)
        body = _section_body(data[section_name])
        if body:
            parts.append(f"## {section_name}\n{body}")
    for k, v in data.items():
        if k in seen or not isinstance(v, dict):
            continue
        body = _section_body(v)
        if body:
            parts.append(f"## {k}\n{body}")
    return "\n\n".join(parts)


def _section_body(obj: object) -> str:
    """Flatten a section's nested dict into newline-joined `Path > To: value` lines."""
    lines: list[str] = []
    for line in _walk_to_lines(obj, ""):
        line = line.strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _walk_to_lines(obj: object, path: str) -> Iterator[str]:
    """DFS over the JSON tree, emitting `"path: value"` (or `"value"` at the root) lines.

    Handles the factbook-specific `{"text": v}` wrapper: when an object's only
    key is `"text"`, the text is emitted under the current path. When `"text"`
    has siblings (e.g. `"note"`), both are emitted under the parent path.
    """
    if isinstance(obj, dict):
        if "text" in obj and isinstance(obj["text"], str):
            text_val = normalize_whitespace(strip_html(obj["text"]))
            if text_val:
                yield _line(path, text_val)
            for k, v in obj.items():
                if k == "text":
                    continue
                yield from _walk_to_lines(v, _join(path, k))
        else:
            for k, v in obj.items():
                yield from _walk_to_lines(v, _join(path, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_to_lines(v, f"{path}[{i}]" if path else f"[{i}]")
    elif obj is None:
        return
    elif isinstance(obj, str):
        s = normalize_whitespace(strip_html(obj))
        if s:
            yield _line(path, s)
    else:
        yield _line(path, str(obj))


def _join(path: str, key: str) -> str:
    return f"{path} > {key}" if path else key


def _line(path: str, value: str) -> str:
    return f"{path}: {value}" if path else value
