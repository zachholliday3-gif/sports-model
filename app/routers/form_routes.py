# app/routers/form_routes.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
import logging

from app.services.last5_form import get_last5_for_team

router = APIRouter(tags=["Form / Last5"])

logger = logging.getLogger("app.form")


# -----------------------------
# Single-team last N games form
# -----------------------------
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
        logger.exception(
            "form_last5_team failed: sport=%s teamId=%s n=%s err=%s",
            sport,
            teamId,
            n,
            e,
        )
        raise HTTPException(status_code=500, detail="internal_error")

    return data


# -----------------------------
# Matchup view: last N for both
# -----------------------------
@router.get("/form/matchup")
async def form_matchup(
    sport: str = Query(..., description="Sport key: cbb, nfl, cfb, nhl"),
    team1Id: str = Query(..., description="ESPN team id for first team (e.g. home)"),
    team2Id: str = Query(..., description="ESPN team id for second team (e.g. away)"),
    n: int = Query(5, ge=1, le=20, description="Number of games to look back for each team (default 5)"),
):
    """
    Matchup-level last N games form for *both* teams.

    Example:
      /api/form/matchup?sport=cbb&team1Id=2509&team2Id=314&n=5

    Returns:
      {
        "sport": "cbb",
        "nRequested": 5,
        "team1": { ... last5 form like /form/last5_team ... },
        "team2": { ... last5 form like /form/last5_team ... }
      }
    """
    sport = sport.lower()
    if sport not in ("cbb", "nfl", "cfb", "nhl"):
        raise HTTPException(status_code=400, detail="Unsupported sport. Use cbb, nfl, cfb, nhl.")

    if team1Id == team2Id:
        raise HTTPException(status_code=400, detail="team1Id and team2Id must be different.")

    try:
        team1_form = await get_last5_for_team(sport, team1Id, n=n)
        team2_form = await get_last5_for_team(sport, team2Id, n=n)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(
            "form_matchup failed: sport=%s team1Id=%s team2Id=%s n=%s err=%s",
            sport,
            team1Id,
            team2Id,
            n,
            e,
        )
        raise HTTPException(status_code=500, detail="internal_error")

    # You can add simple combined metrics later if you want (like avg combined 1H total).
    return {
        "sport": sport,
        "nRequested": n,
        "team1": team1_form,
        "team2": team2_form,
    }
