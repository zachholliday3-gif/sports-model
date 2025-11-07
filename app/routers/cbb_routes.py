# app/routers/cbb_routes.py
from __future__ import annotations

import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query

from app.models.cbb_types import GameLite, Projection, MatchupDetail
from app.services.espn_cbb import (
    get_games_for_date,
    extract_game_lite,
    extract_matchup_detail,
)
from app.models.cbb_model import project_cbb_1h

logger = logging.getLogger("app.cbb")
router = APIRouter(tags=["CBB"])


# -------------------------
# 🏀  CBB — Schedule
# -------------------------
@router.get("/schedule", response_model=List[GameLite])
async def cbb_schedule(
    date: Optional[str] = None,
    d1_only: bool = Query(
        True, description="If true, only NCAA Division I games (ESPN groups = 50)"
    ),
):
    """
    Returns ESPN CBB schedule for a date (defaults to Division I only).
    """
    try:
        games = await get_games_for_date(date, d1_only=d1_only)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("schedule failed for date=%s: %s", date, e)
        return []
    return [extract_game_lite(ev) for ev in games]


# -------------------------
# 🧮  CBB — Projections
# -------------------------
@router.get("/projections", response_model=List[Projection])
async def cbb_projections(
    date: Optional[str] = None,
    scope: str = Query("1H", pattern="^(1H|FG)$"),
    d1_only: bool = Query(
        True, description="If true, only NCAA Division I games (ESPN groups = 50)"
    ),
):
    """
    Returns projections for a date (scope: 1H or FG).
    Defaults to Division I only.
    """
    if scope not in ("1H", "FG"):
        raise HTTPException(400, "scope must be '1H' or 'FG'")

    try:
        games = await get_games_for_date(date, d1_only=d1_only)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("projections failed for date=%s: %s", date, e)
        return []

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


# -------------------------
# 🗓️  CBB — Slate (Schedule + Model)
# -------------------------
@router.get("/slate")
async def cbb_slate(
    date: Optional[str] = None,
    scope: str = Query("1H", pattern="^(1H|FG)$"),
    d1_only: bool = Query(
        True, description="If true, only NCAA Division I games (ESPN groups = 50)"
    ),
):
    """
    Returns schedule rows with model projections (defaults to Division I only).
    """
    try:
        games = await get_games_for_date(date, d1_only=d1_only)
    except Exception as e:
        logger.exception("slate failed for date=%s: %s", date, e)
        return []

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


# -------------------------
# 🏀  CBB — Single Matchup (optional endpoint)
# -------------------------
@router.get("/matchups/{gameId}", response_model=MatchupDetail)
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

    return {
        **base,
        "notes": None,
        "model": {"gameId": base["gameId"], "scope": scope, **model},
    }
