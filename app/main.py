# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging

from app.routers import cbb_routes
from app.routers import nfl_routes

from app.core.db import init_engine, close_engine
from app.core.persist import ensure_schema

# -------- logging --------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

# -------- app --------
app = FastAPI(
    title="Zach Sports Model API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
)

# -------- global error handler --------
@app.exception_handler(Exception)
async def _unhandled(request, exc):
    logger.exception("UNHANDLED ERROR: %s %s", request.method, request.url, exc_info=exc)
    return JSONResponse(status_code=500, content={"error": "internal_error"})

# -------- CORS --------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------- health --------
@app.get("/health")
async def health():
    return {"ok": True}

@app.on_event("startup")
async def _startup():
    eng = await init_engine()
    if eng:
        await ensure_schema()

@app.on_event("shutdown")
async def _shutdown():
    await close_engine()

# -------- register CBB routes --------
app.include_router(cbb_routes.router, prefix="/api/cbb")

# -------- register NFL routes --------
app.include_router(nfl_routes.router, prefix="/api/nfl")
