"""Frontend host for the datasets app.

Serves this repo's static single-page frontend (the ``frontend/`` directory)
under ``/ui/`` and reverse-proxies every other request to the datasets API
backend running on the pop-os machine over Tailscale.

This replaces the previous nginx setup, where nginx served a *separate* copy of
the frontend (``/var/www/datasets/frontend/``) and proxied the API. Now this
repo's ``frontend/`` is the canonical frontend and the whole thing runs as one
systemd service (see ``deploy/datasets-frontend.service``), mirroring the
slollillama service.

Run from the repo root:

    uvicorn frontend.server:app --host 100.117.77.103 --port 8002

The backend address is configurable via ``DATASETS_BACKEND_URL`` (default points
at the pop-os Tailscale IP).
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

# Backend API on pop-os (Tailscale IP for stability), matching the old nginx
# proxy_pass target. Overridable for testing.
BACKEND_URL = os.environ.get("DATASETS_BACKEND_URL", "http://100.83.81.43:8002")

# Static frontend lives next to this file; resolve absolutely so the service
# does not depend on the process working directory.
FRONTEND_DIR = Path(__file__).resolve().parent

# Hop-by-hop headers must not be forwarded across a proxy (RFC 7230 §6.1);
# they describe a single transport connection, not the end-to-end message.
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Hold one shared async client open for the lifetime of the service."""
    # 300 s timeout mirrors the old nginx proxy_read/send_timeout: some backend
    # calls (RAG embedding, large filings) are slow.
    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=300.0) as client:
        app.state.client = client
        yield


app = FastAPI(title="datasets frontend", lifespan=lifespan)


@app.middleware("http")
async def no_cache_ui_assets(request: Request, call_next):
    """Force revalidation on /ui/* so browsers always fetch the latest modules."""
    response = await call_next(request)
    if request.url.path.startswith("/ui/"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


@app.get("/")
def root() -> RedirectResponse:
    """Send the bare host to the UI (mirrors the old nginx `location = /`)."""
    return RedirectResponse(url="/ui/", status_code=302)


# Serve the canonical frontend. html=True returns index.html for "/ui/"; the
# app uses hash-based routing, so no SPA path fallback is required.
app.mount("/ui", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="ui")


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy(path: str, request: Request) -> Response:
    """Reverse-proxy any non-/ui request to the backend API on pop-os."""
    client: httpx.AsyncClient = request.app.state.client

    # Forward request headers minus hop-by-hop and Host (httpx sets Host from
    # the backend base_url).
    fwd_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP and k.lower() != "host"
    }
    body = await request.body()

    backend_req = client.build_request(
        request.method,
        "/" + path,
        params=request.query_params,
        headers=fwd_headers,
        content=body,
    )
    backend_resp = await client.send(backend_req, stream=True)

    # Strip hop-by-hop headers from the response too; Starlette recomputes
    # transfer framing for the client connection.
    resp_headers = {
        k: v
        for k, v in backend_resp.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }
    return StreamingResponse(
        backend_resp.aiter_raw(),
        status_code=backend_resp.status_code,
        headers=resp_headers,
        background=BackgroundTask(backend_resp.aclose),
    )
