# app/routers/cbb_routes.py
from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
import logging

from app.models.cbb_types import GameLite, Projection, MatchupDetail
from app.services.espn_cbb import get_games_for_date, extract_game_lite, extract_matchup_detail
from app.models.cbb_model import project_cbb_1h

logger = logging.getLogger("app")

router = APIRouter()

@router.get("/schedule", response_model=List[GameLite])
async def cbb_schedule(date: Optional[str] = None):
    try:
        games = await get_games_for_date(date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("schedule failed for date=%s: %s", date, e)
        return []
    logger.info("schedule: %d games for date=%s", len(games), date)
    return [extract_game_lite(ev) for ev in games]

@router.get("/projections", response_model=List[Projection])
async def cbb_projections(date: Optional[str] = None, scope: str = "1H"):
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
    out: List[Projection] = []
    for ev in games:
        lite = extract_game_lite(ev)
        if scope == "1H":
            m = project_cbb_1h(lite["homeTeam"], lite["awayTeam"])
        else:
            base = project_cbb_1h(lite["homeTeam"], lite["awayTeam"])
            m = {
                "projTotal": round(base["projTotal"] * 2.02, 1),
                "projSpreadHome": round(base["projSpreadHome"] * 2.0, 1),
                "confidence": base["confidence"],
            }
        out.append({"gameId": lite["gameId"], "scope": scope, **m})
    return out

@router.get("/matchups/{gameId}", response_model=MatchupDetail)
async def cbb_matchup(gameId: str, scope: str = "1H"):
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
        m = project_cbb_1h(base["homeTeam"], base["awayTeam"])
    else:
        base_m = project_cbb_1h(base["homeTeam"], base["awayTeam"])
        m = {
            "projTotal": round(base_m["projTotal"] * 2.02, 1),
            "projSpreadHome": round(base_m["projSpreadHome"] * 2.0, 1),
            "confidence": base_m["confidence"],
        }
    return {**base, "notes": None, "model": {"gameId": base["gameId"], "scope": scope, **m}}

@router.get("/slate")
async def cbb_slate(date: Optional[str] = None, scope: str = Query("1H", pattern="^(1H|FG)$")):
    try:
        games = await get_games_for_date(date)
    except Exception as e:
        logger.exception("slate failed for date=%s: %s", date, e)
        return []
    logger.info("slate: %d games for date=%s", len(games), date)
    rows = []
    for ev in games:
        lite = extract_game_lite(ev)
        if scope == "1H":
            m = project_cbb_1h(lite["homeTeam"], lite["awayTeam"])
        else:
            base = project_cbb_1h(lite["homeTeam"], lite["awayTeam"])
            m = {
                "projTotal": round(base["projTotal"] * 2.02, 1),
                "projSpreadHome": round(base["projSpreadHome"] * 2.0, 1),
                "confidence": base["confidence"],
            }
        rows.append({**lite, "model": {"scope": scope, **m}})
    return rows

@router.get("/mock_slate")
async def cbb_mock_slate(scope: str = "1H"):
    sample_games = [
        {"gameId": "M1", "homeTeam": "Purdue Boilermakers", "awayTeam": "Evansville Purple Aces"},
        {"gameId": "M2", "homeTeam": "Duke Blue Devils", "awayTeam": "Michigan State Spartans"},
    ]
    out = []
    for g in sample_games:
        if scope == "1H":
            m = project_cbb_1h(g["homeTeam"], g["awayTeam"])
        else:
            base = project_cbb_1h(g["homeTeam"], g["awayTeam"])
            m = {
                "projTotal": round(base["projTotal"] * 2.02, 1),
                "projSpreadHome": round(base["projSpreadHome"] * 2.0, 1),
                "confidence": base["confidence"],
            }
        out.append({**g, "model": {"scope": scope, **m}})
    return out
