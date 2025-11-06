# app/routers/nfl_routes.py
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.models.nfl_model import project_nfl_fg
from app.services.espn_nfl import (
    extract_game_lite,
    get_games_for_range,
)
from app.services.nfl_weeks import current_season_week, week_window
from app.services.odds_api import get_nfl_fg_lines  # shared odds service

logger = logging.getLogger("app.nfl")
router = APIRouter(tags=["nfl"])

def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


async def _week_games_soft(season: int, week: int) -> tuple[list, dict]:
    """
    Fetch games for week window; if zero, widen +/- 3 days; if still zero, fallback to previous week.
    Returns (games, meta) where meta may include {"fallbackFrom": {"season":..., "week":...}}
    """
    start, end = week_window(season, week)
    try:
        games = await get_games_for_range(start, end)
    except Exception:
        games = []

    if games:
        return games, {}

    # widen +/-3 days (TNF/SNF/MNF and ESPN quirks)
    logger.info("NFL widen window: season=%s week=%s", season, week)
    try:
        games = await get_games_for_range(start - timedelta(days=3), end + timedelta(days=3))
    except Exception:
        games = []
    if games:
        return games, {}

    # soft fallback: previous week
    prev_week = max(1, week - 1)
    if prev_week != week:
        p_start, p_end = week_window(season, prev_week)
        logger.info("NFL fallback to previous week: season=%s week=%s -> %s", season, week, prev_week)
        try:
            p_games = await get_games_for_range(p_start, p_end)
        except Exception:
            p_games = []
        if p_games:
            return p_games, {"fallbackFrom": {"season": season, "week": week}, "season": season, "week": prev_week}

    # truly nothing
    return [], {}


# ---------------- Schedule (week-based) ----------------
@router.get("/schedule")
async def nfl_schedule(
    season: Optional[int] = None,
    week: Optional[int] = None,
):
    """
    Get NFL schedule by season+week. If missing, uses the current week.
    Soft fallback: if requested week returns 0, previous week is returned with a fallback note.
    """
    if season and week:
        games, meta = await _week_games_soft(season, week)
    else:
        season, week = current_season_week()
        games, meta = await _week_games_soft(season, week)

    rows = [extract_game_lite(ev) for ev in games]
    res = {"rows": rows, "season": meta.get("season", season), "week": meta.get("week", week)}
    if "fallbackFrom" in meta:
        res["fallbackFrom"] = meta["fallbackFrom"]
    logger.info("NFL schedule: season=%s week=%s -> %d", res["season"], res["week"], len(rows))
    return res


# ---------------- Slate (FG projections, week-based) ----------------
@router.get("/slate")
async def nfl_slate(
    season: Optional[int] = None,
    week: Optional[int] = None,
    scope: str = Query("FG", pattern="^FG$"),
    include_markets: bool = False,
):
    # IMPORTANT: NFL only supports FG scope
    if scope != "FG":
        raise HTTPException(400, "NFL supports FG scope only")

    if season and week:
        games, meta = await _week_games_soft(season, week)
        use_season, use_week = meta.get("season", season), meta.get("week", week)
    else:
        season, week = current_season_week()
        games, meta = await _week_games_soft(season, week)
        use_season, use_week = meta.get("season", season), meta.get("week", week)

    logger.info("NFL slate params: season=%s week=%s include_markets=%s", use_season, use_week, include_markets)
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

    res = {"season": use_season, "week": use_week, "rows": rows}
    if "fallbackFrom" in meta:
        res["fallbackFrom"] = meta["fallbackFrom"]
    return res


# ---------------- Edges (ranked, week-based) ----------------
@router.get("/edges")
async def nfl_edges(
    season: Optional[int] = None,
    week: Optional[int] = None,
    sort: str = Query("spread", pattern="^(spread|total)$"),
    limit: int = 25,
):
    data = await nfl_slate(season=season, week=week, include_markets=True)
    rows = data.get("rows", [])
    key = "spreadHome" if sort == "spread" else "total"

    def _abs_edge(row):
        val = (row.get("edge") or {}).get(key)
        return abs(val) if isinstance(val, (int, float)) else -1.0

    ranked = sorted(rows, key=_abs_edge, reverse=True)
    out = ranked[:max(1, min(limit, 100))]
    if "fallbackFrom" in data:
        return {"season": data["season"], "week": data["week"], "fallbackFrom": data["fallbackFrom"], "rows": out}
    return {"season": data["season"], "week": data["week"], "rows": out}


# ---------------- This Week + Week endpoints (for GPT) ----------------
@router.get("/this_week")
async def nfl_this_week(include_markets: bool = False):
    season, week = current_season_week()
    return await nfl_slate(season=season, week=week, include_markets=include_markets)

@router.get("/week")
async def nfl_week_slate(
    season: int,
    week: int,
    include_markets: bool = False,
):
    return await nfl_slate(season=season, week=week, include_markets=include_markets)


# ---------------- GPT-friendly simple endpoints ----------------
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
    data = await nfl_projections_simple(when=when, season=season, week=week, include_markets=True)
    rows = data.get("rows", []) if isinstance(data, dict) else (data or [])
    key = "spreadHome" if sort == "spread" else "total"

    def _abs_edge(row):
        val = (row.get("edge") or {}).get(key)
        return abs(val) if isinstance(val, (int, float)) else -1.0

    ranked = sorted(rows, key=_abs_edge, reverse=True)
    out = ranked[:max(1, min(limit, 100))]
    if isinstance(data, dict) and "fallbackFrom" in data:
        return {"season": data.get("season"), "week": data.get("week"), "fallbackFrom": data["fallbackFrom"], "rows": out}
    return out
