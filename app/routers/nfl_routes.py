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

# ---------- Schedule (by date OR by week) ----------
@router.get("/schedule", response_model=List[GameLite])
async def nfl_schedule(
    date: Optional[str] = None,
    season: Optional[int] = None,
    week: Optional[int] = None,
):
    try:
        if season and week:
            start, end = week_window(season, week)   # NOTE: no 'await'
            games = await get_games_for_range(start, end)
        else:
            games = await get_games_for_date(date)
        logger.info("nfl schedule: %d games (date=%s season=%s week=%s)", len(games), date, season, week)
        return [extract_game_lite(ev) for ev in games]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("nfl schedule failed (date=%s season=%s week=%s): %s", date, season, week, e)
        return []  # graceful fallback instead of 500


# ---------- Slate (full game projections) ----------
@router.get("/slate")
async def nfl_slate(
    date: Optional[str] = None,
    season: Optional[int] = None,
    week: Optional[int] = None,
    scope: str = Query("FG", pattern="^FG$"),
):
    if scope != "FG":
        raise HTTPException(400, "NFL supports FG scope only")

    try:
        if season and week:
            start, end = week_window(season, week)
            games = await get_games_for_range(start, end)
        else:
            games = await get_games_for_date(date)
    except Exception as e:
        logger.exception("nfl slate failed (date=%s season=%s week=%s): %s", date, season, week, e)
        return []

    rows = []
    for ev in games:
        lite = extract_game_lite(ev)
        m = project_nfl_fg(lite["homeTeam"], lite["awayTeam"])
        rows.append({**lite, "model": {"scope": scope, **m}})
    logger.info("nfl slate: %d rows (date=%s season=%s week=%s)", len(rows), date, season, week)
    return rows


# ---------- Single matchup ----------
@router.get("/matchups/{gameId}", response_model=MatchupDetail)
async def nfl_matchup(
    gameId: str,
    date: Optional[str] = None,
    season: Optional[int] = None,
    week: Optional[int] = None,
):
    try:
        if season and week:
            start, end = week_window(season, week)
            games = await get_games_for_range(start, end)
        else:
            games = await get_games_for_date(date)
    except Exception as e:
        logger.exception("nfl matchup load failed (gameId=%s): %s", gameId, e)
        raise HTTPException(404, "Could not load matchups")

    ev = next((g for g in games if str(g.get("id") or "") == str(gameId)), None)
    if not ev:
        raise HTTPException(404, "Game not found")

    base = extract_matchup_detail(ev)
    m = project_nfl_fg(base["homeTeam"], base["awayTeam"])
    return {**base, "notes": None, "model": {"gameId": base["gameId"], "scope": "FG", **m}}


# ---------- Player props (stub, opponent-adjusted) ----------
@router.get("/player_props")
async def nfl_player_props(
    player: str,
    position: str,
    team: str,
    opponent: str,
    season: Optional[int] = None,
    week: Optional[int] = None,
):
    pos = position.upper()
    if pos not in ("QB", "RB", "WR", "TE"):
        raise HTTPException(400, "position must be one of: QB, RB, WR, TE")

    try:
        opp_def = await fetch_defense_allowed_last5(opponent)
    except Exception as e:
        logger.exception("nfl player_props fetch failed (opponent=%s): %s", opponent, e)
        raise HTTPException(502, "Could not fetch opponent defense metrics")

    lines = project_player_line(player, pos, opp_def)
    return {
        "player": player,
        "position": pos,
        "team": team,
        "opponent": opponent,
        "season": season,
        "week": week,
        "basis": "opponent defense allowed (last 5) — stub provider",
        "props": lines,
    }


# ---------- Mock slate (always works; for sanity checks) ----------
@router.get("/mock_slate")
async def nfl_mock_slate():
    sample = [
        {"gameId": "N1", "homeTeam": "Kansas City Chiefs", "awayTeam": "Baltimore Ravens"},
        {"gameId": "N2", "homeTeam": "Philadelphia Eagles", "awayTeam": "Dallas Cowboys"},
    ]
    rows = []
    for g in sample:
        m = project_nfl_fg(g["homeTeam"], g["awayTeam"])
        rows.append({**g, "model": {"scope": "FG", **m}, "status": "STATUS_SCHEDULED", "date": None})
    return rows
