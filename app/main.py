from fastapi import FastAPI, HTTPException
from typing import List, Optional
from app.models.types import GameLite, Projection, MatchupDetail
from app.services.espn import get_games_for_date, extract_game_lite, extract_matchup_detail
from app.services.projections import project_cbb_1h

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Zach Sports Model API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app = FastAPI(title="Zach Sports Model API", version="1.0.0")

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/api/cbb/schedule", response_model=List[GameLite])
async def cbb_schedule(date: Optional[str] = None):
    """Get today's (or specified) CBB schedule. date: YYYYMMDD (e.g. 20251104)"""
    games = await get_games_for_date(date)
    return [extract_game_lite(ev) for ev in games]

@app.get("/api/cbb/projections", response_model=List[Projection])
async def cbb_projections(date: Optional[str] = None, scope: str = "1H"):
    """Get projections for a date (scope: 1H or FG). Placeholder model for now."""
    if scope not in ("1H", "FG"):
        raise HTTPException(400, "scope must be '1H' or 'FG'")
    games = await get_games_for_date(date)
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

@app.get("/api/cbb/matchups/{gameId}", response_model=MatchupDetail)
async def cbb_matchup(gameId: str, scope: str = "1H"):
    """Details + projection for a single matchup (today)."""
    games = await get_games_for_date()
    ev = next((g for g in games if g["id"] == gameId), None)
    if not ev:
        raise HTTPException(404, "Game not found for today")
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
        "model": {"gameId": base["gameId"], "scope": scope, **m}
    }
