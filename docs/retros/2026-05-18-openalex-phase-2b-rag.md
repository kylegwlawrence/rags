# Phase 2b retro — OpenAlex /chunks (paused mid-build)

**Date:** 2026-05-18
**Scope:** Phase 2b of the revised arxiv migration plan. Port the shared `rag/` infrastructure (proven in Phase 2a on arxiv) to the OpenAlex source, with a `/openalex/chunks` hybrid-search endpoint over the top-5000 most-cited works. Same uniform schema; copy-then-refactor approach per `WORK.md` §3.6.
**Status:** Code shipped (commits `eb6f2d2` + `a10c376`); 18/18 pytest pass against the partial corpus. Embedding paused at 2620/5000 docs (3808 chunks, no orphans, `_meta` intact). User will resume manually with `python scripts/openalex_index_rag.py` (no `--reset`); the version-hash skip will pick up at doc 2621 and the final FTS rebuild restores sparse search.

## Summary

Mechanical port from Phase 2a — the indexer skeleton, db opener, router endpoint, and test patterns were all copied with substitutions. About 30 minutes from "start phase 2b" to "code committed" because nothing required new design work. The interesting bit was discovering that the plan's embed-runtime estimate ("~5–10 min") was off by ~6× — reality is ~60 min for 5k docs at local Ollama throughput. User paused at the halfway point; the persisted state is queryable in degraded (dense-only) mode until the next run completes the FTS rebuild.

## What went well

- **Two-file split (extractor + indexer) earned its keep on first reuse.** The openalex extractor is 30 lines and the only file with openalex-specific logic (W-id parsing, NULL-OR-NULL filter, content-hash version key). The indexer reused the arxiv skeleton verbatim with paths and the extractor import swapped — ~155 lines, near-mechanical edit.
- **Copy-then-refactor matched the moment.** Per `WORK.md` §3.6 (three similar lines before abstracting), two near-identical 30-line endpoints is one shy of the threshold. Phase 2c will be where the chunks endpoint + chunks tests + indexer skeleton genuinely want to be DRYed.
- **Periodic progress prints made the long run observable.** Added to the indexer based on the Phase 2a retro carry-over (silent 14-min run was confusing). Now: `19 / 45 / 69 / 92 / 117 / ...` — one line per batch flush, ~26 docs apart. Cheap, sufficient.
- **Stopping mid-run is safe by design.** Flush-per-batch transactions mean every committed batch is durable. User paused at 2620/5000 with zero orphans and a clean `_meta`. Resume path is just re-running the script; version-hash skip handles the already-done docs.
- **Phase 2a review-fix patterns inherited cleanly.** FK enforcement, 503-on-extension-load, CLI positive-int validation, shared `is_operational_error`, orphan-vec cleanup, `pytest.skip` on empty corpus — all worked on first try in the openalex code.
- **The smoke-test suite ran green against a partial corpus.** Both `/arxiv/chunks` and `/openalex/chunks` tests passed even with openalex_rag.db actively being written. SQLite WAL + read-only connection isolation did its job.

## What went wrong / what I learned

- **Embed-runtime estimate was off by ~6×.** Plan said `~5–10 min` for top-5k OpenAlex; reality is ~60 min. The arxiv reference was 14 min for 1238 papers (0.66 s/doc); openalex is similar per-doc but 4× more docs. The math was right there — I just didn't do it. **Takeaway:** scale runtime from a measured data point in the same pipeline, don't eyeball.
- **FTS rebuild at end-of-run only.** Mid-run pause leaves `chunks_fts` empty; sparse search returns 0 hits until the next run completes. Dense search still works (chunks_vec is populated up to the pause point), so the endpoint isn't broken — just degraded. Fixable two ways: (a) rebuild FTS inside `flush()`, (b) trap SIGTERM and rebuild before exit. Neither is blocking.
- **Empty-text docs inflate `n_new`.** If a doc has empty title AND empty abstract (passes the `IS NOT NULL` filter but yields no text), the indexer increments `n_new` before chunking, then `continue`s on the empty-chunks check. The doc never lands in `docs_meta`, so the next run counts it as new again. Accounting noise, not a correctness bug — `n_new` over-reports by however many empty-text docs the corpus has. Worth a one-line fix: move the `n_new` / `n_updated` increment to after the chunks-empty check.
- **`pgrep -f` matched a stale PID briefly after `pkill`.** PID's `/proc` entry was gone but `pgrep` still listed it — process-tree race condition. Used `cat /proc/PID/cmdline` to verify. Cosmetic, but if you trust `pgrep` exit status as "running" you can get confused.
- **Six new tests are duplicates of six existing tests.** Now have a `test_arxiv_chunks_*` block and an `test_openalex_chunks_*` block that differ only in URL prefix and `db.X_rag` opener. `WORK.md` §3.6 says wait for three before abstracting; we're at two — refactor at 2c. Resisted the temptation to parametrize now.

## Decisions worth remembering

- **`doc_id` for openalex is the short W-id** (after the last `/`), matching `/openalex/works/{short_id}`. The extractor parses it once; the rest of the pipeline treats it as opaque TEXT.
- **`version` is a 32-hex-char SHA-256 prefix of `(title, abstract)`** — OpenAlex's schema in this repo has no per-row `updated_at`, so content-hash is the only edit-detection signal. 128 bits is plenty for 268k rows.
- **`source_limit` stored in `_meta`** alongside `embed_model`, `embedding_dim`, `chunk_size`, `chunk_overlap`. Phase 2b is explicitly NOT a full-corpus embed; recording the limit makes the deliberate scope decision auditable.
- **Indexer's progress-print cadence is "every flush boundary"** (after each batch commit). One line per ~26 docs. Worth keeping in 2c/2d.
- **Mid-run stop is supported.** Documented in the indexer's docstring and in CLAUDE.md. Re-runs always pick up exactly where they left off via the version-hash skip.

## Carry-over

- **Resume the openalex embed.** 2380 of 5000 docs remaining. Single command: `python scripts/openalex_index_rag.py` (no `--reset`). FTS rebuild at the end brings sparse search back online.
- **Phase 2c (factbook).** This is the chunks-endpoint/tests/indexer refactor moment — three sources gives enough signal to extract a shared `_chunks_endpoint_skeleton` (or similar). Parametrize the chunks tests with `(source, opener)` tuples.
- **Move `n_new` / `n_updated` increment after the chunks-empty check** in both indexers. One-line cleanup to keep the accounting honest.
- **Incremental FTS rebuild during indexer.** Optional. Either rebuild inside `flush()` (more time per batch, always queryable) or trap SIGTERM and rebuild before exit. Decide based on whether mid-run pause becomes common.
- **Full-corpus OpenAlex embed.** 268k works × ~0.7 s/doc = ~50 hours. The top-5k sampling is the deliberate Phase 2b scope decision; full corpus is a "decide when there's a use case" item.
- **Connection-cache staleness, `/health` HTTP code, lazy-load `html_content`, OpenAlex authorship re-download** — unchanged from prior retros' carry-overs.
