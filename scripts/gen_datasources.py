"""Regenerate the "Search routing" block in DATASOURCES.md from api/sources.py.

`api/sources.py` is the single source of truth for the RAG source catalog. This
script rewrites only the text between the BEGIN/END markers in DATASOURCES.md so
the docs never drift from what `GET /sources` serves.

Usage (from the repo root):

    python scripts/gen_datasources.py            # rewrite in place
    python scripts/gen_datasources.py --check    # exit 1 if out of date (CI/test)
"""

import argparse
import sys
from pathlib import Path

# Repo root is the parent of scripts/; make `api` importable when run directly.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.sources import MARKDOWN_BEGIN, MARKDOWN_END, render_markdown_section  # noqa: E402

DOC_PATH = REPO_ROOT / "DATASOURCES.md"


def replace_block(text: str, new_block: str) -> str:
    """Swap the marker-delimited block in `text` for `new_block`."""
    start = text.find(MARKDOWN_BEGIN)
    end = text.find(MARKDOWN_END)
    if start == -1 or end == -1:
        raise SystemExit(
            f"markers not found in {DOC_PATH.name}; expected "
            f"{MARKDOWN_BEGIN!r} ... {MARKDOWN_END!r}"
        )
    end += len(MARKDOWN_END)
    return text[:start] + new_block + text[end:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if DATASOURCES.md is out of date (don't write)",
    )
    args = parser.parse_args()

    current = DOC_PATH.read_text(encoding="utf-8")
    updated = replace_block(current, render_markdown_section())

    if args.check:
        if current != updated:
            print(
                f"{DOC_PATH.name} is out of date — run python scripts/gen_datasources.py",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"{DOC_PATH.name} is up to date.")
        return

    if current == updated:
        print(f"{DOC_PATH.name} already up to date.")
        return
    DOC_PATH.write_text(updated, encoding="utf-8")
    print(f"Updated {DOC_PATH.name}.")


if __name__ == "__main__":
    main()
