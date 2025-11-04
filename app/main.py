# app/main.py
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
import logging

from app.models.types import GameLite, Projection, MatchupDetail
from app.services.espn import get_games_for_date, extract_game_lite, extract_matchup_detail
from app.services.projections import project_cbb_1h

# ---- Logging ----
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

# ---- App (make docs explicit so /docs always works) ----
app = FastAPI(
    title="Zach Sports Model API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
)

# ---- CORS (open for now; tighten to your domain later) ----
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Health ----
@app.get("/health")
async def health():
    return {"ok": True}

# ---- Schedule ----
@app.get("/api/cbb/schedule", response_model=List[GameLite])
async def cbb_schedule(date: Optional[str] = None):
    """
    Get CBB schedule for a date. If `date` is omitted, uses America/New_York "today"
    and falls back to "yesterday" if empty. Date format: YYYYMMDD or YYYY-MM-DD.
    """
    games = await get_games_for_date(date)
    logger.info("schedule: %d games for date=%s", len(games), date)
    return [extract_game_lite(ev) for ev in games]

# ---- Projections ----
@app.get("/api/cbb/projections", response_model=List[Projection])
async def cbb_projections(date: Optional[str] = None, scope: str = "1H"):
    """
    Get projections for a date (scope: 1H or FG).
    Current model is a deterministic placeholder you can replace later.
    """
    if scope not in ("1H", "FG"):
        raise HTTPException(400, "scope must be '1H' or 'FG'")

    games = await get_games_for_date(date)
    logger.info("projections: %d games for date=%s", len(games), date)

    projs: List[Projection] = []
    for ev in games:
        lite = extract_game_lite(ev)
        if scope == "1H":
            m = project_cbb_1h(lite["homeTeam"], lite["awayTeam"])
        else:
            m1 = project_cbb_1h(lite["homeTeam"], lite["awayTeam"])
            m = {
                "projTotal": round(m1["projTotal"] * 2.02, 1),
                "projSpreadHome": round(m1["projSpreadHome"] * 2.0, 1),
                "confidence": m1["confidence"],
            }
        projs.append({"gameId": lite["gameId"], "scope": scope, **m})
    return projs

# ---- Single Matchup ----
@app.get("/api/cbb/matchups/{gameId}", response_model=MatchupDetail)
async def cbb_matchup(gameId: str, scope: str = "1H"):
    """
    Details + projection for a single matchup (uses America/New_York 'today' slate).
    """
    games = await get_games_for_date()
    ev = next((g for g in games if g.get("id") == gameId), None)
    if not ev:
        raise HTTPException(404, "Game not found for requested date")

    base = extract_matchup_detail(ev)
    if scope == "1H":
        m = project_cbb_1h(base["homeTeam"], base["awayTeam"])
    else:
        m1 = project_cbb_1h(base["homeTeam"], base["awayTeam"])
        m = {
            "projTotal": round(m1["projTotal"] * 2.02, 1),
            "projSpreadHome": round(m1["projSpreadHome"] * 2.0, 1),
            "confidence": m1["confidence"],
        }

    return {
        **base,
        "notes": None,
        "model": {"gameId": base["gameId"], "scope": scope, **m},
    }

# ---- Slate (schedule + model in one call) ----
@app.get("/api/cbb/slate")
async def cbb_slate(
    date: Optional[str] = None,
    scope: str = Query("1H", pattern="^(1H|FG)$")
):
    """
    Convenience endpoint: returns schedule rows with attached model numbers.
    """
    games = await get_games_for_date(date)
    logger.info("slate: %d games for date=%s", len(games), date)

    out = []
    for ev in games:
        lite = extract_game_lite(ev)
        if scope == "1H":
            m = project_cbb_1h(lite["homeTeam"], lite["awayTeam"])
        else:
            m1 = project_cbb_1h(lite["homeTeam"], lite["awayTeam"])
            m = {
                "projTotal": round(m1["projTotal"] * 2.02, 1),
                "projSpreadHome": round(m1["projSpreadHome"] * 2.0, 1),
                "confidence": m1["confidence"],
            }
        out.append({**lite, "model": {"scope": scope, **m}})
    return out
