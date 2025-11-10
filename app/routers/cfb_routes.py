# app/routers/cfb_routes.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging

from app.services.espn_cfb import get_games_for_date, project_cfb_fg

router = APIRouter(tags=["CFB"])
logger = logging.getLogger("app.cfb")


@router.get("/schedule")
async def cfb_schedule(
    date: Optional[str] = Query(None, description="YYYY-MM-DD or YYYYMMDD; default = today (NY)"),
    fbs_only: bool = Query(True, description="If true, prefer FBS (groups=80) with fallback to all levels.")
):
    """
    Returns College Football schedule for a given date.

    By default, looks at FBS first; if no games found, auto-falls back to all levels.
    """
    try:
        games = await get_games_for_date(date, fbs_only=fbs_only)
    except Exception as e:
        logger.exception("CFB schedule failed for date=%s fbs_only=%s: %s", date, fbs_only, e)
        raise HTTPException(status_code=500, detail="fetch_failed")

    return games


@router.get("/projections")
async def cfb_projections(
    date: Optional[str] = Query(None, description="YYYY-MM-DD or YYYYMMDD; default = today (NY)"),
    fbs_only: bool = Query(True, description="If true, prefer FBS with fallback to all levels.")
):
    """
    Returns full-game model projections for all College Football games on a date.
    """
    try:
        games = await get_games_for_date(date, fbs_only=fbs_only)
    except Exception as e:
        logger.exception("CFB projections failed for date=%s fbs_only=%s: %s", date, fbs_only, e)
        raise HTTPException(status_code=500, detail="fetch_failed")

    out = []
    for g in games:
        model = project_cfb_fg(g["homeTeam"], g["awayTeam"])
        out.append({**g, "model": model})

    return out
