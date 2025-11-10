# app/routers/nhl_routes.py
from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
import logging

from app.services.espn_nhl import get_games_for_date, project_nhl_fg

router = APIRouter(prefix="/api/nhl", tags=["NHL"])
logger = logging.getLogger("app.nhl")

@router.get("/schedule")
async def nhl_schedule(date: Optional[str] = None):
    """Return NHL schedule for a given date."""
    try:
        games = await get_games_for_date(date)
    except Exception as e:
        logger.exception("NHL schedule failed: %s", e)
        raise HTTPException(status_code=500, detail="fetch_failed")

    return [g for g in games if g]

@router.get("/projections")
async def nhl_projections(date: Optional[str] = None):
    """Return model projections for all NHL games on a date."""
    try:
        games = await get_games_for_date(date)
    except Exception as e:
        logger.exception("NHL projections failed: %s", e)
        raise HTTPException(status_code=500, detail="fetch_failed")

    out = []
    for g in games:
        model = project_nhl_fg(g["homeTeam"], g["awayTeam"])
        out.append({
            **g,
            "model": model
        })
    return out
