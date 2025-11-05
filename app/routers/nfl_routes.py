# app/routers/nfl_routes.py
from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
import logging

from app.models.nfl_types import GameLite, Projection, MatchupDetail
from app.models.nfl_model import project_nfl_fg
from app.services.espn_nfl import (
    get_games_for_date,
    get_games_for_range,
    extract_game_lite,
    extract_matchup_detail,
)
from app.services.nfl_weeks import week_window
from app.services.props_nfl import fetch_defense_allowed_last5, project_player_line

logger = logging.getLogger("app")
router = APIRouter()

# --------- helpers ----------
def _ensure_week(season: Optional[int], week: Optional[int]) -> tuple[Optional[int], Optional[int]]:
    if season and week: return (season, week)
    return (None, None)

# --------- schedule (by date or by week) ----------
@router.get("/schedule", response_model=List[GameLite])
async def nfl_schedule(date: Optional[str] = None, season: Optional[int] = None, week: Optional[int] = None):
    if season and week:
        start, end = week_window(season, week)
        games = await get_games_for_range(start, end)
    else:
        try:
            games = await get_games_for_date(date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.exception("nfl schedule failed for date=%s: %s", date, e)
            return []
    return [extract_game_lite(ev) for ev in games]

# --------- projections / slate ----------
@router.get("/slate")
async def nfl_slate(
    date: Optional[str] = None,
    season: Optional[int] = None,
    week: Optional[int] = None,
    scope: str = Query("FG", pattern="^FG$"),
):
    if scope != "FG":
        raise HTTPException(400, "NFL supports FG scope only at this endpoint")

    if season and week:
        start, end = week_window(season, week)
        games = await get_games_for_range(start, end)
    else:
        try:
            games = await get_games_for_date(date)
        except Exception as e:
            logger.exception("nfl slate failed: %s", e)
            return []

    rows = []
    for ev in games:
        lite = extract_game_lite(ev)
        m = project_nfl_fg(lite["homeTeam"], lite["awayTeam"])
        rows.append({**lite, "model": {"scope": scope, **m}})
    return rows

# --------- single matchup ----------
@router.get("/matchups/{gameId}", response_model=MatchupDetail)
async def nfl_matchup(gameId: str, date: Optional[str] = None, season: Optional[int] = None, week: Optional[int] = None):
    # find the game in selected range
    if season and week:
        start, end = week_window(season, week)
        games = await get_games_for_range(start, end)
    else:
        games = await get_games_for_date(date)

    ev = next((g for g in games if str(g.get("id") or "") == str(gameId)), None)
    if not ev:
        raise HTTPException(404, "Game not found")

    base = extract_matchup_detail(ev)
    m = project_nfl_fg(base["homeTeam"], base["awayTeam"])
    return {**base, "notes": None, "model": {"gameId": base["gameId"], "scope": "FG", **m}}

# --------- player props (by team/opponent/player) ----------
@router.get("/player_props")
async def nfl_player_props(
    player: str,
    position: str,
    team: str,
    opponent: str,
    season: Optional[int] = None,
    week: Optional[int] = None,
):
    """
    Simple opponent-adjusted player prop suggestions.
    Later: swap fetch_defense_allowed_last5 with a real data source (PFF/SDIO/etc.).
    """
    # Validate position lightly
    position = position.upper()
    if position not in ("QB","RB","WR","TE"):
        raise HTTPException(400, "position must be one of: QB,RB,WR,TE")

    # (optional) confirm team/opponent exist in selected window
    # For now we skip strict validation for speed & provider limits.

    opp_def = await fetch_defense_allowed_last5(opponent)
    lines = project_player_line(player, position, opp_def)
    return {
        "player": player,
        "team": team,
        "opponent": opponent,
        "season": season,
        "week": week,
        "basis": "opponent defense allowed (last 5) — stub provider",
        "props": lines,
    }
