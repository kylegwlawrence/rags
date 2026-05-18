# Phase 3 retro — OpenAlex title+abstract FTS

**Date:** 2026-05-17
**Scope:** Add full-text search to `/openalex/works` via FTS5.
**Status:** Shipped. ~20 s to build the index on 268k rows; DB grew 737 → 890 MB. Endpoint smoke-tested across 7 cases.

## Summary

Added an external-content FTS5 virtual table `works_fts(title, abstract)` over the existing `works` table, indexed via a new one-shot script (`scripts/openalex_index_fts.py`), and wired a `q` parameter plus a `relevance` sort into `/openalex/works`. FTS5 syntax errors and missing-`q` + `sort=relevance` both return 400. The new sort defaults to `relevance` automatically whenever `q` is set.

## What went well

- **External-content FTS5 was the right call.** No data duplication (the index references rows in `works` by rowid), trivial to rebuild on each re-index, and no triggers needed because we drop and rebuild from scratch each run. Costs ~150 MB of index for a ~750 MB source.
- **Reused existing structures cleanly.** The `SORTS` dict picked up `relevance` as one more entry; `_row_to_work` didn't need to change. The list endpoint grew one branch for `from_clause` and one for the `q` clause — everything else stayed.
- **Bm25-ordering by default when `q` is set.** This makes the common case "I typed a search" return obviously-relevant results immediately, while still letting `sort=cited_by_count_desc` override for "most-cited graphene papers."
- **Param-binding hygiene held up under a 7-axis filter matrix.** No injection surface, no manual escaping, no string formatting of user input anywhere in the SQL path.

## What went wrong / what I learned

- **Ambiguous column names blew up the first smoke test.** With `works JOIN works_fts`, both tables expose `title` and `abstract`, so the bare `SELECT title, abstract` in the rows query failed at runtime with "ambiguous column name: title". Caught during the very first `?q=` request. Fix was one line (qualify everything with `works.`), but it's a reminder that adding a JOIN retroactively to a `SELECT col, col, col` shape requires a sweep of all references, not just the new clause. **Takeaway:** when introducing a JOIN, audit the SELECT list explicitly in the same edit, don't trust that "the smoke test will catch it" — most of the test runner output got eaten by `python -m json.tool` choking on the error payload, which made the failure mode briefly confusing.
- **Background process management got noisy.** Multiple cycles of `pkill ... && uvicorn ...` chained with backgrounding tripped over each other; one round of tests came back with no server running. Lesson: kill the server in its own foreground command, confirm, then start the new one separately. The "one-liner restart" wasn't actually saving time.
- **`q=` (empty string) currently returns an ugly SQLite error.** Technically correct, but `fts5: syntax error near ""` isn't a great UX. Left as-is because changing it would diverge from the `region=`/`venue=` convention where empty string is a valid (zero-result) filter — and FTS genuinely has no such concept.
- **`?q=foo` before the indexer is run returns "no such table: works_fts" as a 400.** That's an operational error masquerading as a user error. Considered detecting it and returning 500/503 with a friendlier message; rejected because pattern-matching SQLite error strings is fragile and the setup requirement is documented. If we ever get a third "FTS index not present" failure I'll revisit.

## Decisions worth remembering

- **`porter unicode61` tokenizer chain.** Porter stems English suffixes (`graphene` matches `graphenes`); `unicode61` folds diacritics and handles non-ASCII. Good default for academic abstracts. If this corpus grows non-English, revisit.
- **`relevance` was added as a sort enum value rather than as a flag.** Keeps the parameter shape uniform with the other sorts and lets the OpenAPI schema describe it. The runtime guard (`sort=='relevance' and q is None → 400`) handles the dependency.
- **No `VACUUM` after rebuild.** Cheaper to live with a few MB of free pages between rebuilds than to rewrite 890 MB every time the indexer runs.

## Carry-over

- The `_row_to_work` author-split limitation (~0.08% of works fragmented by credentialed suffixes) is unchanged from Phase 2. A re-download using OpenAlex's `authorships[].author.id` would fix it; not in this phase.
- Phase 4 candidates discussed earlier: Gutenberg full-text FTS (same shape as this work, but ~60 GB of content), `X-API-Key` middleware, OpenAlex incremental top-up. Nothing pulled in yet.
