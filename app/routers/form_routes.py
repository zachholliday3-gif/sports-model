# app/routers/form_routes.py
from fastapi import APIRouter, Query
from typing import Optional
import logging

from app.services.last5_form import get_form_summary, get_matchup_form

logger = logging.getLogger("app.form")
router = APIRouter(tags=["Form / Team Recent Games"])

# -----------------------------
# GET /api/form/last5_team
# -----------------------------
@router.get("/form/last5_team")
async def form_last5_team(
    sport: str = Query(..., description="Sport key (cbb, nfl, nhl, cfb)"),
    teamId: str = Query(..., description="ESPN team ID"),
    n: int = Query(5, description="Number of recent games to pull (default 5)")
):
    """
    Return recent game form for a single team.
    """
    try:
        result = await get_form_summary(sport=sport, team_id=teamId, n=n)
        return result
    except Exception as e:
        logger.exception(f"form_last5_team failed: {e}")
        return {"error": "internal_error", "detail": str(e)}


# -----------------------------
# GET /api/form/matchup
# -----------------------------
@router.get("/form/matchup")
async def form_matchup(
    sport: str = Query(..., description="Sport key (cbb, nfl, nhl, cfb)"),
    team1Id: str = Query(..., description="ESPN team ID for Team 1 (away)"),
    team2Id: str = Query(..., description="ESPN team ID for Team 2 (home)"),
    n: int = Query(5, description="Number of recent games per team (default 5)")
):
    """
    Return side-by-side recent form for both teams in a matchup.
    """
    try:
        logger.info(f"FORM MATCHUP sport={sport} team1={team1Id} team2={team2Id} n={n}")
        result = await get_matchup_form(sport=sport, team1_id=team1Id, team2_id=team2Id, n=n)
        return result
    except Exception as e:
        logger.exception(f"form_matchup failed: {e}")
        return {"error": "internal_error", "detail": str(e)}
