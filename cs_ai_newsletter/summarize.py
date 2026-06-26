"""MAP step: turn one paper's abstract into a 1–2 sentence lay summary.

Each call is isolated (no shared context array), so the model's context is
effectively unloaded between papers — every summary is judged on its own
abstract only.
"""

from __future__ import annotations

from cs_ai_newsletter import llm
from cs_ai_newsletter.config import Config
from cs_ai_newsletter.source import Paper

_SYSTEM = (
    "You are a science writer who explains AI research to curious "
    "non-experts in plain, accurate language."
)

_PROMPT = """\
In 1–2 plain sentences, explain what this AI research paper does and why it \
matters. Write for a curious non-expert. Avoid jargon and acronyms. Output \
only the summary sentences — no preamble, no labels, no markdown, no quotes.

Title: {title}

Abstract: {abstract}"""


def summarize_paper(paper: Paper, *, config: Config) -> str:
    """Return a short lay summary of ``paper`` via one isolated Ollama call."""
    prompt = _PROMPT.format(title=paper.title, abstract=paper.abstract)
    return llm.generate(
        base_url=config.ollama_url,
        model=config.model,
        prompt=prompt,
        num_ctx=config.summary_num_ctx,
        temperature=0.2,
        system=_SYSTEM,
    )
