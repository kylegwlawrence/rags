from fastapi import FastAPI

from api import db
from api.routers import arxiv, factbook, gutenberg, openalex

app = FastAPI(title="datasets API", version="0.1.0")
app.include_router(arxiv.router)
app.include_router(factbook.router)
app.include_router(openalex.router)
app.include_router(gutenberg.router)


@app.get("/health")
def health() -> dict:
    """Return per-database status by running `SELECT 1` against each connection."""
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
    ):
        try:
            opener().execute("SELECT 1").fetchone()
            status[name] = "ok"
        except Exception as e:
            status[name] = f"error: {e.__class__.__name__}: {e}"
    return {"ok": all(v == "ok" for v in status.values()), "databases": status}
