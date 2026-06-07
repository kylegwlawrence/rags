from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

# Load .env before any router module reads its env vars at import time
# (e.g. OLLAMA_URL for the embed routes).
load_dotenv()

from api import db  # noqa: E402
from api.routers import (  # noqa: E402
    arxiv,
    billstatus,
    ecfr,
    enwiki,
    eurlex,
    factbook,
    federal_register,
    geonames,
    github_readmes,
    gutenberg,
    justice_canada,
    openalex,
    openstax,
    pdfs,
    python_docs,
    sec_edgar,
    simplewiki,
    wikinews,
    worldbank,
)

app = FastAPI(title="datasets API", version="0.1.0")
app.include_router(arxiv.router)
app.include_router(factbook.router)
app.include_router(openalex.router)
app.include_router(gutenberg.router)
app.include_router(simplewiki.router)
app.include_router(wikinews.router)
app.include_router(enwiki.router)
app.include_router(python_docs.router)
app.include_router(federal_register.router)
app.include_router(github_readmes.router)
app.include_router(sec_edgar.router)
app.include_router(worldbank.router)
app.include_router(geonames.router)
app.include_router(billstatus.router)
app.include_router(eurlex.router)
app.include_router(ecfr.router)
app.include_router(openstax.router)
app.include_router(pdfs.router)
app.include_router(justice_canada.router)

app.mount("/ui", StaticFiles(directory="frontend", html=True), name="ui")

# OpenStax section images, copied to data/openstax/media/{repo}/ by the
# downloader and referenced from section bodies as Markdown image links.
# mkdir guards against StaticFiles erroring before the first download.
_OPENSTAX_MEDIA = Path("data/openstax/media")
_OPENSTAX_MEDIA.mkdir(parents=True, exist_ok=True)
app.mount("/openstax/media",
          StaticFiles(directory=str(_OPENSTAX_MEDIA)), name="openstax-media")


@app.middleware("http")
async def no_cache_ui_assets(request: Request, call_next):
    """Force revalidation on /ui/* so browsers always fetch the latest ES modules."""
    response = await call_next(request)
    if request.url.path.startswith("/ui/"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


@app.get("/health")
def health(response: Response) -> dict:
    """Return per-DB status (SELECT 1 probe). 503 if any DB is broken."""
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
        ("wikinews", db.wikinews),
        ("wikinews_rag", db.wikinews_rag),
        ("enwiki", db.enwiki),
        ("pydocs", db.pydocs),
        ("pydocs_rag", db.pydocs_rag),
        ("federal_register", db.federal_register),
        ("federal_register_rag", db.federal_register_rag),
        ("github", db.github),
        ("github_rag", db.github_rag),
        ("sec_edgar", db.sec_edgar),
        ("sec_edgar_rag", db.sec_edgar_rag),
        ("worldbank", db.worldbank),
        ("geonames", db.geonames),
        ("billstatus", db.billstatus),
        ("eurlex", db.eurlex),
        ("ecfr", db.ecfr),
        ("ecfr_rag", db.ecfr_rag),
        ("eurlex_rag", db.eurlex_rag),
        ("enwiki_rag", db.enwiki_rag),
        ("openstax", db.openstax),
        ("openstax_rag", db.openstax_rag),
        ("pdfs", db.pdfs),
        ("pdfs_rag", db.pdfs_rag),
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
