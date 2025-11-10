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

# Keyed by: (season, week, stat, positions, bookmakers, region, fast)
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


# ---------- Helper: default positions based on stat ----------

def _default_positions_for_stat(stat: str, positions: Optional[str]) -> str:
    """
    If positions is not provided, choose sensible defaults per stat.
    """
    if positions:
        return positions

    if stat == "recYds" or stat == "receptions":
        return "WR,TE"
    if stat == "rushYds":
        return "RB"
    if stat in ("passYds", "passTDs"):
        return "QB"

    return ""


# ====================================================================
# 1) Bulk props endpoint (less used by GPT, kept simple)
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
    """
    NFL player prop projections (with edges when markets available).

    This is a simple wrapper that can aggregate across multiple stats.
    The GPT primarily uses the edges_simple endpoint; this is kept
    for completeness.
    """
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

        # Choose sensible positions if not provided
        stat_positions = _default_positions_for_stat(stat, positions)

        try:
            result = await get_nfl_player_prop_lines(
                season=season,
                week=week,
                stat_key=stat,  # 🔑 match service signature
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
            # Optionally filter out market/edge if include_markets=False
            if not include_markets:
                r = {**r, "market": None, "edge": None}
            out_rows.append(r)

        diag = result.get("diagnostics") or {}
        diagnostics_agg[stat] = diag

    return {
        "season": chosen_season,
        "week": chosen_week,
        "note": None,
        "rows": out_rows,
        "diagnostics": diagnostics_agg,
    }


# ====================================================================
# 2) Raw edges endpoint (stat key directly)
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
    """
    Raw NFL player prop edges for a single internal stat key.
    The GPT should normally use /player_props/edges_simple instead.
    """
    stat = stat.strip()
    if not stat:
        raise HTTPException(status_code=400, detail="stat is required")

    stat_positions = _default_positions_for_stat(stat, positions)

    try:
        result = await get_nfl_player_prop_lines(
            season=season,
            week=week,
            stat_key=stat,  # 🔑 match service signature
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
# 3) GPT-friendly edges endpoint (statLabel + CACHE)
# ====================================================================

@router.get("/player_props/edges_simple")
async def nfl_player_prop_edges_simple(
    season: Optional[int] = Query(None),
    week: Optional[int] = Query(None),
    statLabel: str = Query(..., description="e.g. 'receiving yards', 'receptions', 'rushing yards', 'passing yards', 'passing tds'"),
    limit: int = Query(25, ge=1, le=200),
    bookmakers: Optional[str] = Query(None),
    region: Optional[str] = Query("us"),
    positions: Optional[str] = Query(None),
    fast: bool = Query(True),
    debug: bool = Query(False),
):
    """
    GPT-friendly NFL player prop edges:
    - statLabel is natural-language (receiving yards, receptions, etc)
    - We apply sensible default positions (WR/TE for receiving, etc)
    - We cache responses to reduce upstream rate limiting
    """
    label_norm = statLabel.strip().lower()
    if label_norm not in STAT_LABEL_MAP:
        raise HTTPException(status_code=400, detail="Unsupported statLabel.")

    stat = STAT_LABEL_MAP[label_norm]
    stat_positions = _default_positions_for_stat(stat, positions)

    # ---------- 1) Try cache first (if not debug) ----------
    if not debug:
        cached = _get_cached_edges(season, week, stat, stat_positions, bookmakers, region, fast)
        if cached is not None:
            logger.info(
                "NFL props edges_simple cache hit: season=%s week=%s stat=%s positions=%s",
                season, week, stat, stat_positions,
            )
            # Apply limit on top of cached rows
            rows = cached.get("rows") or []
            if limit:
                rows = rows[:limit]
            return {
                **cached,
                "rows": rows,
                "stat": stat,
            }

    # ---------- 2) Call underlying service ----------
    try:
        result = await get_nfl_player_prop_lines(
            season=season,
            week=week,
            stat_key=stat,  # 🔑 match service signature
            positions=stat_positions,
            bookmakers=bookmakers,
            region=region,
            fast=fast,
            debug=debug,
        )
    except Exception as e:
        logger.exception("nfl_player_prop_edges_simple failed: %s", e)
        raise HTTPException(status_code=500, detail="fetch_failed")

    # Enforce limit
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

    # ---------- 3) Store in cache ----------
    if not debug:
        _set_cached_edges(season, week, stat, stat_positions, bookmakers, region, fast, response)

    return response
