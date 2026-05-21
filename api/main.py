from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles

from api import db
from api.routers import (
    arxiv,
    factbook,
    gutenberg,
    openalex,
    python_docs,
    simplewiki,
    wikihow,
)

app = FastAPI(title="datasets API", version="0.1.0")
app.include_router(arxiv.router)
app.include_router(factbook.router)
app.include_router(openalex.router)
app.include_router(gutenberg.router)
app.include_router(simplewiki.router)
app.include_router(python_docs.router)
app.include_router(wikihow.router)

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
    ):
        try:
            opener().execute("SELECT 1").fetchone()
            status[name] = "ok"
        except Exception as e:
            status[name] = f"error: {e.__class__.__name__}: {e}"
    ok = all(v == "ok" for v in status.values())
    if not ok:
        response.status_code = 503
    return {"ok": ok, "databases": status}
