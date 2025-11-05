# app/routers/nfl_routes.py
from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
import asyncio
import logging

from app.services.espn_nfl import (
    get_games_for_date,
    get_games_for_range,
    extract_game_lite,
)
from app.services.nfl_weeks import week_window, current_season_week
from app.models.nfl_model import project_nfl_fg
from app.services.odds_api import get_nfl_fg_lines  # shared odds service

logger = logging.getLogger("app.nfl")
router = APIRouter(tags=["nfl"])

def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())

# -------- Schedule --------
@router.get("/schedule")
async def nfl_schedule(
    date: Optional[str] = None,
    season: Optional[int] = None,
    week: Optional[int] = None,
):
    """
    Get schedule by date (YYYYMMDD) or by season+week.
    """
    try:
        if season and week:
            start, end = week_window(season, week)
            games = await get_games_for_range(start, end)
        else:
            games = await get_games_for_date(date)
    except Exception as e:
        logger.exception("nfl schedule failed: date=%s season=%s week=%s err=%s", date, season, week, e)
        return []
    rows = [extract_game_lite(ev) for ev in games]
    logger.info("NFL schedule: date=%s season=%s week=%s -> %d", date, season, week, len(rows))
    return rows

# -------- Slate (FG projections) --------
@router.get("/slate")
async def nfl_slate(
    date: Optional[str] = None,
    season: Optional[int] = None,
    week: Optional[int] = None,
    scope: str = Query("FG", pattern="^FG$"),
    include_markets: bool = False,  # default safe/fast
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
        logger.exception("nfl slate failed: date=%s season=%s week=%s err=%s", date, season, week, e)
        return []

    logger.info("NFL slate params: date=%s season=%s week=%s include_markets=%s", date, season, week, include_markets)
    logger.info("NFL games fetched: %d", len(games))

    markets = {}
    if include_markets:
        try:
            markets = await asyncio.wait_for(get_nfl_fg_lines(), timeout=8.0)
            logger.info("NFL markets loaded: %d", len(markets))
        except Exception as e:
            logger.exception("nfl odds fetch failed or timed out: %s", e)
            markets = {}

    rows = []
    for ev in games:
        lite = extract_game_lite(ev)
        m = project_nfl_fg(lite["homeTeam"], lite["awayTeam"])
        token = f"{_norm(lite['awayTeam'])}|{_norm(lite['homeTeam'])}"
        mk = markets.get(token, {}) if include_markets else {}
        mt = mk.get("marketTotal")
        ms = mk.get("marketSpreadHome")
        edge_total = round(m["projTotal"] - mt, 2) if isinstance(mt, (int, float)) else None
        edge_spread = round(m["projSpreadHome"] - ms, 2) if isinstance(ms, (int, float)) else None
        rows.append({
            **lite,
            "model": {"scope": "FG", **m},
            "market": {"total": mt, "spreadHome": ms, "book": mk.get("book")},
            "edge": {"total": edge_total, "spreadHome": edge_spread},
        })
    return rows

# -------- Edges (ranked) --------
@router.get("/edges")
async def nfl_edges(
    date: Optional[str] = None,
    season: Optional[int] = None,
    week: Optional[int] = None,
    sort: str = Query("spread", pattern="^(spread|total)$"),
    limit: int = 25,
):
    rows = await nfl_slate(date=date, season=season, week=week, include_markets=True)
    key = "spreadHome" if sort == "spread" else "total"

    def _abs_edge(row):
        val = (row.get("edge") or {}).get(key)
        return abs(val) if isinstance(val, (int, float)) else -1.0

    ranked = sorted(rows, key=_abs_edge, reverse=True)
    return ranked[:max(1, min(limit, 100))]

# -------- “This Week” helpers --------
@router.get("/this_week")
async def nfl_this_week():
    season, week = current_season_week()
    start, end = week_window(season, week)
    try:
        games = await get_games_for_range(start, end)
    except Exception:
        games = []
    lite = [extract_game_lite(ev) for ev in games]
    logger.info("NFL this_week: season=%s week=%s games=%d", season, week, len(lite))
    return {"season": season, "week": week, "start": start, "end": end, "games": lite}

@router.get("/slate_this_week")
async def nfl_slate_this_week(include_markets: bool = False):
    season, week = current_season_week()
    start, end = week_window(season, week)
    try:
        games = await get_games_for_range(start, end)
    except Exception:
        games = []

    logger.info("NFL slate_this_week: season=%s week=%s include_markets=%s", season, week, include_markets)
    logger.info("NFL games fetched: %d", len(games))

    markets = {}
    if include_markets:
        try:
            markets = await asyncio.wait_for(get_nfl_fg_lines(), timeout=8.0)
            logger.info("NFL markets loaded: %d", len(markets))
        except Exception as e:
            logger.exception("nfl odds fetch failed or timed out: %s", e)
            markets = {}

    rows = []
    for ev in games:
        lite = extract_game_lite(ev)
        m = project_nfl_fg(lite["homeTeam"], lite["awayTeam"])
        token = f"{_norm(lite['awayTeam'])}|{_norm(lite['homeTeam'])}"
        mk = markets.get(token, {}) if include_markets else {}
        mt = mk.get("marketTotal")
        ms = mk.get("marketSpreadHome")
        edge_total = round(m["projTotal"] - mt, 2) if isinstance(mt, (int, float)) else None
        edge_spread = round(m["projSpreadHome"] - ms, 2) if isinstance(ms, (int, float)) else None
        rows.append({
            **lite,
            "model": {"scope": "FG", **m},
            "market": {"total": mt, "spreadHome": ms, "book": mk.get("book")},
            "edge": {"total": edge_total, "spreadHome": edge_spread},
        })
    return {"season": season, "week": week, "start": start, "end": end, "rows": rows}

# -------- Projections alias --------
@router.get("/projections")
async def nfl_projections(
    date: Optional[str] = None,
    season: Optional[int] = None,
    week: Optional[int] = None,
    include_markets: bool = False,
):
    return await nfl_slate(
        date=date,
        season=season,
        week=week,
        scope="FG",
        include_markets=include_markets,
    )
