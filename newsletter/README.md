# newsletter — daily cs.AI arXiv newsletter

A self-contained package that turns each day's new **cs.AI** arXiv papers into a
short, themed newsletter for a lay reader, using a local Ollama model.

It runs in two passes:

1. **Map** — each paper's abstract is summarized independently into 1–2 plain
   sentences (fresh model context per paper).
2. **Reduce** — all of the day's summaries are consolidated into one issue: a
   short intro digest, then the papers grouped into 4–8 themes.

Issues are stored as markdown in its own `newsletter.db` and served over a small
FastAPI router under `/newsletter`.

## Decoupled by design

This package imports **nothing** from the surrounding repo's `api/` or `rag/`
packages. It brings its own SQLite access, its own Ollama client (stdlib
`urllib`), and its own Pydantic models, so it can be lifted into a standalone
repo with `git mv newsletter/ ../newsletter-repo/` plus moving `data/newsletter/`.

Its only contact with the host repo is two trivially-removable touchpoints:

- a guarded `include_router` in `api/main.py`, and
- a final task in `dags/arxiv_daily_dag.py` (added once the pipeline is proven).

## Running

From the repo root, with the venv active:

```bash
# Yesterday (UTC) — the nightly default:
python -m newsletter.cli

# A specific announcement date:
python -m newsletter.cli --oai-date 2026-06-25

# Fast smoke test — summarize only the first 5 papers, then compose:
python -m newsletter.cli --oai-date 2026-06-25 --limit 5
```

The run is **resumable**: a paper already in `paper_summaries` for that date is
skipped, so re-running only fills gaps and re-composes. If the compose pass
fails, the issue is left `status='partial'` with summaries intact and a re-run
redoes only the compose.

## Configuration (environment variables)

| Env var | Default | Meaning |
|---|---|---|
| `NEWSLETTER_ARXIV_DB` | `/datasets/arxiv/arxiv.db` | read-only source DB |
| `NEWSLETTER_DB` | `data/newsletter/newsletter.db` | this package's own DB |
| `NEWSLETTER_OLLAMA_URL` | `http://localhost:11434` | Ollama base URL |
| `NEWSLETTER_MODEL` | `qwen3.5:9b` | generation model tag |
| `NEWSLETTER_CATEGORY` | `cs.AI` | arxiv `primary_category` covered |
| `SUMMARY_NUM_CTX` | `8192` | `num_ctx` for the map calls |
| `COMPOSE_NUM_CTX` | `65536` | `num_ctx` for the reduce call |

## API

- `GET /newsletter/issues` — list issues, newest first (`limit`/`offset`).
- `GET /newsletter/issues/latest` — the most recent issue (full markdown).
- `GET /newsletter/issues/{run_date}` — one issue by date (full markdown).

A missing `newsletter.db` returns `503` (run the CLI first).

## Data model (`newsletter.db`)

- `paper_summaries` — one row per paper per run (map output); copies the title
  in so issues render without arxiv.db. PK `(paper_id, run_date)`.
- `issues` — one row per day (reduce output): `intro`, full `body_md`, counts,
  model, and `status` (`complete` | `partial` | `failed` | `empty`).

Per-paper links are derived from `paper_id` at render time (no URL stored).
