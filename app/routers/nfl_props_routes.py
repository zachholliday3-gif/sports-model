# app/routers/nfl_props_routes.py
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional
from fastapi import APIRouter, Query, HTTPException

from app.models.nfl_props_model import project_player_props
from app.services.nfl_weeks import current_season_week, week_window
from app.services.odds_api_nfl_props import get_nfl_player_prop_lines

logger = logging.getLogger("app.nfl_props")
router = APIRouter(tags=["nfl-props"])

def _parse_positions(s: Optional[str]) -> Optional[set]:
    if not s:
        return None
    vals = [p.strip().upper() for p in s.split(",") if p.strip()]
    return set(vals) if vals else None

def _pos_ok(entry_pos: Optional[str], want: Optional[set]) -> bool:
    if not want:
        return True
    if not entry_pos:
        return False
    if entry_pos == "WR/TE":
        return bool({"WR", "TE"} & want)
    return entry_pos in want

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

@router.get("/player_props")
async def nfl_player_props(
    season: Optional[int] = None,
    week: Optional[int] = None,
    stats: str = Query("recYds,receptions,rushYds,passYds,passTDs"),
    include_markets: bool = True,
    bookmakers: Optional[str] = Query(None, description="CSV, e.g. 'draftkings,fanduel'"),
    region: Optional[str] = Query("us"),
    debug: bool = False,
    fast: bool = Query(False, description="Quick path (recYds only)"),
    positions: Optional[str] = Query(None, description="CSV positions filter, e.g. 'WR,TE'"),
):
    if season is None or week is None:
        season, week = current_season_week()

    want_stats = ["recYds"] if fast else [s.strip() for s in stats.split(",") if s.strip()]
    bks = [b.strip() for b in (bookmakers or "").split(",") if b.strip()] or None
    start_bound, end_bound = week_window(season, week)

    # Hard wall timeout for Actions
    wait_secs = 6.0 if fast else 12.0

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
            timeout=wait_secs,
        )
    except asyncio.TimeoutError:
        logger.warning("odds props fetch timeout (fast=%s)", fast)
        market_blob, diag = {}, {"error": "timeout"}
    except Exception as e:
        logger.exception("odds props fetch failed: %s", e)
        market_blob, diag = {}, {"error": "fetch_failed"}

    want_positions = _parse_positions(positions)
    rows: List[dict] = []
    for _, entry in (market_blob or {}).items():
        if not _pos_ok(entry.get("position"), want_positions):
            continue

        team, opp = entry["team"], entry["opponent"]
        model = project_player_props(
            player_name=entry["player"],
            position=entry.get("position"),
            team=team,
            opponent=opp,
            game_total=43.0,
            team_spread_home=0.0,
        )
        model = {k: v for k, v in model.items() if k in want_stats}

        market = {}
        edges = {}
        for stat in want_stats:
            ml = (entry.get("markets") or {}).get(stat)
            if ml is not None:
                market[stat] = ml
                if stat in model:
                    edges[stat] = round(model[stat] - ml, 2)

        rows.append({
            "player": entry["player"],
            "team": team,
            "opponent": opp,
            "position": entry.get("position"),
            "model": model,
            "market": ({"book": entry.get("book"), **market} if market else {}),
            "edge": edges,
        })

    return {
        "season": season,
        "week": week,
        "rows": rows,
        "diagnostics": diag if debug else None,
        "note": "Use positions=WR,TE to filter. Fast mode returns recYds only.",
    }

@router.get("/player_props/edges")
async def nfl_player_props_edges(
    season: Optional[int] = None,
    week: Optional[int] = None,
    stat: str = Query("recYds"),
    limit: int = 25,
    bookmakers: Optional[str] = Query(None),
    region: Optional[str] = Query("us"),
    debug: bool = False,
    fast: bool = Query(False),
    positions: Optional[str] = Query(None),
):
    data = await nfl_player_props(
        season=season, week=week, stats=stat, include_markets=True,
        bookmakers=bookmakers, region=region, debug=debug, fast=fast, positions=positions,
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

@router.get("/player_props/edges_simple")
async def nfl_player_props_edges_simple(
    season: Optional[int] = None,
    week: Optional[int] = None,
    statLabel: str = Query("receiving yards"),
    limit: int = 15,
    bookmakers: Optional[str] = Query(None),
    region: Optional[str] = Query("us"),
    debug: bool = False,
    fast: bool = Query(True, description="Default fast mode for Actions"),
    positions: Optional[str] = Query(None, description="CSV; defaults to WR,TE for receiving/receptions"),
):
    key = "".join(ch for ch in (statLabel or "").lower() if ch.isalnum() or ch.isspace()).strip()
    canon = {
        "receivingyards": "recYds", "recyds": "recYds", "rec yds": "recYds", "receiving yds": "recYds", "receiving": "recYds",
        "receptions": "receptions", "recs": "receptions",
        "rushingyards": "rushYds", "rushyds": "rushYds", "rush yds": "rushYds", "rushing yds": "rushYds", "rushing": "rushYds",
        "passingyards": "passYds", "passyds": "passYds", "pass yds": "passYds", "passing yds": "passYds", "passing": "passYds",
        "passingtds": "passTDs", "pass tds": "passTDs", "pass td": "passTDs",
    }.get(key)

    if not canon:
        raise HTTPException(status_code=400, detail="Unsupported statLabel.")

    if positions is None and canon in ("recYds", "receptions"):
        positions = "WR,TE"

    data = await nfl_player_props_edges(
        season=season, week=week, stat=canon, limit=limit, bookmakers=bookmakers, region=region,
        debug=debug, fast=fast, positions=positions,
    )
    # Ensure we never blow out GPT time limits with huge payloads
    data["rows"] = data.get("rows", [])[:max(1, min(limit, 25))]
    return data
