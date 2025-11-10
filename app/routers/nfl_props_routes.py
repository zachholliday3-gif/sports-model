# app/routers/nfl_props_routes.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
import logging

from app.services.odds_api_nfl_props import get_nfl_player_prop_lines

router = APIRouter(tags=["NFL Props"])
logger = logging.getLogger("app.nfl_props")

# ---------- Stat label → internal key mapping ----------

STAT_LABEL_MAP = {
    "receiving yards": "recYds",
    "receptions": "receptions",
    "rushing yards": "rushYds",
    "passing yards": "passYds",
    "passing tds": "passTDs",
}

# ---------- Simple in-memory cache for edges_simple ----------

_EDGE_CACHE: Dict[Tuple[int, int, str, str, str, str, bool], Dict[str, Any]] = {}
_EDGE_CACHE_TTL = timedelta(minutes=30)


def _make_cache_key(
    season: Optional[int],
    week: Optional[int],
    stat: str,
    positions: Optional[str],
    bookmakers: Optional[str],
    region: Optional[str],
    fast: bool,
) -> Tuple[int, int, str, str, str, str, bool]:
    return (
        season or 0,
        week or 0,
        stat,
        positions or "",
        bookmakers or "",
        region or "",
        fast,
    )


def _get_cached_edges(
    season: Optional[int],
    week: Optional[int],
    stat: str,
    positions: Optional[str],
    bookmakers: Optional[str],
    region: Optional[str],
    fast: bool,
):
    now = datetime.utcnow()
    key = _make_cache_key(season, week, stat, positions, bookmakers, region, fast)
    entry = _EDGE_CACHE.get(key)
    if not entry:
        return None
    if entry["expires_at"] < now:
        _EDGE_CACHE.pop(key, None)
        return None
    return entry["value"]


def _set_cached_edges(
    season: Optional[int],
    week: Optional[int],
    stat: str,
    positions: Optional[str],
    bookmakers: Optional[str],
    region: Optional[str],
    fast: bool,
    value: Any,
):
    now = datetime.utcnow()
    key = _make_cache_key(season, week, stat, positions, bookmakers, region, fast)
    _EDGE_CACHE[key] = {
        "value": value,
        "expires_at": now + _EDGE_CACHE_TTL,
    }


def _default_positions_for_stat(stat: str, positions: Optional[str]) -> str:
    """Default positions per stat."""
    if positions:
        return positions

    if stat in ("recYds", "receptions"):
        return "WR,TE"
    if stat == "rushYds":
        return "RB"
    if stat in ("passYds", "passTDs"):
        return "QB"

    return ""


# ====================================================================
# 1) Bulk props endpoint
# ====================================================================

@router.get("/player_props")
async def nfl_player_props(
    season: Optional[int] = Query(None),
    week: Optional[int] = Query(None),
    stats: Optional[str] = Query(
        None,
        description="CSV of internal stats: recYds,rushYds,passYds,receptions,passTDs"
    ),
    include_markets: bool = Query(True),
    positions: Optional[str] = Query(None),
    fast: bool = Query(False),
):
    """Aggregated props data across multiple stats."""
    if not stats:
        stats = "recYds,rushYds,passYds,receptions,passTDs"

    out_rows = []
    diagnostics_agg = {}
    chosen_season = season
    chosen_week = week

    for stat in stats.split(","):
        stat = stat.strip()
        if not stat:
            continue

        stat_positions = _default_positions_for_stat(stat, positions)

        try:
            result = await get_nfl_player_prop_lines(
                season=season,
                week=week,
                stat=stat,  # ✅ using 'stat'
                positions=stat_positions,
                bookmakers=None,
                region=None,
                fast=fast,
                debug=False,
            )
        except Exception as e:
            logger.exception("nfl_player_props failed for stat=%s: %s", stat, e)
            continue

        if chosen_season is None:
            chosen_season = result.get("season")
        if chosen_week is None:
            chosen_week = result.get("week")

        rows = result.get("rows") or []
        for r in rows:
            if not include_markets:
                r = {**r, "market": None, "edge": None}
            out_rows.append(r)

        diagnostics_agg[stat] = result.get("diagnostics") or {}

    return {
        "season": chosen_season,
        "week": chosen_week,
        "rows": out_rows,
        "diagnostics": diagnostics_agg,
    }


# ====================================================================
# 2) Raw edges endpoint
# ====================================================================

@router.get("/player_props/edges")
async def nfl_player_prop_edges(
    season: Optional[int] = Query(None),
    week: Optional[int] = Query(None),
    stat: str = Query(..., description="Internal stat key: recYds,rushYds,passYds,receptions,passTDs"),
    limit: int = Query(25, ge=1, le=200),
    positions: Optional[str] = Query(None),
    bookmakers: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    fast: bool = Query(True),
    debug: bool = Query(False),
):
    """Raw NFL player prop edges for a given stat."""
    stat = stat.strip()
    if not stat:
        raise HTTPException(status_code=400, detail="stat is required")

    stat_positions = _default_positions_for_stat(stat, positions)

    try:
        result = await get_nfl_player_prop_lines(
            season=season,
            week=week,
            stat=stat,  # ✅ using 'stat'
            positions=stat_positions,
            bookmakers=bookmakers,
            region=region,
            fast=fast,
            debug=debug,
        )
    except Exception as e:
        logger.exception("nfl_player_prop_edges failed: %s", e)
        raise HTTPException(status_code=500, detail="fetch_failed")

    rows = result.get("rows") or []
    if limit:
        rows = rows[:limit]

    return {
        "season": result.get("season"),
        "week": result.get("week"),
        "stat": stat,
        "rows": rows,
        "diagnostics": result.get("diagnostics"),
    }


# ====================================================================
# 3) GPT-friendly cached edges endpoint
# ====================================================================

@router.get("/player_props/edges_simple")
async def nfl_player_prop_edges_simple(
    season: Optional[int] = Query(None),
    week: Optional[int] = Query(None),
    statLabel: str = Query(..., description="e.g. receiving yards, receptions, rushing yards, passing yards, passing tds"),
    limit: int = Query(25, ge=1, le=200),
    bookmakers: Optional[str] = Query(None),
    region: Optional[str] = Query("us"),
    positions: Optional[str] = Query(None),
    fast: bool = Query(True),
    debug: bool = Query(False),
):
    """GPT-friendly edges endpoint with caching."""
    label_norm = statLabel.strip().lower()
    if label_norm not in STAT_LABEL_MAP:
        raise HTTPException(status_code=400, detail="Unsupported statLabel.")

    stat = STAT_LABEL_MAP[label_norm]
    stat_positions = _default_positions_for_stat(stat, positions)

    # --- Try cache first ---
    if not debug:
        cached = _get_cached_edges(season, week, stat, stat_positions, bookmakers, region, fast)
        if cached:
            logger.info("Cache hit: %s %s %s %s", season, week, stat, stat_positions)
            rows = cached.get("rows") or []
            if limit:
                rows = rows[:limit]
            return {**cached, "rows": rows, "stat": stat}

    # --- Fetch fresh ---
    try:
        result = await get_nfl_player_prop_lines(
            season=season,
            week=week,
            stat=stat,  # ✅ using 'stat'
            positions=stat_positions,
            bookmakers=bookmakers,
            region=region,
            fast=fast,
            debug=debug,
        )
    except Exception as e:
        logger.exception("nfl_player_prop_edges_simple failed: %s", e)
        raise HTTPException(status_code=500, detail="fetch_failed")

    rows = result.get("rows") or []
    if limit:
        rows = rows[:limit]

    response = {
        "season": result.get("season"),
        "week": result.get("week"),
        "stat": stat,
        "rows": rows,
        "diagnostics": result.get("diagnostics"),
    }

    if not debug:
        _set_cached_edges(season, week, stat, stat_positions, bookmakers, region, fast, response)

    return response
