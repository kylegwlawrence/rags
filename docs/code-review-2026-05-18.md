# Code review — datasets repo

**Date:** 2026-05-18
**Scope:** Whole repo as of commit `7e2f800`. ~3,200 lines across `api/`, `rag/`, `scripts/`, `tests/`. Excludes `data/` (gitignored), `.venv/`, and `docs/`.
**Mode:** Report-only — no code changes were applied. Each finding includes a file/line reference and a suggested disposition; you decide which to action and in what order.

## How this is organized

- **High** — real correctness bugs or things that will break for someone running the repo cold.
- **Medium** — improvements that would meaningfully reduce future friction.
- **Low / style** — cosmetic or speculative.
- **Verified fine** — things I checked that look correct, listed so the review's scope is legible.

Severity is about likelihood × cost, not aesthetic preference. None of the High items are urgent — the API has been running with these for a while — but each is the kind of thing where the cost of fixing later (after someone else hits it) is much higher than fixing now.

---

## High

### H1. `requests` is imported but not declared in `requirements.txt`

- `scripts/openalex_download.py:4` does `import requests`.
- `requirements.txt` lists `fastapi`, `uvicorn`, `sqlite-vec`, `httpx`, `pytest` — no `requests`.
- It works on this machine because something pulled in `requests` transitively (it's a very common library). On a fresh `pip install -r requirements.txt && python scripts/openalex_download.py` it would `ImportError`.

**Suggested disposition:** Either add `requests>=2.32` to `requirements.txt`, or port the downloader to `httpx` (which is already a project dep). The rest of the project uses `httpx` (see `rag/embedder.py`), so converging on `httpx` removes the inconsistency. ~10-line change.

### H2. `/openalex/works?q=` returns 400 for a missing `works_fts` table; should return 503

- `api/routers/arxiv.py:173-183` correctly classifies `OperationalError` via `is_operational_error` and returns 503 for the missing-table case, 400 only for FTS5 syntax errors. This is the fix from `WORK.md` §2.2 ("HTTP status to who actually failed").
- `api/routers/openalex.py:141-143` does **not** do the same classification — every `OperationalError` becomes a 400. If anyone drops or fails to build `works_fts`, the response says "bad query" instead of "service not ready."
- The same pattern already exists in this file: `is_operational_error` is imported and used in `_chunks.py`. Backporting to `list_works` is mechanical — copy the arxiv router's try/except shape.

**Suggested disposition:** Mirror the arxiv block (~10 lines). The original project retro flagged the operational-vs-user-error confusion as a general pattern; this is the one spot where it was missed during the fix.

### H3. `/health` always returns HTTP 200, even when databases are broken

- `api/main.py:13-32` returns `{"ok": ..., "databases": {...}}` with the per-DB status in the body, but the response status is always 200.
- Carried over in **five** retros (project retro, phase 2a, 2b, 2c, 2d). Documented as "small change" each time and not done.
- A health probe that only checks status codes won't see this. Anyone watching for outages via `200 OK` will miss every broken DB.

**Suggested disposition:** When `not all(v == "ok" ...)`, return 503 with the same body. ~3 lines. The carry-over has aged enough that it deserves either action or a formal "we accept this" entry in `CLAUDE.md`.

### H4. `README.md` line 38 says "the routes will 500" but the code returns 503

- `README.md:38`: "If a database is missing, the corresponding routes will 500 but the rest of the app still serves."
- `api/db.py:46-50` translates missing-file / unreadable-file errors to `HTTPException(status_code=503, ...)`.
- The README was written before the 503 translation landed (commit `d7f4027` and earlier). Doc drift.

**Suggested disposition:** Change "500" → "503" in the README. One word.

---

## Medium

### M1. `scripts/openalex_download.py` has no retry / no resume-friendly error handling

- `scripts/openalex_download.py:43-52` — single non-200 response from OpenAlex breaks the `while True` loop and the script exits.
- Re-running is safe (`INSERT OR IGNORE`), but the cursor is lost — the next run starts from `cursor=*` and re-fetches every page until it reaches new rows.
- The OpenAlex polite pool is reliable enough that this hasn't bitten yet, but the failure mode is "lose hours of wall-clock to retry a 503 that resolved in 5 seconds."

**Suggested disposition:** A small retry-with-backoff around the `requests.get` call (~10 lines), mirroring the pattern in `rag/embedder.py`. Optional: persist the cursor to disk so re-runs resume rather than restart.

### M2. `scripts/openalex_download.py` and `scripts/factbook_download.py` have no `if __name__ == "__main__":` guard

- Both run their work at module top-level. Importing the module (e.g., from a REPL or by accident in a test) would trigger a full download.
- The arxiv / gutenberg / factbook *indexer* scripts all use the `def main(): ... if __name__ == "__main__": sys.exit(main())` pattern. The two oldest downloaders predate that convention.

**Suggested disposition:** Wrap each in a `main()` function with the guard. Mostly mechanical; modest risk of breaking something subtle in the long top-level openalex loop, so do it in a separate commit from any behavior change.

### M3. `scripts/factbook_download.py` and `scripts/openalex_download.py` don't `mkdir -p` their data dirs

- `factbook_download.py:11`: `DB_PATH = os.path.expanduser("./data/factbook/factbook.db")` then `sqlite3.connect(DB_PATH)` directly.
- `openalex_download.py:9`: same shape.
- If a contributor runs the downloader on a clean checkout with no `data/factbook/` directory yet, SQLite errors with "unable to open database file". The arxiv FTS indexer and the gutenberg indexer both create their parents (`DB_PATH.parent.mkdir(parents=True, exist_ok=True)`).

**Suggested disposition:** Add the `mkdir` line at the top of each downloader. Two lines total.

### M4. No tests cover the `/content` endpoints or the gutenberg path-traversal check

- `tests/test_smoke.py` covers `/papers`, `/works`, `/countries`, `/texts` (list + one detail 404) and the `/chunks` endpoints. No test hits `/arxiv/papers/{id}/content` or `/gutenberg/texts/{id}/content`.
- `api/routers/gutenberg.py:91-100` has a "defense in depth" path-traversal check (`root not in full.parents`). It's exactly the kind of code a future refactor could accidentally break — and the only signal would be a security regression that nobody has a test for.

**Suggested disposition:** Two tests. One happy-path content fetch per source; one synthetic test that mocks `_lookup` to return a `texts.path` like `../etc/passwd` and asserts 404. ~30 lines.

### M5. `scripts/kaggle_download.sh` is still a dormant stub

- 47 lines of placeholders (`KAGGLE_USERNAME="your_kaggle_username"`, `DATASET="owner/dataset-name"`). Carry-over from the original project retro.
- Carrying it costs nothing, but it muddies the "what does this repo do" answer ("four downloaders" is really three plus a stub).

**Suggested disposition:** Either (a) fill it in for a concrete dataset (commits a real workflow), (b) delete it (gets honest about scope), or (c) add a one-line header comment "Template; never used in production" so the next reader knows immediately. Pick one — the worst outcome is another four retros that mention it.

### M6. Connection-cache staleness has no resolution path

- `api/db.py:7-9` documents the staleness mode: "If a downloader script rewrites a DB file while the API is running, restart the server."
- This is mentioned in every Phase 2 retro and the project retro. Documented behavior, but the user-facing impact is that every `_rag.db` rebuild requires a `pkill uvicorn && uvicorn ... &`.
- Two reasonable fixes exist: (a) close-and-reopen on a sentinel value in `_meta` (an `indexed_at` watermark), (b) signal-driven invalidation via a manual `POST /admin/reload` (would break the "strictly read-only" rule).

**Suggested disposition:** Either pick (a) and document the price (~1 SQL roundtrip per `Depends` call), or formally accept the restart workflow in `CLAUDE.md` and stop listing it as carry-over. The current limbo is the worst state.

---

## Low / style

### L1. Embedder duplicates a retry loop in `embed_text` and `embed_texts_batch`

- `rag/embedder.py:52-65` and `:75-88` have near-identical retry-with-backoff scaffolding. The only differences are the URL suffix (`/api/embeddings` vs `/api/embed`), the JSON body shape, and the timeout (30 s vs 120 s).
- A shared `_post_with_retry(url, json, timeout)` would remove ~10 lines and centralize the backoff policy. Per `WORK.md` §3.6, two near-identical implementations is one shy of the abstraction threshold — fine to leave.

**Suggested disposition:** No action unless a third embedder call gets added. Note the parallel structure.

### L2. `scripts/factbook_download.py:11` uses a no-op `expanduser`

- `DB_PATH = os.path.expanduser("./data/factbook/factbook.db")` — `expanduser` only expands `~`. The string has none. The call returns the input verbatim.
- Harmless, but reads as "this is doing something" when it isn't.

**Suggested disposition:** Replace with a `pathlib.Path(...)` against `Path(__file__).resolve().parent.parent / "data" / "factbook" / "factbook.db"` to match the other scripts. Drive-by cleanup if you touch the file for M2 or M3.

### L3. `scripts/openalex_download.py:11` hardcodes a personal email for OpenAlex's polite pool

- `EMAIL = "sagansagansagan@protonmail.com"` — your email, already public in git, used as the polite-pool identifier.
- Not a bug. Worth noting that it ties this repo to your account; if anyone else runs the downloader they'd be sending requests under your email.

**Suggested disposition:** None required. If you wanted, an env-var fallback (`os.environ.get("OPENALEX_EMAIL", EMAIL)`) would let collaborators substitute their own without editing the file.

### L4. `scripts/openalex_download.py` uses `requests`; the rest of the project uses `httpx`

- Mixed HTTP clients across 100 lines of code. The downloader works fine; cognitive cost of remembering "this one script is different."
- Folded into H1 because porting also solves the missing-dependency issue.

**Suggested disposition:** Bundle with H1.

### L5. `/openalex/works` and `/arxiv/papers` run the FTS5 join twice per request

- Both `list_papers` (`api/routers/arxiv.py:160-172`) and `list_works` (`api/routers/openalex.py:131-140`) issue a `SELECT COUNT(*)` and a `SELECT ... LIMIT ? OFFSET ?` separately, each going through the `JOIN papers_fts ON ...` clause when `q` is set.
- At current scales (1.2k arxiv papers, 268k openalex works) this is fast (~10 ms for COUNT on 268k FTS rows). At larger scales it would matter.

**Suggested disposition:** No action. If a future scale-up makes the count latency visible, the standard fix is to estimate via `sqlite_stat1` or drop the exact total and return a "≥" hint. Don't preempt.

### L6. `_chunks.py` factory hardcodes the path shape in its 503 detail

- `api/_chunks.py:60-65` builds `"data/{source_name}/{source_name}_rag.db"` in the error string from `source_name` alone. If a future source has a non-canonical path, this string would be wrong.
- All four current sources happen to follow the canonical shape, so it's correct today.

**Suggested disposition:** No action. Note that adding a fifth source with a different path shape would want a `rag_db_path: Path` parameter alongside `source_name`.

### L7. `tests/test_smoke.py` doesn't assert any specific behavior of `_flatten` in the factbook detail response

- `_flatten` (`api/routers/factbook.py:14-31`) has non-trivial recursion logic (the `{"text": v, "note": ...}` sibling case especially).
- Only the list endpoint is smoke-tested for factbook; the detail endpoint with its `_flatten` pass isn't covered.

**Suggested disposition:** A 5-line test that asserts the flattened shape for one known country (e.g., `us`). The flatten logic was changed once (commit `277441a`) and could regress silently.

---

## Verified fine (checked but no finding)

These are listed so the review's blast radius is clear, not because they need attention.

- **Path traversal in `/gutenberg/texts/{id}/content`** (`api/routers/gutenberg.py:91-100`) — `root not in full.parents` correctly rejects paths that escape `GUTENBERG_ROOT`, including absolute paths in the DB and `..`-only relative paths. The `is_file()` check is the right secondary gate. Defense in depth is appropriate; DB content is trusted but not the bedrock.
- **Read-only-with-shared-thread connection cache** (`api/db.py`) — `mode=ro` plus `check_same_thread=False` is safe across FastAPI's threadpool because the SQLite connection can't mutate state. Documented; correct.
- **Order-of-deletes in `rag/indexer.py:flush`** — `chunks_vec` (virtual table, no FK cascade) is deleted before `chunks`, which is deleted before `docs_meta`. The orphan-vector test (`tests/test_smoke.py:138-153`) covers regressions here.
- **FK enforcement** — `rag/schema.py:36` turns on `PRAGMA foreign_keys=ON` in `connect_rag`, so the `chunks.doc_id REFERENCES docs_meta(doc_id)` constraint is real, not decorative.
- **FTS5 escape strategy** (`rag/retriever.py:111`) — each word is double-quoted individually, with literal `"` stripped first. Inside FTS5 phrase quotes, special characters become literal tokens, so `*`, `(`, `^`, etc. don't introduce injection. Documented in the docstring.
- **Param binding hygiene** — every router uses `?` placeholders and a `params` list. No string-formatted SQL anywhere, including the inline `LIKE '%...%'` substring filters (the `%` is added to the bound value, not the SQL).
- **`Page[T]` generic** — three different filter shapes (arxiv has 7 filters, openalex has 5, gutenberg has 3, factbook has 1), one response contract. Earns its keep; no churn risk.
- **CLI arg validation** in the indexer scripts — `--limit < 1` / `--chunk-size < 1` / `--batch < 1` all raise via `parser.error`. Consistent across all four indexers.

---

## Cross-cutting observations

- The codebase enforces its conventions through repetition, not abstraction. Four routers all build `clauses` + `params` lists the same way; four indexers all wrap `run_indexer`; four extractors all yield `Doc`. The cost is small (10–20 lines of similar shape per source); the benefit is each file is self-contained and grep-friendly.
- The two oldest downloader scripts (`factbook_download.py`, `openalex_download.py`) are noticeably looser than everything else (no `__main__` guard, no `mkdir`, inline state, no docstrings, no retries). Everything written from Phase 1 onward is stylistically tighter. M1/M2/M3 are all about closing that gap; doing them in one commit would be cheap.
- The retros explicitly document several known issues (the `/health` status code, connection-cache staleness, kaggle stub, openalex authorship fragmentation) that haven't been resolved across multiple phases. The pattern of "documented carry-over that ages" is its own signal — see the WORK.md proposal section of the corresponding retro.
- The Phase 2 RAG work is the single largest architectural addition since the API itself, and it landed without breaking any existing route. `rag/` as a peer to `api/` (not nested under it) was the load-bearing design choice.

## Files reviewed

```
api/_chunks.py                 (87)
api/db.py                      (147)
api/main.py                    (32)
api/models.py                  (80)
api/routers/arxiv.py           (230)
api/routers/factbook.py        (78)
api/routers/gutenberg.py       (108)
api/routers/openalex.py        (158)
rag/__init__.py                (75)
rag/chunker.py                 (152)
rag/embedder.py                (99)
rag/indexer.py                 (263)
rag/retriever.py               (168)
rag/schema.py                  (102)
scripts/arxiv_index_fts.py     (59)
scripts/arxiv_index_rag.py     (65)
scripts/arxiv_rag_extract.py   (43)
scripts/factbook_download.py   (77)
scripts/factbook_index_rag.py  (67)
scripts/factbook_rag_extract.py (166)
scripts/gutenberg_download.sh  (2)
scripts/gutenberg_index.py     (143)
scripts/gutenberg_index_rag.py (75)
scripts/gutenberg_rag_extract.py (113)
scripts/kaggle_download.sh     (47)
scripts/openalex_download.py   (111)
scripts/openalex_index_fts.py  (59)
scripts/openalex_index_rag.py  (65)
scripts/openalex_normalize_authors.py (108)
scripts/openalex_rag_extract.py (50)
tests/conftest.py              (26)
tests/test_smoke.py            (153)
README.md                      (176)
requirements.txt               (5)
```
