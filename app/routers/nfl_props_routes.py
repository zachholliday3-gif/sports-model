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


def _normalize_result(
    raw: Any,
    season_default: Optional[int],
    week_default: Optional[int],
) -> Dict[str, Any]:
    """
    Normalize whatever get_nfl_player_prop_lines returns into:
    {
      "rows": [...],
      "season": <int or None>,
      "week": <int or None>,
      "diagnostics": {...}
    }

    Handles dicts or tuples.
    """
    # Case 1: already a dict
    if isinstance(raw, dict):
        rows = raw.get("rows") or []
        season_out = raw.get("season", season_default)
        week_out = raw.get("week", week_default)
        diagnostics = raw.get("diagnostics") or {}
        return {
            "rows": rows,
            "season": season_out,
            "week": week_out,
            "diagnostics": diagnostics,
        }

    # Case 2: tuple-like
    if isinstance(raw, (tuple, list)):
        # Guess the structure
        if len(raw) == 2:
            # (rows, diagnostics)
            rows = raw[0] or []
            diagnostics = raw[1] or {}
            season_out = season_default
            week_out = week_default
        elif len(raw) >= 3:
            # (rows, season, week, [diagnostics])
            rows = raw[0] or []
            season_out = raw[1] if raw[1] is not None else season_default
            week_out = raw[2] if raw[2] is not None else week_default
            diagnostics = raw[3] if len(raw) > 3 and raw[3] is not None else {}
        else:
            rows = raw[0] or []
            season_out = season_default
            week_out = week_default
            diagnostics = {}

        return {
            "rows": rows,
            "season": season_out,
            "week": week_out,
            "diagnostics": diagnostics,
        }

    # Fallback
    return {
        "rows": [],
        "season": season_default,
        "week": week_default,
        "diagnostics": {"warning": "unexpected_result_type"},
    }


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
    """
    Aggregated NFL player props across multiple stats.
    """
    if not stats:
        stats = "recYds,rushYds,passYds,receptions,passTDs"

    out_rows = []
    diagnostics_agg: Dict[str, Any] = {}
    chosen_season = season
    chosen_week = week

    for stat in stats.split(","):
        stat = stat.strip()
        if not stat:
            continue

        stat_positions = _default_positions_for_stat(stat, positions)

        try:
            # POSitional call: (season, week, stat, positions, bookmakers, region, fast, debug)
            raw_result = await get_nfl_player_prop_lines(
                season,
                week,
                stat,
                stat_positions,
                None,   # bookmakers
                None,   # region
                fast,
                False,  # debug
            )
        except Exception as e:
            logger.exception("nfl_player_props failed for stat=%s: %s", stat, e)
            continue

        norm = _normalize_result(raw_result, season, week)

        if chosen_season is None:
            chosen_season = norm.get("season")
        if chosen_week is None:
            chosen_week = norm.get("week")

        rows = norm.get("rows") or []
        for r in rows:
            if not include_markets:
                r = {**r, "market": None, "edge": None}
            out_rows.append(r)

        diagnostics_agg[stat] = norm.get("diagnostics") or {}

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
        raw_result = await get_nfl_player_prop_lines(
            season,
            week,
            stat,
            stat_positions,
            bookmakers,
            region,
            fast,
            debug,
        )
    except Exception as e:
        logger.exception("nfl_player_prop_edges failed: %s", e)
        raise HTTPException(status_code=500, detail="fetch_failed")

    norm = _normalize_result(raw_result, season, week)
    rows = norm.get("rows") or []
    if limit:
        rows = rows[:limit]

    return {
        "season": norm.get("season"),
        "week": norm.get("week"),
        "stat": stat,
        "rows": rows,
        "diagnostics": norm.get("diagnostics"),
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
    """
    GPT-friendly NFL prop edges with caching.
    Uses natural-language statLabel and sensible default positions,
    then calls the underlying service and caches by (season, week, stat, positions, bookmakers, region, fast).
    """
    label_norm = statLabel.strip().lower()
    if label_norm not in STAT_LABEL_MAP:
        raise HTTPException(status_code=400, detail="Unsupported statLabel.")

    stat = STAT_LABEL_MAP[label_norm]
    stat_positions = _default_positions_for_stat(stat, positions)

    # --- Try cache first ---
    if not debug:
        cached = _get_cached_edges(season, week, stat, stat_positions, bookmakers, region, fast)
        if cached:
            logger.info("NFL props edges_simple cache hit: %s %s %s %s", season, week, stat, stat_positions)
            rows = cached.get("rows") or []
            if limit:
                rows = rows[:limit]
            return {**cached, "rows": rows, "stat": stat}

    # --- Fetch fresh ---
    try:
        raw_result = await get_nfl_player_prop_lines(
            season,
            week,
            stat,
            stat_positions,
            bookmakers,
            region,
            fast,
            debug,
        )
    except Exception as e:
        logger.exception("nfl_player_prop_edges_simple failed: %s", e)
        raise HTTPException(status_code=500, detail="fetch_failed")

    norm = _normalize_result(raw_result, season, week)
    rows = norm.get("rows") or []
    if limit:
        rows = rows[:limit]

    response = {
        "season": norm.get("season"),
        "week": norm.get("week"),
        "stat": stat,
        "rows": rows,
        "diagnostics": norm.get("diagnostics"),
    }

    if not debug:
        _set_cached_edges(season, week, stat, stat_positions, bookmakers, region, fast, response)

    return response
