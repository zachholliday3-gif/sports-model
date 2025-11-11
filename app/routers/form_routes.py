# app/routers/form_routes.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging

from app.services.last5_form import get_last5_for_team

router = APIRouter(tags=["Form / Last5"])

logger = logging.getLogger("app.form")


@router.get("/form/last5_team")
async def form_last5_team(
    sport: str = Query(..., description="Sport key: cbb, nfl, cfb, nhl"),
    teamId: str = Query(..., description="ESPN team id"),
    n: int = Query(5, ge=1, le=20, description="Number of games to look back (default 5)"),
):
    """
    Generic 'last N games' view for a single team.

    Supports:
      - cbb (men's D1)
      - nfl
      - cfb
      - nhl

    Returns:
      - per-game rows with 1H + full-game scoring
      - averages over the sample
    """
    sport = sport.lower()
    if sport not in ("cbb", "nfl", "cfb", "nhl"):
        raise HTTPException(status_code=400, detail="Unsupported sport. Use cbb, nfl, cfb, nhl.")

    try:
        data = await get_last5_for_team(sport, teamId, n=n)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("form_last5_team failed: sport=%s teamId=%s n=%s err=%s", sport, teamId, n, e)
        raise HTTPException(status_code=500, detail="internal_error")

    return data
