"""Orchestrate select -> map (resumable) -> reduce -> store for one date."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from newsletter import compose, source, store, summarize
from newsletter.config import Config
from newsletter.llm import OllamaError

# Where the per-paper map loop emits progress; overridable for tests/quiet runs.
Logger = Callable[[str], None]


@dataclass(frozen=True)
class RunResult:
    """Outcome of one pipeline run, mirrored into the ``issues`` row."""

    run_date: str
    paper_count: int     # summaries available for the issue
    skipped_count: int   # junk source rows dropped at selection
    failed_count: int    # map calls that errored (left for a re-run)
    status: str          # complete | partial | failed | empty


def _split_intro(body_md: str) -> str:
    """Take the intro as the text before the first markdown heading."""
    intro_lines: list[str] = []
    for line in body_md.splitlines():
        if line.lstrip().startswith("#"):
            break
        intro_lines.append(line)
    return "\n".join(intro_lines).strip()


def run(
    run_date: str,
    *,
    config: Config,
    limit: int | None = None,
    log: Logger = print,
) -> RunResult:
    """Build (or resume) the newsletter issue for ``run_date``.

    Selects the day's papers, summarizes any not already done (resumable),
    composes the themed issue, and stores it. Map failures are logged and left
    missing so a later run retries only those; a compose failure leaves the
    issue ``partial`` with the summaries intact.

    Args:
        run_date: ``oai_datestamp`` the issue covers (``YYYY-MM-DD``).
        config: runtime settings.
        limit: if set, only summarize the first N selected papers (smoke test).
        log: progress sink.

    Returns:
        A :class:`RunResult` describing the run.
    """
    papers, skipped = source.select_papers(
        config.arxiv_db, config.category, run_date)
    log(f"{run_date}: selected {len(papers)} {config.category} papers "
        f"({skipped} skipped as withdrawn/placeholder)")
    if limit is not None:
        papers = papers[:limit]
        log(f"--limit {limit}: summarizing first {len(papers)} papers only")

    conn = store.connect_rw(config.newsletter_db)
    try:
        done = store.existing_summary_ids(conn, run_date)
        todo = [p for p in papers if p.id not in done]
        log(f"map: {len(done)} already summarized, {len(todo)} to do")

        failed = 0
        for i, paper in enumerate(todo, 1):
            try:
                text = summarize.summarize_paper(paper, config=config)
            except OllamaError as e:
                failed += 1
                log(f"  [{i}/{len(todo)}] FAILED {paper.id}: {e}")
                continue
            store.insert_summary(
                conn,
                paper_id=paper.id,
                run_date=run_date,
                title=paper.title,
                summary=text,
                model=config.model,
            )
            log(f"  [{i}/{len(todo)}] {paper.id} ok")

        rows = store.load_summaries(conn, run_date)
        paper_count = len(rows)

        # Nothing to write about (quiet weekend/holiday): record an empty issue
        # so the API has something to serve and the day is marked done.
        if paper_count == 0:
            body = (f"_No new {config.category} papers were announced on "
                    f"{run_date}._")
            store.upsert_issue(
                conn, run_date=run_date, paper_count=0,
                skipped_count=skipped, intro=body, body_md=body,
                model=config.model, status="empty")
            log(f"{run_date}: no papers — stored empty issue")
            return RunResult(run_date, 0, skipped, failed, "empty")

        log(f"reduce: composing issue from {paper_count} summaries")
        pairs = [(row["title"], row["summary"]) for row in rows]
        try:
            body_md = compose.compose_issue(pairs, config=config)
        except OllamaError as e:
            # Summaries are safe in the DB; a re-run will only redo compose.
            log(f"reduce FAILED: {e}")
            store.upsert_issue(
                conn, run_date=run_date, paper_count=paper_count,
                skipped_count=skipped, intro="", body_md="",
                model=config.model, status="partial")
            return RunResult(
                run_date, paper_count, skipped, failed, "partial")

        intro = _split_intro(body_md)
        status = "complete" if failed == 0 else "partial"
        store.upsert_issue(
            conn, run_date=run_date, paper_count=paper_count,
            skipped_count=skipped, intro=intro, body_md=body_md,
            model=config.model, status=status)
        log(f"{run_date}: stored issue ({status}, {paper_count} papers, "
            f"{failed} map failures)")
        return RunResult(run_date, paper_count, skipped, failed, status)
    finally:
        conn.close()
