# Phase 2d retro — Gutenberg /chunks

**Date:** 2026-05-18
**Scope:** Phase 2d of the revised arxiv migration plan. Add `/gutenberg/chunks` over the Project Gutenberg `.txt` corpus on disk. The fourth and final source covered by Phase 2 — and the first one that reads from disk rather than from a SQLite column.
**Status:** Code shipped (commits `9283ea6` + `88934ae`). 30/30 pytest pass. Full embed deferred per user: 3 books currently embedded as smoke validation (Declaration of Independence, Bill of Rights, JFK Inaugural). Pre-flight against `--limit 100` showed 32,512 chunks → ~9 hours on CPU; resume when convenient.

## Summary

By far the smallest of the four Phase 2 sub-phases. The abstractions built up across 2a/2b/2c (`run_indexer`, `add_chunks_route`, parametrized chunks tests, `content_hash`, `chunk_doc` default chunker, schema-mismatch auto-rebuild) reduced gutenberg to: an extractor with the file-system-specific bits (PG-banner stripper, encoding fallback, size+endpoint-hash fingerprint), a thin indexer wrapper, one `add_chunks_route` call, one `RAG_SOURCES` entry. ~30 minutes of code; the user deferred the embed itself. Net new lines across the eight touched files: ~210, of which the extractor alone is ~100.

## What went well

- **The shared abstractions held without modification.** `run_indexer` ate gutenberg's file-on-disk reading pattern unchanged — the per-source "variation" turned out to be entirely captured by the extractor closure. Same `chunk_doc` (no `chunk_fn` override needed; gutenberg `.txt` is paragraph-structured, not heading-structured).
- **`add_chunks_route` proved out.** Phase 2c's factory extraction made the gutenberg endpoint literally one call. 4-line diff in `api/routers/gutenberg.py`.
- **Parametrized tests scaled 3→4 sources for free.** One `pytest.param` line in `RAG_SOURCES`. Six new test runs (one per parametrized function) covering gutenberg. Total 30/30.
- **Pre-flight chunk count caught the embed-time miss before committing.** Real number: 100 books → 32,512 chunks (median 187/book, max 2978). At ~1 s/chunk that's 9 hours. User chose to defer the full embed and ship the endpoint with a 3-book validation corpus.
- **File-fingerprint version key is the right shape.** `{size}-{sha256_first_4kb_and_last_4kb}` avoids reading the whole file just to detect change, and sidesteps the unreliable-mtime problem on the gutenberg rsync mirror. Same pattern would work for any future filesystem-backed source.
- **Encoding fallback handled real-world variation.** UTF-8 → utf-8-sig → Latin-1 → utf-8-with-replace. Older PG files use Latin-1; the smoke test docs decoded cleanly.

## What went wrong / what I learned

- **My napkin embed-time math was off by ~20× from a quick eyeball.** I initially thought ~30 min for 100 books. Pre-flight showed 9 hours. The median English book is much longer than I had in my head; a 187-chunk median × 100 books = 18,700 chunks just from the medians, ignoring the long-tail books. **Takeaway:** before estimating an embed run, do the chunk-count pre-flight on real data. Eyeballed multiplications keep being wrong.
- **PG banner regex only catches the modern `*** START/END OF (THIS|THE) PROJECT GUTENBERG EBOOK ... ***` format.** Older files (pre-1995) and "Small Print" sections slip through and end up embedded. Not blocking — the modern PG corpus is mostly in the canonical format — but for any old IDs in the corpus, the chunks contain meta-text. Could add more regexes if it becomes a problem.
- **The user has now deferred two embeds (openalex 2b at 2620/5000, factbook 2c at 50/261, gutenberg 2d at 3/100).** Pattern: any embed estimated above an hour gets stopped. The right system-level answer is GPU acceleration — see carry-over.

## Decisions worth remembering

- **File-fingerprint version key for filesystem-backed sources.** `f"{size_bytes}-{sha256_first_4kb_and_last_4kb}"` — fast, stable, doesn't read the whole file. Use this shape for any future source where the document body lives on disk.
- **`chunk_size=2000` for Gutenberg** (vs 1600 default). Plain-text prose is denser than abstracts; the extra 25% per chunk produced fewer over-tiny chunks without overrunning nomic's effective context. No measured retrieval quality difference yet but worth keeping as the per-source default.
- **`--language` + `--limit` is the right CLI shape for filesystem-backed sources** where the corpus is large and clearly bucketed. Made the `--limit 100 --language en` default actionable without imposing a fixed scope.
- **Defer-the-embed is a legitimate "ship" state.** Endpoint exists, smoke-tests pass, schema is correct, 3 docs validate the pipeline. The corpus can grow at the user's discretion via the same `python scripts/gutenberg_index_rag.py --limit N` command.

## Carry-over

- **Resume gutenberg embed at any scope.** `python scripts/gutenberg_index_rag.py --limit N` picks up incrementally. Or run without `--limit` (default 100).
- **Resume openalex (2620/5000)** and **factbook (50/261)**. Both unchanged from prior retros' carry-overs.
- **GPU lever.** Highest-value perf investigation, increasingly relevant: three deferred embeds aggregate to many hours on CPU. Whether AMD ROCm / Apple Metal / NVIDIA CUDA is available locally is the load-bearing question for finishing them all.
- **Phase 2 is done.** Four sources × hybrid /chunks endpoint. The shared `rag/` infrastructure handled three flat-text sources, a JSON-tree source, and a filesystem source without internal conditionals. Ready for Phase 3.
- **Phase 3: arxiv OAI ingest port** from `local_wikipedia` + `render.py` + full-HTML chunking. `chunk_markdown` is already in place; `chunk_fn=chunk_markdown` would activate it for arxiv once render.py produces markdown.
- **Older PG banner variants** — only fix if a corpus expansion hits files that fail to extract. Not blocking.
- **Project retro** — at the end of Phase 2 it's worth writing a Phase-2-overall retro distilling the four sub-phase patterns into permanent rules for `WORK.md`. Likely items: pre-flight chunk count before estimating, refactor-one-phase-early at the §3.6 threshold, file-fingerprint vs content-hash for version keys, defer-the-embed as a ship state.
- **Connection-cache invalidation, `/health` HTTP code, lazy-load `html_content`** — unchanged from prior retros' carry-overs.
