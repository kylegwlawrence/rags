# Phase 2c retro — Factbook /chunks (and the pre-2c refactor)

**Date:** 2026-05-18
**Scope:** Phase 2c of the revised arxiv migration plan. Extract a shared `rag/indexer.py:run_indexer` skeleton (proven on 2a/2b's near-identical scripts) and use it to add factbook as a third source — the first non-flat data shape, where each country's nested JSON is rendered as section-tagged markdown chunked by `rag.chunker.chunk_markdown`. Includes the parametrization of the chunks tests across all sources.
**Status:** Code shipped (commits `d7f4027` + `806ebee` + `43d9c7d`). 50 of 261 countries embedded as a sample (1281 chunks, 8.6 MB DB, 15.7 min). 24/24 pytest pass including `[factbook]` variants of all six parametrized chunks tests. Full-corpus embed (~85 min remaining for the other 211 countries) deferred until the GPU question is answered.

## Summary

Two changes in one phase. First, a pre-2c refactor: with arxiv and openalex sharing ~170 lines of near-identical indexer code, the shape was obvious enough to extract one phase early (right before the third source landed). Net diff: `-496 / +416` lines across the four touched files, plus `+190` in the new `rag/indexer.py`. Per-source scripts shrank from ~170 to ~50 lines. Then factbook was the validation case: a genuinely-novel data shape (nested JSON, not flat columns) that exercised the abstraction. The new `chunk_fn` parameter on `run_indexer` and the new `chunk_markdown` chunker handled the section-aware chunking; the rest of the indexer was reuse.

## What went well

- **The refactor proved itself on the very next source.** With the abstraction in place, factbook landed in ~30 minutes of code (~70 lines of new extractor + ~50 lines of indexer + ~50 lines of router endpoint + 1 line in `RAG_SOURCES`). All 6 parametrized chunks tests covered it for free.
- **`chunk_fn` was the right extension point.** Plumbed once through `run_indexer`'s signature; defaults to `chunk_doc`; factbook passes `chunk_markdown`. No special-casing inside the shared skeleton. Future sources with different chunking shapes (line-based, paragraph-based, anything) plug in the same way.
- **`chunk_markdown` is reusable beyond factbook.** Phase 3's arxiv full-HTML→markdown work will use the same chunker; the `_HEADING_RE` regex matches both `## Geography` (factbook) and `## Introduction` (arxiv papers).
- **The JSON walker handled every factbook idiom on first try.** The `{"text": v}` wrapper, `{"text": v, "note": ...}` sibling pattern, deeply-nested keys, lists — all rendered to readable `"Path > To: value"` lines. Consistent with the existing `api/routers/factbook._flatten`.
- **Parametrized tests pay off immediately.** Adding factbook to `RAG_SOURCES` (one line) added 6 new test runs (one per parametrize × the same 6 test functions). Going from 2→3 sources didn't cost any test-code maintenance.
- **Pre-flight chunk estimate caught the embed-time miss early.** Before kicking off the full run, I queried `chunk_markdown(doc, 1600)` for every country and got 6616 chunks; at the known ~1 s/chunk Ollama rate that's ~2 hours, not the plan's "5–10 min". User picked a 50-country sample.
- **Mid-batch progress prints (Phase 2b carry-over) shined here.** Visible cadence "X seen / Y new / Z updated / W unchanged" every flush. No anxious waiting.

## What went wrong / what I learned

- **Plan's chunk-count estimate for factbook was off by ~1.7×.** Plan: `261 × 12 × ~1–2 ≈ ~4k chunks`. Reality: 6616 chunks. The per-country JSON is denser than the plan's eyeball — Economy alone often runs 5–8 chunks. **Takeaway:** "section count × an estimate" loses to "run the chunker on real data and count" every time.
- **Plan's embed-time estimate was off by ~12–24×.** Plan: 5–10 min. Reality: ~2 hours full corpus / 15.7 min for 47 new countries. Same "scale from a measured rate" lesson as the openalex retro — but here I had the openalex data point (1 s/chunk on CPU) and still didn't re-do the math. **Takeaway:** when a plan says "X minutes", multiply by the actual chunk count, not the plan's assumed chunk count.
- **No GPU detected; CPU is the bottleneck.** `nvidia-smi` isn't installed. nomic-embed-text:v1.5 on CPU runs at ~1 s per 1600-char chunk. Each Ollama HTTP call is essentially "n forward passes, sequentially". Direct probes confirmed: batch-of-32 took 80 s (with contention) and the model-load tax adds ~30–60 s once per cold Ollama session. **Takeaway:** the local Ollama / CPU embedding loop is the project's biggest perf lever. Worth investigating whether AMD or Apple Silicon GPU is available before the full factbook + remaining-openalex + gutenberg embed runs.
- **Two stale `pgrep` PIDs during waiter cleanup.** Same false-positive as Phase 2b (process gone from `/proc` but `pgrep` still listed it for a few seconds after kill). Doesn't break anything, just cosmetic. Adding a `cat /proc/$PID/cmdline` step is the reliable check.
- **The `n_new` / `n_updated` accounting fix from the 2b review now lives in the shared indexer.** Free correctness for every future source.

## Decisions worth remembering

- **Refactor *with* the next source in mind, not *after* it.** WORK.md §3.6 says wait for three before abstracting; in practice, with two examples plus a clear third on deck, extracting one phase early made the third source mechanical. The alternative (extract after 2c lands) would have meant rewriting the factbook code post-hoc.
- **`chunk_fn` parameter pattern.** Default for flat-text sources; markdown sources pass `chunk_markdown`; future sources can pass any callable matching the `(doc, *, chunk_size, overlap) -> list[dict]` signature. No conditional logic inside `run_indexer`.
- **One Doc per country, section labels via chunker.** docs_meta stays at 261 rows (one per real entity); chunks carry per-section labels via `chunk_markdown`'s output. version-hash is whole-JSON so re-extraction on JSON change re-embeds the whole country (correct semantics).
- **JSON walker emits `"Path > To: value"` lines.** Preserves the section/subsection hierarchy in the embed-time text so the dense vector encodes "this is in Geography > Coastline" rather than just the raw value. Helps query-time retrieval.
- **factbook indexer takes no `--limit` by default but accepts one.** Symmetric with arxiv (limit by `--limit`), unlike openalex (limit defaults to 5000). Three sources now exercise all three CLI shapes; the shared `run_indexer` doesn't care.

## Carry-over

- **Resume factbook to full corpus.** 211 countries remain. `python scripts/factbook_index_rag.py` (no `--limit`, no `--reset`) will pick up. Estimate ~85 min at current CPU rate.
- **Resume openalex.** 2380 docs remain (from Phase 2b's pause). Same one-liner; estimate ~25 min.
- **Phase 2d: gutenberg.** `--limit 100 --language en` per the plan. Full-book text means much larger chunks-per-doc than factbook; pre-flight a chunk count before committing to runtime.
- **GPU lever.** Highest-value perf investigation. If Ollama can be made to use a local GPU (Apple Silicon Metal works zero-config; AMD needs ROCm; NVIDIA needs CUDA driver), per-chunk time would drop from ~1 s to ~20–100 ms — 10–50× speedup. Worth checking `lspci` / `system_profiler` / etc. before Phase 2d.
- **Phase 3: arxiv OAI ingest port + render.py + full-HTML chunking.** `chunk_markdown` is already in place; just needs the HTML→markdown render step.
- **Connection-cache staleness, `/health` HTTP code, lazy-load `html_content`, OpenAlex authorship re-download** — unchanged from prior retros' carry-overs.
- **Indexer skeleton stability.** Three sources × different shapes (flat columns, sampled flat columns, nested JSON) handled by `run_indexer` without conditionals. The abstraction looks stable enough to take Phase 2d's `--limit`-sampled filesystem reads in stride.
