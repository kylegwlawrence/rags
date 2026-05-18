# RULES.md

Hard rules for agents working in this project. Short by design — the *why* and the worked examples live in [`WORK.md`](./WORK.md).

If a rule below is ambiguous in your situation, read the matching section in `WORK.md` and follow the reasoning, not the literal wording.

---

## Order of precedence

When instructions conflict, follow this order (higher wins):

1. **Explicit user instructions in the current conversation.**
2. **This file (`RULES.md`).**
3. **[`WORK.md`](./WORK.md)** — the long form of these rules with rationale and examples.
4. **`CLAUDE.md`** — project-specific conventions and gotchas.
5. **General defaults.**

---

## Read these before doing anything

- **[`WORK.md`](./WORK.md)** — the working-rules charter. Required reading for any agent operating in this repo or any sibling project that adopts these conventions. Detailed but generalized — written to travel across projects.
- **`CLAUDE.md`** — project-specific instructions for this codebase (downloader scripts + read-only API). Overrides `WORK.md` where the two disagree, but only on this project.

---

## The non-negotiables

These are the rules where breaking them has caused real harm in past sessions. Each links to the full discussion in `WORK.md`.

1. **Ask clarifying questions before coding.** (`WORK.md` §1.1)
2. **Pause and confirm before committing to git.** (`WORK.md` §1.2)
3. **Never run destructive operations without explicit authorization.** (`WORK.md` §1.8)
4. **Parameterize all user input. No exceptions.** (`WORK.md` §2.3)
5. **Match HTTP / error-signal severity to who actually failed.** (`WORK.md` §2.2)
6. **Preserve foreign-system identity columns at ingest.** (`WORK.md` §2.1)
7. **Don't chain background restarts.** Kill, confirm, then start. (`WORK.md` §1.5)
8. **When you defer work, write it down with the reason.** (`WORK.md` §1.6)
9. **Match scope to what was requested.** No surprise refactors. (`WORK.md` §1.7)
10. **Update docs in the same change that broke them.** (`WORK.md` §4.5)

---

## When you're not sure

Default to the single rule in `WORK.md` §7:

> **When in doubt, slow down and ask.**
