# Phase 1 retro — ArXiv read-only metadata API

**Date:** 2026-05-18
**Scope:** Phase 1 of the arxiv migration plan — add `/arxiv/papers` (list + detail + content) to the read-only API, mirroring openalex but with deliberate divergences (`:path` converter, `_lookup` helper, 503-vs-400 split). Build `papers_fts` over title + abstract. Source data copied from `local_wikipedia` on the same machine; ingest, chunking, and embeddings deferred.
**Status:** Shipped. ~0.1 s to build the FTS index on 1238 rows (DB 9.6 → 9.9 MB). Five commits — plan rewrite, indexer, API wiring, docs, and a 503-fix surfaced by post-implementation review. Smoke-tested across ~15 cases including the 503 and the old-style id path.

## Summary

The plan was already written when this phase started, but from `local_wikipedia/docs/` with macOS paths and a "laptop → remote LAN" architecture that never materialized. The phase began with a retarget of the plan to this repo's reality, then a pre-implementation audit against the existing `openalex.py` / `gutenberg.py` routers that surfaced six concrete divergences worth rolling into the plan before any code was written. Implementation followed in three commits — indexer, API wiring with curl smoke-tests, docs — and a post-implementation code review caught a missing-DB → 500 gap that the plan had implied would be 503. Fixed centrally in `_connect_ro` so all four routers benefit uniformly.

## What went well

- **Pre-implementation audit caught real bugs in the plan.** Six findings landed in the plan as edits before coding: the indexer's `INSERT … SELECT` vs `VALUES('rebuild')` claim ("mirrors exactly" wasn't true), missing `_lookup` helper, missing `Response` import note, `:path` framing as a new pattern not a mirror, 503-vs-400 reframed as a conscious divergence from openalex, and the `has_html=false` NULL-handling bug that would have been silently wrong. All six would have been ambiguous-or-wrong implementations otherwise.
- **Direct DB inspection instead of upstream code reading.** Asking "what indexes exist?" against `data/arxiv/arxiv.db` (rather than reading `local_wikipedia/arxiv/schema.py`) told us `idx_papers_primary_cat` and `idx_papers_submitted` already ship with the upstream file. No `CREATE INDEX` work in the indexer script, no speculative writes to local_wikipedia.
- **`:path` route ordering footgun caught at design time, not at smoke-test.** FastAPI/Starlette dispatches in definition order, and `{paper_id:path}` is greedy — `/papers/foo/content` would have been swallowed by the detail route if it came first. Putting `get_paper_content` before `get_paper` in the file fixed it, with an inline comment for the next reader.
- **Three-commit phase boundaries.** Indexer, API wiring (with smoke-tests), docs. Each commit is independently testable and reviewable; the indexer commit alone is safe to land, the API commit alone passes smoke tests once the indexer has run.
- **503 split implemented as a documented divergence.** The error model genuinely diverges from openalex — `_is_operational(e)` pattern-matches `"no such table"` and `"unable to open database file"` and returns 503; everything else stays 400. Smoke-tested by `DROP TABLE papers_fts` against the live API.
- **Post-implementation review found a bug worth fixing.** The "missing arxiv.db → 500 instead of 503" gap would have shipped silently. The fix (~15 lines in `_connect_ro`) generalized to all four routers, not just arxiv.
- **Param-binding hygiene held under 9 filter parameters.** Every user value through `?` placeholders; `from_clause` / `where` / `order` are interpolated only from hardcoded fragments and the `SORTS` dict.

## What went wrong / what I learned

- **Audit ≠ code review.** The pre-implementation audit compared the plan to the existing code, which caught plan/code mismatches. The post-implementation code review compared the new code to itself and to conventions — which caught the missing-DB → 500 bug. Different activities; both needed. I almost shipped without doing the second one.
- **The plan was massively out-of-date with reality.** It assumed a different repo (`rags` on macOS), different working directory layout, and a network-rsync deploy that doesn't apply when both repos are on the same Linux machine. ~13 surgical Edits to retarget. Would have been ~3 if the plan had been updated when the repo moved.
- **`html_content` is fetched eagerly by `_lookup`.** Mirroring gutenberg's all-columns pattern means the detail endpoint pulls the full HTML body even though `Paper` doesn't include it. At 12 / 1238 papers having content, invisible. At full corpus scale this becomes a per-detail-request perf problem. Deferred — flagged in the code review.
- **`download_status` has three states, not two.** `'downloaded'`, `'no_html'`, and `NULL`. The schema doesn't document the sentinel; it surfaced from inspecting actual data. `has_html=true` matches only `'downloaded'`; `has_html=false` correctly returns the other two via `IS NOT 'downloaded'`. Smoke-test math confirms: 12 + 1226 = 1238.
- **Connection-cache staleness still unaddressed.** Same item from the project retro — refresh-copy of `arxiv.db` followed by re-index doesn't take effect until uvicorn restart. The data-migration runbook in the plan and `CLAUDE.md` both say so explicitly now, but a real fix (inode-watch or weak-ref) is still deferred.
- **Background uvicorn lifecycle is still noisy.** `pkill` propagates to the backgrounded uvicorn task which reports exit code 144, surfaced as a "failed" task notification even though the kill was intentional. Cosmetic; I worked around it but it kept being confusing.

## Decisions worth remembering

- **`:path` converter with explicit route ordering, not URL-encoding.** Old-style arxiv ids like `cond-mat/0204015` work as `/arxiv/papers/cond-mat/0204015` directly. The cost is the route-ordering gotcha (content before detail), paid once and commented.
- **`download_status IS NOT 'downloaded'` for `has_html=false`.** SQLite's `IS` operator is null-safe; bare `!=` would silently drop the dominant `NULL` case. Smoke-tested explicitly.
- **503 for operational errors, 400 only for user errors.** Conscious divergence from `openalex.py`, which collapses both into 400. Per `WORK.md` §2.2; new routers should follow arxiv's pattern, not openalex's. The project retro already flagged this; this phase made the precedent concrete.
- **Centralized 503 translation in `_connect_ro`.** A single try/except in the connection helper means all four routers report 503 for missing/unreadable DB files, with the filename in the detail. Cost: importing `HTTPException` into `db.py` (mild layer mixing). Benefit: zero per-router boilerplate, uniform behavior.
- **No new indexes; the upstream ones ride along with the copy.** Indexes are physical parts of the SQLite file and survive `cp`. The indexer script's job is FTS5 only.
- **`local_wikipedia` as the source of truth, `cp` as the refresh mechanism.** No rsync, no SSH, no daemon. Both repos live on this machine; the data move is a one-line copy. Restart uvicorn after to flush the cache.

## Carry-over

- **Lazy-load `html_content`.** Refactor `_lookup` into a metadata-only fetcher; have `get_paper_content` do its own slim `SELECT html_content WHERE id = ?`. Not blocking until the corpus grows.
- **Phase 2: chunks + semantic search.** `paper_chunks`, `paper_chunks_fts`, `paper_chunks_vec`; hybrid (dense + sparse + RRF) retrieval; Ollama placement decision.
- **Phase 3: port arxiv ingest from `local_wikipedia`.** Per `WORK.md` §2.1, the ported `oai.py` parser should preserve `<keyname>` / `<forenames>` separately and capture `<affiliation>` — don't repeat the OpenAlex retro lesson of collapsing structured author fields at ingest. `arxiv/` can be removed from `local_wikipedia` at this point.
- **Connection-cache invalidation.** Still no fix. Probably a Phase 2 or Phase 3 item, when there's a refresh cadence that makes the cost real.
- **`/health` HTTP status.** Carry-over from the project retro, unchanged. `/health` returns 200 even when one DB is broken. Arxiv now 503s for its own missing-DB case, but `/health` doesn't reflect it in the status code.
- **Minimal `pytest` smoke suite.** Carry-over from the project retro. One happy-path per route, plus the FTS-syntax 400 and the relevance-without-q 400. Arxiv's `?has_html=false` and the missing-DB 503 are worth adding too.
