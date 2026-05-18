# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Personal collection of one-shot downloader scripts that fetch public datasets into local SQLite databases or plain files under `data/<source>/`. Each script is independent — there is no shared library, build system, or test suite. The `data/` directory is gitignored; only the scripts are tracked.

## Running scripts

A Python venv lives at `.venv/`. Activate before running Python scripts:

```bash
source .venv/bin/activate
python scripts/factbook_download.py
python scripts/openalex_download.py
bash   scripts/kaggle_download.sh
bash   scripts/gutenberg_download.sh
```

Scripts assume they are run from the repo root — they use relative paths like `./data/<source>/<source>.db` or `data/<source>/<source>.db`. `cd` to the repo root first.

## Per-script notes

- **`factbook_download.py`** — Clones `github.com/factbook/factbook.json` to `/tmp/factbook_json`, walks the per-region directories, and inserts each country as one row (with the full JSON blob in a `data` column) into a `countries` table at `data/factbook/factbook.db`. Temp clone is removed on success.
- **`openalex_download.py`** — Paginates the OpenAlex `/works` API filtering by `cited_by_count` and reconstructs abstracts from the inverted-index format the API returns. Uses the OpenAlex "polite pool" (`mailto=` param) so changing the `EMAIL` constant matters for rate limiting. Cursor pagination, ~10 req/sec.
- **`gutenberg_download.sh`** — Single rsync line that runs on a remote host (`pop-os`) via SSH and pulls `.txt` files from the ibiblio Gutenberg mirror. Not self-contained — requires that SSH alias to resolve.
- **`kaggle_download.sh`** — Template script: `KAGGLE_USERNAME`, `KAGGLE_API_KEY`, and `DATASET` must be filled in before use. Writes credentials to `~/.kaggle/kaggle.json` and shells out to the `kaggle` CLI (pip-installed on demand).

## Conventions

- Each new source gets its own script in `scripts/` and writes to `data/<source>/`.
- SQLite tables use `INSERT OR REPLACE` / `INSERT OR IGNORE` so scripts are safe to re-run incrementally.
