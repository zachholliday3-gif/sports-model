# app/routers/nfl_props_routes.py
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Tuple, Union
from datetime import datetime

from fastapi import APIRouter, Query, HTTPException

from app.models.nfl_props_model import project_player_props
from app.services.espn_nfl import extract_game_lite, get_games_for_range
from app.services.nfl_weeks import current_season_week, week_window
from app.services.odds_api_nfl_props import get_nfl_player_prop_lines

logger = logging.getLogger("app.nfl_props")
router = APIRouter(tags=["nfl-props"])

def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())

async def _week_games(season: int, week: int):
    start, end = week_window(season, week)
    return await get_games_for_range(start, end)

def _build_game_lookup(events: List[dict]) -> Dict[str, dict]:
    out = {}
    for ev in events:
        lite = extract_game_lite(ev)
        token = f"{_norm(lite['awayTeam'])}|{_norm(lite['homeTeam'])}"
        out[token] = lite
    return out

# utility: we just pass through whatever week_window gives us (str or datetime) to the service
def _week_bounds(season: int, week: int) -> Tuple[Union[str, datetime], Union[str, datetime]]:
    return week_window(season, week)

@router.get("/player_props")
async def nfl_player_props(
    season: Optional[int] = None,
    week: Optional[int] = None,
    stats: str = Query("recYds,receptions,rushYds,passYds,passTDs", description="Comma list of stats"),
    include_markets: bool = True,
    bookmakers: Optional[str] = Query(None, description="Comma-separated list, e.g. 'draftkings,fanduel'"),
    region: Optional[str] = Query("us", description="Odds API region, e.g. us, us2, eu"),
    debug: bool = False,
):
    """
    Returns list of player prop projections (and edges when markets available).
    """
    if season is None or week is None:
        season, week = current_season_week()

    want_stats = [s.strip() for s in stats.split(",") if s.strip()]
    start_bound, end_bound = _week_bounds(season, week)
    events = await _week_games(season, week)
    games = _build_game_lookup(events)
    logger.info("NFL props: season=%s week=%s games=%d", season, week, len(games))

    bks = [b.strip() for b in (bookmakers or "").split(",") if b.strip()] or None
    market_blob, diag = ({}, {})
    if include_markets:
        try:
            market_blob, diag = await asyncio.wait_for(
                get_nfl_player_prop_lines(
                    season=season,
                    week=week,
                    want_stats=want_stats,
                    start_iso=start_bound,
                    end_iso=end_bound,
                    bookmakers=bks,
                    region=region or "us",
                    debug=debug,
                ),
                timeout=20.0,
            )
        except Exception as e:
            logger.exception("odds props fetch failed: %s", e)
            market_blob = {}
            diag = {"error": "fetch_failed"}

    rows: List[dict] = []

    # If odds returned nothing, you still get model-only projections when markets are absent (edges empty)
    for _, entry in (market_blob or {}).items():
        team = entry["team"]
        opp = entry["opponent"]

        game_total = 43.0
        team_spread_home = 0.0

        proj = project_player_props(
            player_name=entry["player"],
            position=entry.get("position"),
            team=team,
            opponent=opp,
            game_total=game_total,
            team_spread_home=team_spread_home,
        )
        proj_filt = {k: v for k, v in proj.items() if k in want_stats}

        market = {}
        edges = {}
        for stat in want_stats:
            ml = (entry.get("markets") or {}).get(stat)
            if ml is not None:
                market[stat] = ml
                if stat in proj_filt:
                    edges[stat] = round(proj_filt[stat] - ml, 2)

        rows.append({
            "player": entry["player"],
            "team": team,
            "opponent": opp,
            "position": entry.get("position"),
            "model": proj_filt,
            "market": ({"book": entry.get("book"), **market} if market else {}),
            "edge": (edges if edges else {}),
        })

    return {
        "season": season,
        "week": week,
        "rows": rows,
        "diagnostics": diag if debug else None,
        "note": "If rows is empty, try adding ?bookmakers=draftkings,fanduel or ?region=eu or remove filters.",
    }

@router.get("/player_props/edges")
async def nfl_player_props_edges(
    season: Optional[int] = None,
    week: Optional[int] = None,
    stat: str = Query("recYds", description="recYds | rushYds | passYds | receptions | passTDs"),
    limit: int = 25,
    bookmakers: Optional[str] = Query(None, description="Comma-separated (optional)"),
    region: Optional[str] = Query("us", description="Odds API region, e.g. us, us2, eu"),
    debug: bool = False,
):
    data = await nfl_player_props(
        season=season,
        week=week,
        stats=stat,
        include_markets=True,
        bookmakers=bookmakers,
        region=region,
        debug=debug,
    )
    rows = data.get("rows", [])

    def _edge_abs(r):
        v = (r.get("edge") or {}).get(stat)
        return abs(v) if isinstance(v, (int, float)) else -1.0

    ranked = sorted(rows, key=_edge_abs, reverse=True)
    return {
        "season": data.get("season"),
        "week": data.get("week"),
        "stat": stat,
        "rows": ranked[:max(1, min(limit, 100))],
        "diagnostics": data.get("diagnostics") if debug else None,
    }

# Natural-language mapping endpoint stays as you had it (edges_simple)
from fastapi import HTTPException  # ensure imported above if not already

_label_map = {
    "receivingyards": "recYds", "recyds": "recYds", "rec yds": "recYds", "receiving yds": "recYds", "receiving": "recYds",
    "receptions": "receptions", "recs": "receptions",
    "rushingyards": "rushYds", "rushyds": "rushYds", "rush yds": "rushYds", "rushing yds": "rushYds", "rushing": "rushYds",
    "passingyards": "passYds", "passyds": "passYds", "pass yds": "passYds", "passing yds": "passYds", "passing": "passYds",
    "passingtds": "passTDs", "pass tds": "passTDs", "pass td": "passTDs",
}
def _canon_stat(label: str) -> str | None:
    key = "".join(ch for ch in (label or "").lower() if ch.isalnum() or ch.isspace()).strip()
    return _label_map.get(key)

@router.get("/player_props/edges_simple")
async def nfl_player_props_edges_simple(
    season: Optional[int] = None,
    week: Optional[int] = None,
    statLabel: str = Query("receiving yards"),
    limit: int = 25,
    bookmakers: Optional[str] = Query(None),
    region: Optional[str] = Query("us"),
    debug: bool = False,
):
    canon = _canon_stat(statLabel)
    if not canon:
        raise HTTPException(status_code=400, detail="Unsupported statLabel. Use receiving yards | receptions | rushing yards | passing yards | passing tds")
    return await nfl_player_props_edges(
        season=season, week=week, stat=canon, limit=limit, bookmakers=bookmakers, region=region, debug=debug
    )
