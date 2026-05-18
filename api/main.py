from fastapi import FastAPI

from api import db
from api.routers import factbook, gutenberg, openalex

app = FastAPI(title="datasets API", version="0.1.0")
app.include_router(factbook.router)
app.include_router(openalex.router)
app.include_router(gutenberg.router)


@app.get("/health")
def health() -> dict:
    """Return per-database status by running `SELECT 1` against each connection."""
    status: dict[str, str] = {}
    for name, opener in (
        ("factbook", db.factbook),
        ("openalex", db.openalex),
        ("gutenberg", db.gutenberg),
    ):
        try:
            opener().execute("SELECT 1").fetchone()
            status[name] = "ok"
        except Exception as e:
            status[name] = f"error: {e.__class__.__name__}: {e}"
    return {"ok": all(v == "ok" for v in status.values()), "databases": status}
