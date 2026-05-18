# Phase 2a retro — Shared RAG infra + /arxiv/chunks

**Date:** 2026-05-18
**Scope:** Phase 2a of the revised arxiv migration plan. Build a shared `rag/` package (chunker, embedder, retriever, schema) reusable across all four datasources, wire it up for the arxiv source with a `/arxiv/chunks` hybrid-search endpoint, build `data/arxiv/arxiv_rag.db` from scratch via the new indexer, add a pytest scaffold with smoke tests. Per the locked Phase 2a decisions: arxiv embeds title+abstract only, full-HTML chunking deferred to Phase 3.
**Status:** Shipped. ~14 min to embed 1238 papers into 1645 chunks (DB 8.6 MB). 11/11 pytest pass. `/arxiv/chunks?q=…` returns RRF-merged dense+sparse hits; `used_dense` toggles correctly under monkeypatch. Two commits — code + docs.

## Summary

The plan agent's structure carried through verbatim: `rag/` at the repo root with one module per concern (chunker, embedder, retriever, schema); per-source extractors next to their indexer scripts (`scripts/<source>_rag_extract.py` and `scripts/<source>_index_rag.py`); uniform RAG schema across sources (`chunks`, `chunks_fts`, `chunks_vec`, `docs_meta`, `_meta`); a new `_connect_ro_with_vec` helper in `api/db.py` that loads sqlite-vec on top of the existing 503-translating `_connect_ro`. The indexer detects legacy `paper_chunks*` tables (the old upstream schema) and rebuilds from scratch with a stderr warning. pytest came in alongside under `tests/` with `TestClient` + `app.dependency_overrides` for the 503 case and `monkeypatch` for the Ollama-down case.

## What went well

- **The pre-implementation plan agent paid off.** The four locked decisions (one model, per-source chunk_size, all-sources scope, per-source endpoints) and the module layout were settled before coding began. Implementation was mechanical — port `local_wikipedia/rag/embedder.py` and `retriever.py` almost verbatim, change `articles_meta` → `docs_meta`, drop `chunk_type`, lock the model tag.
- **Day-1 verification answered the only open infra question.** `sqlite-vec.load()` on a `mode=ro` connection works fine — no fallback to read-write + `PRAGMA query_only=ON` needed. Five lines of test code on Day 1; would have been a multi-hour detour if it had failed mid-build.
- **Schema-mismatch auto-rebuild is the right ergonomic.** The pre-existing `arxiv_rag.db` had `paper_chunks*` tables from upstream; the indexer detected this, printed `rebuilding arxiv_rag.db: legacy upstream schema (paper_chunks tables present)`, and continued. No `--reset` flag needed for the obvious case.
- **`ChunksResponse` is not `Page[T]`** — and adding the new model alongside `Page` confirmed it was the right call. RRF doesn't paginate; `used_dense` doesn't fit. The pattern in WORK.md §3.3 (generic wrappers earn their keep only when the contract is uniform) is the test, and this one fails the test.
- **pytest came in cheap.** `TestClient` against the real `app`, `app.dependency_overrides[db.arxiv_rag] = fake_503` for the 503 case, `monkeypatch.setattr(embedder, "embed_text", boom)` for sparse-only. 11 tests in 0.22 s. The longest-running test was happy-path FTS+vec, which actually exercises Ollama. Clears the long-standing carry-over from the project retro.
- **Two-file split (extractor + indexer) is justified even with one source.** The extractor is ~30 lines, the indexer is ~150. Keeping them separate lets the indexer skeleton be the same across sources and the extractor be the only per-source code. When Phase 2b adds openalex, only the extractor changes.
- **Dense+sparse fusion actually works at this scale.** `?q=attention mechanism` returned the convolutional-transformer paper at the top spot, with RRF score 0.0312 — both dense and sparse hits contributed. `?q=transformer language model` returned MABViT, SentinelLMs, COMMA. The corpus is only 1645 chunks and most papers have one chunk each, so retrieval quality scales with corpus size from here.

## What went wrong / what I learned

- **`schema.get_meta` hardcoded `row["value"]`** and broke when called from `_needs_rebuild` with a plain `sqlite3.connect()` (no `row_factory`). Caught on the first full indexer run with a clean traceback. Fix was one character (`row[0]`) and a docstring note that the helper now works either way. **Takeaway:** helpers that take a bare `sqlite3.Connection` should not assume `row_factory` — index access is universally safe, key access only works after `conn.row_factory = sqlite3.Row`.
- **The plan asserted `nomic-embed-text:v1.5`, the source code had bare `nomic-embed-text`.** The Plan agent's first survey reported the v1.5 tag, but `local_wikipedia/rag/embedder.py:10` is actually `EMBED_MODEL = "nomic-embed-text"`. The user caught this when I tried to `ollama pull nomic-embed-text` and corrected me with the specific tag. The corrected tag is what's locally pulled and what the embedder now uses. **Takeaway:** model tag strings are load-bearing — verify against `ollama list` before committing to one in code.
- **Couldn't actually kill Ollama for the manual sparse-only test** because stopping a systemd-managed service needs sudo. Fell back to pytest's `monkeypatch.setattr(embedder, "embed_text", boom)`. **Takeaway:** "external service down" is a unit-test boundary, not a curl test — the curl matrix's value is end-to-end *with the dependency up*. Don't waste plan time on curl steps that need root.
- **Indexer ran silently for 14 minutes.** No periodic progress output; I had to poll the DB row count manually to track progress (345 → 433 → 868 → 1067 → 1238). The script does print a summary at the end, but during a multi-minute run the user has no signal. **Takeaway:** add a progress callback or `if n_seen % 100 == 0: print(...)` line to the indexer skeleton — cheaper than tail -f.
- **Backgrounded `pkill` produces exit-code-144 "failed" task notifications.** Same as Phase 1 retro; still cosmetic; still confusing because the kill was intentional. Could be quieted by `nohup ... & disown` or by checking the exit reason in the notification handler. Not blocking.
- **Untracked `.claude/` directory.** Not gitignored, surfaced in `git status` during the commit step. I left it untracked rather than ignoring it inline (out of scope for Phase 2a). Worth fixing in a future cleanup.

## Decisions worth remembering

- **`rag/` at the repo root, not under `api/` or `scripts/_lib/`.** Both readers (FastAPI router) and writers (indexer scripts) import from it. Putting it inside either subtree would force one side to import "across the boundary" — a peer directory resolves it cleanly.
- **Generic table names everywhere (`chunks`, `chunks_fts`, `chunks_vec`, `docs_meta`, `_meta`).** Source identity comes from the file path (`<source>_rag.db`), not the table name. The shared schema DDL and the shared retriever both work unmodified on any source's RAG DB.
- **`EMBED_MODEL = "nomic-embed-text:v1.5"`** — specific tag, not bare. Stored in `_meta.embed_model` per DB so the indexer can detect mismatch on subsequent runs and wipe-and-rebuild rather than silently mixing vectors across model versions.
- **503 for missing RAG DB / index tables; 400 only for empty `q`.** Same convention as the arxiv metadata router (per `WORK.md` §2.2). Inherits the central `_connect_ro` 503 path automatically.
- **`ChunksResponse` (not `Page[Chunk]`) for hybrid search.** Different contract; reusing `Page` would have meant null/zero `total` and `offset` fields with no meaning, plus no place for `used_dense`.
- **Sparse-only fallback when Ollama is unreachable** (200 with `used_dense=False`), not 503. The sparse FTS hits are still useful and the boolean lets the client decide whether to retry.
- **`schema.get_meta`/`set_meta` use index access** (`row[0]`) instead of `row["value"]` so callers don't need `row_factory = sqlite3.Row`.

## Carry-over

- **Phase 2b: openalex top 5k by `cited_by_count`.** The extractor is straightforward (`f"{title}\n\n{abstract}"`, version = content hash). The indexer skeleton works verbatim. Estimate ~5–10 min embed run.
- **Phase 2c: factbook (all 261 countries).** Tests the JSON-walker extractor pattern — first source where the extractor builds text from structured data, not concatenates columns.
- **Phase 2d: gutenberg `--limit 100 --language en`.** Per-paper time is the highest (full book text per doc).
- **Phase 3: port OAI ingest + `render.py` from `local_wikipedia`.** Promotes arxiv from title+abstract chunking to full-HTML chunking. `arxiv/` becomes removable from `local_wikipedia` at this point.
- **Indexer progress reporting** — add `if n_seen % 100 == 0: print(...)` or a tqdm-style progress bar to the shared indexer skeleton. Worth doing before Phase 2b's 5k-paper run.
- **`.claude/` gitignore.** Add `/.claude` (or specific files like `settings.local.json`, `scheduled_tasks.lock`) to `.gitignore`. One-line cleanup.
- **Connection-cache invalidation** — unchanged from Phase 1's carry-over and the project retro's carry-over. Every `_rag.db` rebuild requires a uvicorn restart for the cached connection to pick up the new file. The CLAUDE.md note now mentions this for `arxiv_index_rag.py` too.
- **`/health` HTTP status code** — unchanged from prior carry-overs. Still returns 200 even when a DB is broken.
- **Test extension** — the smoke suite covers `/arxiv/chunks` but the other three sources will each need analogous chunks tests when they ship in 2b/2c/2d. Mechanical addition.
