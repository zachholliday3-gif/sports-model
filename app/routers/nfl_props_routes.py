# app/routers/nfl_props_routes.py
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Query

from app.models.nfl_props_model import project_player_props
from app.services.nfl_weeks import current_season_week
from app.services.espn_nfl import extract_game_lite, get_games_for_range
from app.services.odds_api_nfl_props import get_nfl_player_prop_lines

from app.services.nfl_weeks import week_window

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

@router.get("/player_props")
async def nfl_player_props(
    season: Optional[int] = None,
    week: Optional[int] = None,
    stats: str = Query("passYds,passTDs,rushYds,recYds,receptions", description="Comma list of stats"),
    include_markets: bool = True,
):
    """
    Returns list of player prop projections (and edges when markets available).
    Shape per row:
      {
        player, team, opponent, position,
        model: { passYds?, passTDs?, rushYds?, recYds?, receptions? },
        market: { stat: line?, book? },
        edge:   { stat: model - market }
      }
    """
    if season is None or week is None:
        season, week = current_season_week()

    want_stats = [s.strip() for s in stats.split(",") if s.strip()]
    events = await _week_games(season, week)
    games = _build_game_lookup(events)
    logger.info("NFL props: season=%s week=%s games=%d", season, week, len(games))

    # Pull markets (this also gives us the player list)
    market_blob = {}
    if include_markets:
        try:
            market_blob = await asyncio.wait_for(
                get_nfl_player_prop_lines(season=season, week=week, want_stats=want_stats),
                timeout=10.0
            )
        except Exception as e:
            logger.exception("odds props fetch failed: %s", e)
            market_blob = {}

    rows: List[dict] = []

    # For each player entry from markets, compute model + edges
    for pkey, entry in (market_blob or {}).items():
        team = entry["team"]
        opp = entry["opponent"]
        token = entry.get("gameToken")
        g = games.get(token)

        # If we can, pull model inputs from the game (proj total/spread) using our existing team model
        if g:
            # crude mapping from our FG game model (we project FG totals/spreads at the game level already)
            # Use a neutral total/spread if none available in schedule
            # Since we don't have per-game projections stored here, we just assume league avg 43 and spread 0
            game_total = 43.0
            team_spread_home = 0.0 if team == g["homeTeam"] else 0.0
        else:
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

        # Filter projection to requested stats only
        proj_filt = {k: v for k, v in proj.items() if k in want_stats}

        market = {}
        edges = {}
        if include_markets:
            for stat in want_stats:
                ml = entry["markets"].get(stat)
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
            "market": {"book": entry.get("book"), **market} if market else {},
            "edge": edges if edges else {},
        })

    return {
        "season": season,
        "week": week,
        "rows": rows,
        "note": "Projections are heuristic baselines adjusted by pace/spread; markets used when available.",
    }


@router.get("/player_props/edges")
async def nfl_player_props_edges(
    season: Optional[int] = None,
    week: Optional[int] = None,
    stat: str = Query("recYds", description="Which stat to rank by (e.g. recYds,rushYds,passYds,receptions,passTDs)"),
    limit: int = 25,
):
    """
    Ranked edges for a single stat (descending by absolute edge).
    """
    data = await nfl_player_props(season=season, week=week, stats=stat, include_markets=True)
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
    }
