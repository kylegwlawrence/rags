"""Minimal Ollama ``/api/generate`` client (stdlib only).

Deliberately tiny and dependency-free (``urllib`` + ``json``) so the package
carries its own LLM access and can move repos without pulling anything in.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

# Thinking-capable models (e.g. qwen3.x) can leak a <think>…</think> block into
# the response text; strip it so callers always get clean output.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class OllamaError(RuntimeError):
    """Raised when the Ollama request fails or returns no usable text."""


def _strip_think(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


def generate(
    *,
    base_url: str,
    model: str,
    prompt: str,
    num_ctx: int,
    temperature: float = 0.2,
    system: str | None = None,
    think: bool | None = False,
    timeout: float = 600.0,
) -> str:
    """Run one non-streaming generation and return the response text.

    Args:
        base_url: Ollama server base URL, e.g. ``http://localhost:11434``.
        model: model tag to run.
        prompt: the user prompt.
        num_ctx: context window size for this call.
        temperature: sampling temperature (low for factual summaries).
        system: optional system prompt.
        think: pass ``False`` to disable thinking on capable models; ``None``
            omits the field entirely (for models that reject it).
        timeout: socket timeout in seconds.

    Raises:
        OllamaError: on transport errors, non-200 status, or empty output.
    """
    url = base_url.rstrip("/") + "/api/generate"
    payload: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_ctx": num_ctx, "temperature": temperature},
    }
    if system:
        payload["system"] = system
    if think is not None:
        payload["think"] = think

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise OllamaError(f"Ollama HTTP {e.code} from {url}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise OllamaError(f"Ollama request to {url} failed: {e}") from e
    except json.JSONDecodeError as e:
        raise OllamaError(f"Ollama returned non-JSON from {url}: {e}") from e

    text = _strip_think(body.get("response", ""))
    if not text:
        raise OllamaError(f"Ollama returned empty response for model {model!r}")
    return text
