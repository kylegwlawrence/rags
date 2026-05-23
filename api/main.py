import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles

# Load .env before any router module reads its env vars at import time
# (e.g. api.routers.enwiki reads ENWIKI_REMOTE_URL when it's imported).
load_dotenv()

from api import db  # noqa: E402
from api.routers import (  # noqa: E402
    arxiv,
    enwiki,
    factbook,
    federal_register,
    github_readmes,
    gutenberg,
    openalex,
    python_docs,
    sec_edgar,
    simplewiki,
    wikihow,
    worldbank,
)

app = FastAPI(title="datasets API", version="0.1.0")
app.include_router(arxiv.router)
app.include_router(factbook.router)
app.include_router(openalex.router)
app.include_router(gutenberg.router)
app.include_router(simplewiki.router)
app.include_router(enwiki.router)
app.include_router(python_docs.router)
app.include_router(wikihow.router)
app.include_router(federal_register.router)
app.include_router(github_readmes.router)
app.include_router(sec_edgar.router)
app.include_router(worldbank.router)

app.mount("/ui", StaticFiles(directory="frontend", html=True), name="ui")


@app.get("/health")
def health(response: Response) -> dict:
    """Return per-database status by running `SELECT 1` against each connection.

    HTTP 503 if any database is broken; 200 otherwise. Body always carries
    per-DB detail so a probe can tell which one failed without a second call.
    """
    status: dict[str, str] = {}
    for name, opener in (
        ("arxiv", db.arxiv),
        ("arxiv_rag", db.arxiv_rag),
        ("factbook", db.factbook),
        ("factbook_rag", db.factbook_rag),
        ("openalex", db.openalex),
        ("openalex_rag", db.openalex_rag),
        ("gutenberg", db.gutenberg),
        ("gutenberg_rag", db.gutenberg_rag),
        ("simplewiki", db.simplewiki),
        ("simplewiki_rag", db.simplewiki_rag),
        ("pydocs", db.pydocs),
        ("pydocs_rag", db.pydocs_rag),
        ("wikihow", db.wikihow),
        ("wikihow_rag", db.wikihow_rag),
        ("federal_register", db.federal_register),
        ("federal_register_rag", db.federal_register_rag),
        ("github", db.github),
        ("github_rag", db.github_rag),
        ("sec_edgar", db.sec_edgar),
        ("sec_edgar_rag", db.sec_edgar_rag),
        ("worldbank", db.worldbank),
    ):
        try:
            opener().execute("SELECT 1").fetchone()
            status[name] = "ok"
        except Exception as e:
            status[name] = f"error: {e.__class__.__name__}: {e}"

    # enwiki lives on a remote host, not a local DB file — probe its /health
    # over HTTP with a tight timeout so this endpoint stays snappy. Treat
    # "not configured" as "skip" rather than "error" since the env var is
    # optional and a developer without Tailscale shouldn't get a red /health.
    if enwiki.REMOTE_URL:
        try:
            with httpx.Client(timeout=3.0) as client:
                r = client.get(f"{enwiki.REMOTE_URL}/health")
            r.raise_for_status()
            status["enwiki"] = "ok" if r.json().get("ok") else f"error: {r.json()}"
        except Exception as e:
            status["enwiki"] = f"error: {e.__class__.__name__}: {e}"
    else:
        status["enwiki"] = "skipped: ENWIKI_REMOTE_URL not set"

    ok = all(v == "ok" or v.startswith("skipped") for v in status.values())
    if not ok:
        response.status_code = 503
    return {"ok": ok, "databases": status}
