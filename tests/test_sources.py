"""Tests for the source catalog: GET /sources and DATASOURCES.md sync.

`api/sources.py` is the single source of truth; these guard the endpoint shape
and that the generated DATASOURCES.md block hasn't drifted from it.
"""

from pathlib import Path

from api.sources import (
    MARKDOWN_BEGIN,
    MARKDOWN_END,
    SOURCES,
    render_markdown_section,
)

DOC_PATH = Path(__file__).resolve().parent.parent / "DATASOURCES.md"

_REQUIRED_KEYS = {"id", "name", "description", "timeframe", "chunks_endpoint"}


def test_sources_endpoint_shape(client):
    r = client.get("/sources")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == len(SOURCES)
    assert len(body["items"]) == len(SOURCES)
    for item in body["items"]:
        assert set(item) == _REQUIRED_KEYS, item
        assert item["chunks_endpoint"].startswith("/")
        assert item["chunks_endpoint"].endswith("/chunks")


def test_source_ids_unique():
    ids = [s["id"] for s in SOURCES]
    assert len(ids) == len(set(ids)), ids


def test_datasources_md_in_sync():
    """The generated block in DATASOURCES.md must match render_markdown_section().

    If this fails, run: python scripts/gen_datasources.py
    """
    text = DOC_PATH.read_text(encoding="utf-8")
    start = text.find(MARKDOWN_BEGIN)
    end = text.find(MARKDOWN_END)
    assert start != -1 and end != -1, "markers missing from DATASOURCES.md"
    block = text[start : end + len(MARKDOWN_END)]
    assert block == render_markdown_section(), (
        "DATASOURCES.md is out of date — run python scripts/gen_datasources.py"
    )
