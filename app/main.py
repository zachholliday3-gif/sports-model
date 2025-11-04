# app/main.py
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import List, Optional
import logging

from app.models.types import GameLite, Projection, MatchupDetail
from app.services.espn import get_games_for_date, extract_game_lite, extract_matchup_detail
from app.services.projections import project_cbb_1h

# ----------------- Logging Setup -----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

# ----------------- FastAPI App -----------------
app = FastAPI(
    title="Zach Sports Model API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
)

# ----------------- Global Error Handler -----------------
@app.exception_handler(Exception)
async def _unhandled(request, exc):
    logger.exception("UNHANDLED ERROR: %s %s", request.method, request.url, exc_info=exc)
    return JSONResponse(status_code=500, content={"error": "internal_error"})

# ----------------- CORS -----------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"],
)

# ----------------- Health -----------------
@app.get("/health")
async def health():
    return {"ok": True}

# ----------------- Schedule -----------------
@app.get("/api/cbb/schedule", response_model=List[GameLite])
async def cbb_schedule(date: Optional[str] = None):
    """
    Safe schedule endpoint:
    - If date missing → NY "today"
    - If date invalid → 400
    - If ESPN fails → []
    """
    try:
        games = await get_games_for_date(date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("schedule failed for date=%s: %s", date, e)
        return []

    logger.info("schedule: %d games for date=%s", len(games), date)
    return [extract_game_lite(ev) for ev in games]

# ----------------- Projections -----------------
@app.get("/api/cbb/projections", response_model=List[Projection])
async def cbb_projections(date: Optional[str] = None, scope: str = "1H"):
    """
    Returns projections for a date (scope: 1H or FG).
    """
    if scope not in ("1H", "FG"):
        raise HTTPException(400, "scope must be '1H' or 'FG'")

    try:
        games = await get_games_for_date(date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("projections failed for date=%s: %s", date, e)
        return []

    logger.info("projections: %d games for date=%s", len(games), date)

    projections: List[Projection] = []
    for ev in games:
        lite = extract_game_lite(ev)

        if scope == "1H":
            model = project_cbb_1h(lite["homeTeam"], lite["awayTeam"])
        else:
            base = project_cbb_1h(lite["homeTeam"], lite["awayTeam"])
            model = {
                "projTotal": round(base["projTotal"] * 2.02, 1),
                "projSpreadHome": round(base["projSpreadHome"] * 2.0, 1),
                "confidence": base["confidence"],
            }

        projections.append({"gameId": lite["gameId"], "scope": scope, **model})

    return projections

# ----------------- Single Matchup -----------------
@app.get("/api/cbb/matchups/{gameId}", response_model=MatchupDetail)
async def cbb_matchup(gameId: str, scope: str = "1H"):
    """
    Returns matchup detail + model numbers for one game.
    """
    try:
        games = await get_games_for_date()
    except Exception as e:
        logger.exception("matchup failed for gameId=%s: %s", gameId, e)
        raise HTTPException(404, "Could not load matchups")

    ev = next((g for g in games if g.get("id") == gameId), None)
    if not ev:
        raise HTTPException(404, "Game not found")

    base = extract_matchup_detail(ev)

    if scope == "1H":
        model = project_cbb_1h(base["homeTeam"], base["awayTeam"])
    else:
        base_m = project_cbb_1h(base["homeTeam"], base["awayTeam"])
        model = {
            "projTotal": round(base_m["projTotal"] * 2.02, 1),
            "projSpreadHome": round(base_m["projSpreadHome"] * 2.0, 1),
            "confidence": base_m["confidence"],
        }

    return {**base, "notes": None, "model": {"gameId": base["gameId"], "scope": scope, **model}}

# ----------------- Slate (Schedule + Model) -----------------
@app.get("/api/cbb/slate")
async def cbb_slate(
    date: Optional[str] = None,
    scope: str = Query("1H", pattern="^(1H|FG)$")
):
    """
    Returns schedule rows with model numbers combined.
    """
    try:
        games = await get_games_for_date(date)
    except Exception as e:
        logger.exception("slate failed for date=%s: %s", date, e)
        return []

    logger.info("slate: %d games for date=%s", len(games), date)

    slate_rows = []
    for ev in games:
        lite = extract_game_lite(ev)

        if scope == "1H":
            model = project_cbb_1h(lite["homeTeam"], lite["awayTeam"])
        else:
            base = project_cbb_1h(lite["homeTeam"], lite["awayTeam"])
            model = {
                "projTotal": round(base["projTotal"] * 2.02, 1),
                "projSpreadHome": round(base["projSpreadHome"] * 2.0, 1),
                "confidence": base["confidence"],
            }

        slate_rows.append({**lite, "model": {"scope": scope, **model}})
    return slate_rows

# ----------------- Mock Slate (No ESPN Required) -----------------
@app.get("/api/cbb/mock_slate")
async def cbb_mock_slate(scope: str = "1H"):
    """
    Returns 2 fake matchups with model projections — useful for testing
    even if ESPN is down or schedule is empty.
    """
    sample_games = [
        {"gameId": "M1", "homeTeam": "Purdue Boilermakers", "awayTeam": "Evansville Purple Aces"},
        {"gameId": "M2", "homeTeam": "Duke Blue Devils", "awayTeam": "Michigan State Spartans"},
    ]

    out = []
    for g in sample_games:
        if scope == "1H":
            m = project_cbb_1h(g["homeTeam"], g["awayTeam"])
        else:
            m1 = project_cbb_1h(g["homeTeam"], g["awayTeam"])
            m = {
                "projTotal": round(m1["projTotal"] * 2.02, 1),
                "projSpreadHome": round(m1["projSpreadHome"] * 2.0, 1),
                "confidence": m1["confidence"],
            }

        out.append({**g, "model": {"scope": scope, **m}})

    return out
