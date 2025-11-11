# app/main.py
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import logging
import time
import os

# ------------ Router imports ------------
from app.routers import (
    cbb_routes,
    nfl_routes,
    nfl_props_routes,
    nfl_debug_routes,
    nhl_routes,
    cfb_routes,
)
import app.routers.form_routes as form_routes  # generic last-5 / form endpoints

# ------------ Logging ------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

# ------------ App ------------
app = FastAPI(
    title="Zach Sports Model API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
)


# ------------ Access log middleware ------------
class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            raise
        finally:
            dt = (time.perf_counter() - t0) * 1000
            logger.info(
                "ACCESS %s %s q=%s -> %s in %.1fms",
                request.method,
                request.url.path,
                request.url.query,
                status,
                dt,
            )
        return response


app.add_middleware(AccessLogMiddleware)

# ------------ CORS (open; can tighten later) ------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------ Global error handler ------------
@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    logger.exception("UNHANDLED ERROR: %s %s", request.method, request.url)
    return JSONResponse(status_code=500, content={"error": "internal_error"})


# ------------ Health & status ------------
@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/status")
async def status():
    return {
        "ok": True,
        "has_odds_key": bool(os.getenv("ODDS_API_KEY")),
        "regions": os.getenv("ODDS_REGIONS", "us"),
        "books": os.getenv("ODDS_BOOKMAKERS") or None,
    }


# ------------ Mount routers ------------
# Sport endpoints
app.include_router(cbb_routes.router, prefix="/api/cbb")
app.include_router(nfl_routes.router, prefix="/api/nfl")
app.include_router(nfl_props_routes.router, prefix="/api/nfl")
app.include_router(nfl_debug_routes.router, prefix="/api/nfl")
app.include_router(nhl_routes.router, prefix="/api/nhl")
app.include_router(cfb_routes.router, prefix="/api/cfb")

# Generic form / last5 endpoints (all sports: cbb, nfl, cfb, nhl)
app.include_router(form_routes.router, prefix="/api")



