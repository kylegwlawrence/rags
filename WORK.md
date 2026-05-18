# WORK.md

How agents should work on this project — and on others. Distilled from retros where things went well, and from places where they didn't. Generalized so it travels.

Each rule has the same shape:

> **Rule.** One line, imperative.
>
> **Why:** the reason it exists — usually a past failure or a pattern that repeatedly paid off.
>
> **Example:** a concrete success/failure case so the rule survives translation to a new stack.

Use these as defaults, not laws. When a rule conflicts with the situation, follow the *why* — the rule is a compressed form of the reason, and the reason is what's load-bearing.

---

## 1. Process — how to work

### 1.1 Ask clarifying questions before starting a coding task.

**Why:** Agents are fast at producing code and slow at undoing it. Ambiguity in the request compounds into the wrong architecture; a 30-second question prevents a 30-minute rewrite. The user is the cheapest source of truth available.

**Example:** User says "add search to the works endpoint." Before coding, ask: prefix matching or full-text? Title only or title+abstract? Should missing input return zero results or an error? Each answer rules out a different implementation.

### 1.2 Pause and confirm before committing to git.

**Why:** A commit is a public artifact in the local history. Once made, undoing it requires a destructive operation or a confusing revert commit. Confirming costs one sentence; an unwanted commit costs a `git reset` and an explanation.

**Example:** After finishing a feature, say "I'm ready to commit these changes — here's the message I'd use. OK to go?" Don't assume completion implies authorization.

### 1.3 Ship end-to-end in phases. No half-finished branches.

**Why:** A phase that compiles, runs, and exposes its functionality is something you can stop on. A phase that's 80% done is something you have to remember. Discrete, complete phases also give you natural retro boundaries.

**Example:** When adding a normalization pass + an API filter that uses it, complete both before moving on. Don't land the normalizer and leave the filter for "next session" — by next session you'll have lost the live context.

### 1.4 Write the retro at the time of the phase, not at the end of the project.

**Why:** Reconstructive retros are guesses. The interesting failure modes — the wrong direction you backed out of, the lemma you almost shipped — are gone within hours. Even a three-bullet retro written today beats a polished one written next month.

**Example:** If a phase took two `pkill` cycles to get the dev server stable, that detail belongs in the phase retro. It's the kind of thing you'll keep doing if you don't write it down.

### 1.5 Manage long-running processes deliberately. Don't chain background restarts.

**Why:** `pkill X && start X &` looks atomic and is not. The kill races the start; the new process inherits the old port-in-TIME_WAIT; the backgrounded shell makes the error invisible. Multiple retros document the same lesson.

**Example:** Bad — `pkill -f uvicorn && uvicorn api.main:app &`. Good — stop the server in its own command, confirm it's dead, then start the new one in its own command. The "one-liner restart" wasn't actually saving time.

### 1.6 When you defer work, write down what and why.

**Why:** Deferred work that isn't recorded is forgotten work. The retro's carry-over list is the only thing that prevents "we'll do that next time" from becoming a permanent invisible debt.

**Example:** "OpenAlex authorship re-download — deferred. Proper fix requires capturing `authorships[].author.id`. Worth doing next time the OpenAlex DB is refreshed for any other reason." That sentence carries forward; "we'll fix that later" does not.

### 1.7 Match the scope of your actions to the scope that was requested.

**Why:** A bug fix doesn't need surrounding cleanup. A one-shot script doesn't need a helper module. Expanding scope without permission burns trust and inflates diffs. The user can always ask for more; they cannot easily ask for less.

**Example:** User asks to fix a typo in an error message. Don't refactor the surrounding error-handling block, even if it's ugly. Note the ugliness in a retro carry-over if it bothers you.

### 1.8 Treat destructive operations as requiring explicit authorization.

**Why:** `rm`, `git reset --hard`, `DROP TABLE`, `force push` — these can't be undone by a follow-up command. The cost of asking is one sentence. The cost of an unwanted deletion is irrecoverable work and a damaged session.

**Example:** Before running `git reset --hard origin/main` to drop local changes, confirm with the user that the local changes are truly disposable. Don't assume "the diff looks like garbage" means "the user wants it gone."

---

## 2. Engineering principles

### 2.1 Preserve foreign-system identity columns at ingest, even if you don't think you'll need them.

**Why:** Stripping later is easy. Reconstructing later is expensive or impossible. Foreign IDs are the only stable join key when you need to re-fetch, re-link, or backfill. Display names, descriptions, and human-readable fields are derived data; the IDs are the originals.

**Example:** A downloader stored author names as a `", "`-joined string. Author filtering then required a normalization pass that fragmented credentialed suffixes (`"Smith, Jr."`) into phantom rows. The proper fix would have been keeping `authorships[].author.id` from the source API at ingest time. Strip later, never the other way around.

### 2.2 Match HTTP status codes (and equivalent error signals) to who actually failed.

**Why:** 4xx means the caller asked for something invalid; 5xx means the server is broken. Conflating them makes remote debugging hard and breaks monitoring. If a precondition is operational (a file isn't present, an index hasn't been built, a downstream is down), a 503 or 500 is correct — a 400 hides the problem from anyone watching status codes.

**Example:** `?q=foo` returning a 400 because the FTS index hasn't been built yet is wrong: the user's query is valid; the server isn't ready. The correct response is 503 with a clear message. A health probe that only checks status codes won't see a 400, but it will see a 503.

### 2.3 Always parameterize user input. No exceptions for "trusted" inputs.

**Why:** Param binding is one keystroke more than string formatting, and the moment you bypass it for "just this one place" you create an injection surface that's invisible at review time. The discipline only works if it's absolute.

**Example:** Even when accepting FTS5 query syntax (which the user *expects* to be interpreted), pass it as a bound parameter and let the engine parse it. Don't pre-escape, don't interpolate. Same rule for shell calls: use argv arrays, never string-formatted commands.

### 2.4 When you add a JOIN, audit every `SELECT` list and `WHERE` clause for newly-ambiguous columns in the same edit.

**Why:** Adding a JOIN retroactively to a query that selects bare column names is one of the most reliable ways to ship a runtime bug. Both tables expose `title`? The bare `SELECT title` becomes ambiguous, and the failure only shows up on the first request that exercises the JOIN.

**Example:** Adding `JOIN works_fts ON ...` to a query that previously did `SELECT title, abstract FROM works` requires changing the SELECT to `SELECT works.title, works.abstract` in the same edit. Don't trust the smoke test to catch it — most test output gets eaten by downstream pretty-printers choking on the error.

### 2.5 Document staleness modes of any cached state.

**Why:** Caches are correct until something writes underneath them. Module-level connections, in-memory lookup tables, file handles held across reloads — all of these have a "what happens when the underlying thing changes" answer that future-you will need.

**Example:** Read-only DB connections cached at module level are safe across threads, but if a downloader rewrites the file while the API is running, the cached handle is stale. Document this in the file that holds the cache, so "why do I need to restart after reindex?" has an answer.

### 2.6 One happy-path automated test per route is worth more than zero.

**Why:** Refactor confidence scales with test coverage. Smoke-testing by hand catches the *current* matrix; an automated test catches regressions you didn't think to look for. The first test is the expensive one — adding the second is trivial.

**Example:** A `pytest` file that hits each list and detail endpoint with one valid request, asserting status 200 and a non-empty response, would have caught the ambiguous-column-name bug at import time. ~50 lines, positive ROI from day one of a project this size.

### 2.7 Make every write operation safely re-runnable.

**Why:** Re-runnability removes the "delete and start over" branch from every runbook. `INSERT OR REPLACE`, `CREATE INDEX IF NOT EXISTS`, idempotent file copies, mkdir-p — these convert "scripts that broke halfway through" from a recovery problem to a re-run problem.

**Example:** A downloader that crashes 60% through a paginated fetch should resume from where it left off (or re-process safely) on the next run. Never write a script that requires deleting its output to start over.

### 2.8 Don't add error handling for cases that can't happen.

**Why:** Defensive code for impossible inputs is noise. It makes real error handling harder to find and obscures the actual contract. Trust internal callers and framework guarantees; validate only at system boundaries (user input, external APIs, file/network reads).

**Example:** A function that's only called with a validated enum value doesn't need to handle "what if the value is `None`?" — its caller has already guaranteed otherwise. The right place for that check is at the boundary where the value entered the system.

---

## 3. Decision-making patterns

### 3.1 Pick the simplest tool that meets the constraint. Frameworks are paid in cognitive load.

**Why:** Every abstraction (ORM, migration framework, plugin system, config layer) has a learning cost and an opinion. If the constraints don't demand it, the abstraction will fight you. The simplest thing that works almost always wins for projects below a certain scale, and the scale is usually higher than you'd expect.

**Example:** File-per-source read-only SQLite beat Postgres for a personal dataset collection: no daemon, no auth surface, backup is `cp`. Inline SQL beat an ORM because there were no migrations to manage. Neither choice would scale to a multi-writer production system; both were correct for the actual constraints.

### 3.2 Prefer independent units over shared libraries when a new unit shouldn't touch existing ones.

**Why:** A "shared library" only earns its keep when callers benefit from a common change. If every new unit is independent — a new script, a new endpoint, a new ingester — the shared library is a coordination point that only adds blast radius. Repetition of small boilerplate (10 lines of setup) is much cheaper than a wrong abstraction.

**Example:** Per-source downloader scripts with no shared base class meant adding a new source didn't touch the others. The cost was repeating SQLite-open boilerplate; the benefit was zero risk of breaking the existing sources when working on a new one.

### 3.3 Generic wrappers earn their keep when the contract is genuinely uniform.

**Why:** Speculative generics that "might be useful" become dead weight. But when every endpoint returns the same shape (items + total + paging), a generic wrapper makes adding the next endpoint free. The test is: would I copy-paste this if I didn't generalize it?

**Example:** A `Page[T]` response model used by every list endpoint paid off the first time a new filter was added — the response contract didn't move. If only one endpoint had ever needed pagination, the generic would have been overhead.

### 3.4 Define the architectural boundary by who's allowed to write.

**Why:** Read-only consumers can share state safely with much less ceremony than writers. If you can split "the thing that builds the data" from "the thing that serves it," the serving layer gets to be much simpler — no migrations, no schema management, no lock contention.

**Example:** Downloaders/indexers own all schema and `CREATE INDEX` operations. The API opens DBs read-only and can't corrupt anything. New filters that need new indexes get added to the downloader, not the API.

### 3.5 Match security to the threat model. Don't pay for security you don't need.

**Why:** Every auth layer (JWT, API keys, rate limits) has implementation cost and operational cost. If the deployment context already provides the trust boundary (private network, ACLs, OS-level permissions), adding application-level auth is duplicative and adds attack surface of its own.

**Example:** A read-only API on a private Tailscale network doesn't need JWT — the ACL is the auth. If it ever gets exposed publicly, the auth story becomes its own project, not a retrofit.

### 3.6 Three similar lines is better than a premature abstraction.

**Why:** Abstractions encode assumptions. With one example, you have no idea which parts are essential; with two, you're guessing; with three, the shape starts to be visible. Extracting too early bakes in the wrong axis of variation, and the next caller has to fight the abstraction or work around it.

**Example:** Three routers each building a `WHERE` clause from a list of filters — slightly different filter shapes, slightly different params. Extracting a `build_where()` helper after the first one would have constrained the second and third. After all three exist, the common pattern is obvious and the abstraction (if needed) is cheap.

### 3.7 Don't design for hypothetical future requirements.

**Why:** You will almost always be wrong about what the future needs. Building for it adds code that has to be maintained without delivering value. Real requirements always look different from imagined ones.

**Example:** Don't add a plugin system "in case we need to add more datasources" before you have the second datasource. Add the second one and see what actually wanted to be shared.

---

## 4. Documentation conventions

### 4.1 Split docs by audience, not by topic.

**Why:** A single `README.md` trying to serve users, contributors, and tooling/agents serves none of them well. Different audiences need different surface area: the user wants to run the thing, the contributor wants to extend it, the agent needs the gotchas that aren't derivable from the code.

**Example:** This project's split — `README.md` describes the public API surface for users; `CLAUDE.md` holds per-script gotchas, port reservations, and the index-creation convention for agents. Both stay short because neither tries to do the other's job.

### 4.2 Document gotchas in the file that lives closest to the gotcha.

**Why:** A docstring at the top of a script is read by anyone who opens the script. A general `NOTES.md` is read by nobody. Locality matters.

**Example:** "This rsync requires SSH alias `pop-os` to resolve" belongs in a header comment in the script, not buried in a project-level FAQ. "Cached connections become stale after a rewrite" belongs in the file that owns the cache.

### 4.3 Each retro has a `Carry-over` section. If there's nothing in it, write that explicitly.

**Why:** The presence of an empty carry-over is a positive signal ("nothing deferred"). An absent carry-over section is ambiguous — was nothing deferred, or was carry-over forgotten? Make the difference legible.

**Example:** Use the structure: Summary → What went well → What went wrong / what I learned → Decisions worth remembering → Carry-over. The last section can be one line ("Nothing carried over") or twelve, but it's always there.

### 4.4 Retro structure should make grep work.

**Why:** Retros are read in two modes: end-to-end shortly after writing, and grep-style months later when something feels familiar. Consistent headings make the second mode fast.

**Example:** Always include `**Date:**`, `**Scope:**`, `**Status:**` at the top of every retro. `grep -l "OpenAlex" docs/retros/` should reliably find every retro that touched OpenAlex.

### 4.5 Update docs in the same change that broke them.

**Why:** Docs that drift behind code are worse than no docs — they actively mislead. The cheapest moment to update a doc is when you're already thinking about the thing it describes; every other moment is more expensive and more error-prone.

**Example:** Adding a new endpoint? Update `CLAUDE.md`'s endpoint list in the same commit. Removing a script? Remove its mention. Don't ship a "I'll update docs next" commit.

### 4.6 Write `CLAUDE.md` (or its equivalent) for the next agent, not for yourself.

**Why:** You have context; the next agent doesn't. Things that feel obvious now ("of course port 8002, the other apps use 8000 and 8001") aren't obvious from a cold start. The test is: could a competent agent who has never seen this repo do the right thing armed only with this file?

**Example:** "Port 8002 reserved because other local uvicorn apps occupy 8000 and 8001" tells the next agent why, so they don't switch the port and break the convention. "Port 8002" alone doesn't.

---

## 5. Anti-patterns to avoid

These are negations of the rules above, written out so they're easy to recognize when you find yourself doing one:

- **Coding before clarifying.** Writing code based on guessed requirements when a question would have settled it.
- **Auto-committing.** Treating "I finished the change" as authorization to commit.
- **Stacked half-phases.** Leaving phase N at 80% to start phase N+1.
- **Reconstructive retros.** Writing a retro weeks later, guessing at what mattered.
- **Backgrounded restarts.** `pkill && start &` chains, where failures are invisible.
- **Silent deferral.** "We'll do this later" with no record of what or why.
- **Scope creep on small tasks.** Refactoring around a one-line fix.
- **Premature abstraction.** Extracting a helper after one example.
- **Speculative frameworks.** Building plugin systems before the second plugin exists.
- **Operational errors as user errors.** 400 for "the index isn't built."
- **Selective parameterization.** String-formatted SQL/shell for "trusted" inputs.
- **JOIN without column audit.** Adding a JOIN and trusting the smoke test to catch ambiguous columns.
- **Hidden caches.** Module-level state without a documented staleness mode.
- **Catch-everything error handling.** `try/except` around things that can't fail.
- **One-doc-fits-all.** README that's simultaneously a user guide, contributor guide, and agent guide.
- **Doc drift.** Shipping code changes without the corresponding doc change.

---

## 6. Project-specific overrides

Every project will have at least one rule above that doesn't apply. The pattern is:

1. The project's `CLAUDE.md` (or equivalent) names the override.
2. The override states *which* rule is suspended and *why*.
3. If the override is permanent, it lives in `CLAUDE.md`. If it's task-specific, it lives in the conversation.

**Example:** A throwaway prototype might explicitly suspend rule 2.6 (one test per route) because nothing will be refactored. That's fine — but it should be stated, so the next agent knows the rule was waived deliberately, not forgotten.

---

## 7. The single rule that subsumes the rest

> **When in doubt, slow down and ask.**

Every rule above is a compressed form of "someone moved too fast and paid for it." The cost of pausing to confirm is always low. The cost of an unwanted action — a destructive commit, a wrong abstraction, a half-finished phase — is always higher than it looks. If a rule above doesn't fit the situation, the fallback is the same: ask the user, then proceed.
