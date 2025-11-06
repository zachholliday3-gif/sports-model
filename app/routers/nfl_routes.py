# app/routers/nfl_routes.py
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.models.nfl_model import project_nfl_fg
from app.services.espn_nfl import (
    extract_game_lite,
    get_games_for_date,
    get_games_for_range,
)
from app.services.nfl_weeks import current_season_week, week_window
from app.services.odds_api import get_nfl_fg_lines  # shared odds service

# --------------------------------------------------------------------
# Router + logger MUST be defined before any @router.get decorators
# --------------------------------------------------------------------
logger = logging.getLogger("app.nfl")
router = APIRouter(tags=["nfl"])

def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


# ---------------- Schedule (week-based) ----------------
@router.get("/schedule")
async def nfl_schedule(
    season: Optional[int] = None,
    week: Optional[int] = None,
):
    """
    Get NFL schedule by season+week. If missing, uses the current week.
    """
    try:
        if season and week:
            start, end = week_window(season, week)
        else:
            season, week = current_season_week()
            start, end = week_window(season, week)
        games = await get_games_for_range(start, end)
    except Exception as e:
        logger.exception("nfl schedule failed: season=%s week=%s err=%s", season, week, e)
        return []
    rows = [extract_game_lite(ev) for ev in games]
    logger.info("NFL schedule: season=%s week=%s -> %d", season, week, len(rows))
    return rows


# ---------------- Slate (FG projections, week-based) ----------------
@router.get("/slate")
async def nfl_slate(
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
        else:
            season, week = current_season_week()
            start, end = week_window(season, week)
        games = await get_games_for_range(start, end)
    except Exception as e:
        logger.exception("nfl slate failed: season=%s week=%s err=%s", season, week, e)
        return []

    logger.info("NFL slate params: season=%s week=%s include_markets=%s", season, week, include_markets)
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


# ---------------- Edges (ranked, week-based) ----------------
@router.get("/edges")
async def nfl_edges(
    season: Optional[int] = None,
    week: Optional[int] = None,
    sort: str = Query("spread", pattern="^(spread|total)$"),
    limit: int = 25,
):
    rows = await nfl_slate(season=season, week=week, include_markets=True)
    key = "spreadHome" if sort == "spread" else "total"

    def _abs_edge(row):
        val = (row.get("edge") or {}).get(key)
        return abs(val) if isinstance(val, (int, float)) else -1.0

    ranked = sorted(rows, key=_abs_edge, reverse=True)
    return ranked[:max(1, min(limit, 100))]


# ---------------- “This Week” helpers ----------------
@router.get("/this_week")
async def nfl_this_week(include_markets: bool = False):
    season, week = current_season_week()
    start, end = week_window(season, week)
    try:
        games = await get_games_for_range(start, end)
    except Exception:
        games = []
    rows = []
    for ev in games:
        lite = extract_game_lite(ev)
        m = project_nfl_fg(lite["homeTeam"], lite["awayTeam"])
        rows.append({
            **lite,
            "model": {"scope": "FG", **m},
        })
    return {"season": season, "week": week, "start": start, "end": end, "rows": rows}


@router.get("/week")
async def nfl_week_slate(
    season: int,
    week: int,
    include_markets: bool = False,
):
    """
    Returns {season, week, start, end, rows:[...]} built from the computed week window.
    """
    start, end = week_window(season, week)
    try:
        games = await get_games_for_range(start, end)
    except Exception as e:
        logger.exception("nfl week(%s,%s) fetch failed: %s", season, week, e)
        games = []

    logger.info("NFL week slate: season=%s week=%s include_markets=%s", season, week, include_markets)
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


# ---------------- Projections alias (GPT-friendly) ----------------
@router.get("/projections_simple")
async def nfl_projections_simple(
    when: str | None = None,
    season: int | None = None,
    week: int | None = None,
    include_markets: bool = False,
):
    """
    One-call NFL projections for GPT, week-oriented.
    Supported when:
      - this_week  (default if none)
      - week:YYYY:W  (e.g., 'week:2025:10')
    Or pass explicit season & week.
    """
    w = (when or "this_week").strip().lower()

    if season and week:
        return await nfl_week_slate(season=season, week=week, include_markets=include_markets)

    if w == "this_week":
        return await nfl_this_week(include_markets=include_markets)

    if w.startswith("week:"):
        try:
            _, rest = w.split(":", 1)
            y_str, wk_str = rest.split(":")
            return await nfl_week_slate(season=int(y_str), week=int(wk_str), include_markets=include_markets)
        except Exception:
            pass

    # Fallback to current week
    return await nfl_this_week(include_markets=include_markets)


@router.get("/edges_simple")
async def nfl_edges_simple(
    when: str | None = None,
    season: int | None = None,
    week: int | None = None,
    sort: str = Query("spread", pattern="^(spread|total)$"),
    limit: int = 25,
):
    rows = await nfl_projections_simple(when=when, season=season, week=week, include_markets=True)
    # rows might be list (from /slate) or dict (from week/this_week); normalize:
    if isinstance(rows, dict) and "rows" in rows:
        rows_list = rows.get("rows") or []
    else:
        rows_list = rows if isinstance(rows, list) else []

    key = "spreadHome" if sort == "spread" else "total"
    def _abs_edge(row):
        val = (row.get("edge") or {}).get(key)
        return abs(val) if isinstance(val, (int, float)) else -1.0

    ranked = sorted(rows_list, key=_abs_edge, reverse=True)
    return ranked[:max(1, min(limit, 100))]
