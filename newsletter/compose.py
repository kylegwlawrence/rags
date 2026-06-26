"""REDUCE step: consolidate the day's summaries into one themed newsletter.

A single Ollama call (large ``num_ctx``) groups the papers into a handful of
themes, writes a short intro digest, then lists every paper under its theme.
Returns GitHub-flavored markdown.
"""

from __future__ import annotations

from newsletter import llm
from newsletter.config import Config

_SYSTEM = (
    "You are the editor of a daily AI research newsletter for curious "
    "non-experts. You write clear, engaging GitHub-flavored markdown."
)

_PROMPT = """\
Below are {n} one-line summaries of today's new cs.AI arXiv papers, each with \
its title.

Write a short daily newsletter in GitHub-flavored markdown:
1. Start with a 2–3 sentence intro digest highlighting the day's biggest \
threads. Do not put a top-level heading above it.
2. Group the papers into 4–8 coherent themes. Give each theme a `## ` heading \
that names the theme.
3. Under each theme, list every relevant paper as a bullet: \
`- **Title** — one-line summary`.

Cover every paper exactly once. Do not invent papers, titles, or facts beyond \
what the summaries state. Output only the markdown — no preamble or sign-off.

Papers:
{papers}"""


def _format_papers(summaries: list[tuple[str, str]]) -> str:
    """Render the (title, summary) pairs as an indexed list for the prompt."""
    return "\n".join(
        f"{i}. **{title}** — {summary}"
        for i, (title, summary) in enumerate(summaries, 1)
    )


def compose_issue(summaries: list[tuple[str, str]], *, config: Config) -> str:
    """Compose the full newsletter markdown from ``(title, summary)`` pairs."""
    prompt = _PROMPT.format(
        n=len(summaries), papers=_format_papers(summaries))
    return llm.generate(
        base_url=config.ollama_url,
        model=config.model,
        prompt=prompt,
        num_ctx=config.compose_num_ctx,
        temperature=0.3,
        system=_SYSTEM,
    )
