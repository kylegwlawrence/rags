"""Command-line entry point for the newsletter pipeline.

Usage:
    python -m cs_ai_newsletter.cli --oai-date 2026-06-25
    python -m cs_ai_newsletter.cli                       # defaults to yesterday (UTC)
    python -m cs_ai_newsletter.cli --oai-date 2026-06-25 --limit 5   # fast smoke test
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from cs_ai_newsletter import pipeline
from cs_ai_newsletter.config import Config


def _yesterday_utc() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m cs_ai_newsletter.cli",
        description="Generate the daily cs.AI arXiv newsletter for a date.",
    )
    parser.add_argument(
        "--oai-date",
        default=_yesterday_utc(),
        metavar="YYYY-MM-DD",
        help="oai_datestamp the issue covers (default: yesterday UTC).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Summarize only the first N papers (smoke test).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = Config.from_env()

    def log(msg: str) -> None:
        print(msg, flush=True)

    log(f"newsletter: model={config.model} arxiv_db={config.arxiv_db} "
        f"newsletter_db={config.newsletter_db}")
    result = pipeline.run(
        args.oai_date, config=config, limit=args.limit, log=log)

    # Non-zero exit when nothing usable was produced, so the DAG surfaces it.
    return 0 if result.status in ("complete", "empty") else 1


if __name__ == "__main__":
    sys.exit(main())
