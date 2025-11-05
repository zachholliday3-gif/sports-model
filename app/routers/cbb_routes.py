# app/routers/cbb_routes.py
from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
import asyncio
import logging

from app.services.espn_cbb import (
    get_games_for_date,
    extract_game_lite,
    extract_matchup_detail,
)
from app.models.cbb_model import project_cbb_1h
from app.models.cbb_types import GameLite, Projection, MatchupDetail
from app.services.odds_api import get_cbb_1h_lines  # shared odds service

logger = logging.getLogger("app.cbb")
router = APIRouter(tags=["cbb"])

def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())

# -------- Schedule --------
@router.get("/schedule", response_model=List[GameLite])
async def cbb_schedule(date: Optional[str] = None):
    """
    CBB schedule by date (YYYYMMDD). Defaults to US/Eastern 'today' if missing.
    """
    try:
        games = await get_games_for_date(date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("cbb schedule failed for date=%s: %s", date, e)
        return []
    rows = [extract_game_lite(ev) for ev in games]
    logger.info("CBB schedule: date=%s -> %d", date, len(rows))
    return rows

# -------- Slate (1H/FG projections) --------
@router.get("/slate")
async def cbb_slate(
    date: Optional[str] = None,
    scope: str = Query("1H", pattern="^(1H|FG)$"),
    include_markets: bool = False,  # default safe/fast
):
    try:
        games = await get_games_for_date(date)
    except Exception as e:
        logger.exception("cbb slate failed for date=%s: %s", date, e)
        return []

    logger.info("CBB slate params: date=%s scope=%s include_markets=%s", date, scope, include_markets)
    logger.info("CBB games fetched: %d", len(games))

    markets = {}
    if include_markets:
        try:
            # using FG lines as proxy unless your plan supports 1H markets
            markets = await asyncio.wait_for(get_cbb_1h_lines(), timeout=8.0)
            logger.info("CBB markets loaded: %d", len(markets))
        except Exception as e:
            logger.exception("cbb odds fetch failed or timed out: %s", e)
            markets = {}

    rows = []
    for ev in games:
        lite = extract_game_lite(ev)

        # base model (1H)
        m1h = project_cbb_1h(lite["homeTeam"], lite["awayTeam"])
        if scope == "1H":
            m = m1h
        else:
            # simple FG proxy from 1H — refine later
            m = {
                "projTotal": round(m1h["projTotal"] * 2.02, 1),
                "projSpreadHome": round(m1h["projSpreadHome"] * 2.0, 1),
                "confidence": m1h["confidence"],
            }

        token = f"{_norm(lite['awayTeam'])}|{_norm(lite['homeTeam'])}"
        mk = markets.get(token, {}) if include_markets else {}
        mt = mk.get("marketTotal")
        ms = mk.get("marketSpreadHome")
        edge_total = round(m["projTotal"] - mt, 2) if isinstance(mt, (int, float)) else None
        edge_spread = round(m["projSpreadHome"] - ms, 2) if isinstance(ms, (int, float)) else None

        rows.append({
            **lite,
            "model": {"scope": scope, **m},
            "market": {"total": mt, "spreadHome": ms, "book": mk.get("book")},
            "edge": {"total": edge_total, "spreadHome": edge_spread},
        })
    return rows

# -------- Edges (ranked) --------
@router.get("/edges")
async def cbb_edges(
    date: Optional[str] = None,
    scope: str = Query("1H", pattern="^(1H|FG)$"),
    sort: str = Query("spread", pattern="^(spread|total)$"),
    limit: int = 25,
):
    rows = await cbb_slate(date=date, scope=scope, include_markets=True)
    key = "spreadHome" if sort == "spread" else "total"

    def _abs_edge(row):
        val = (row.get("edge") or {}).get(key)
        return abs(val) if isinstance(val, (int, float)) else -1.0

    ranked = sorted(rows, key=_abs_edge, reverse=True)
    return ranked[:max(1, min(limit, 100))]

# -------- Projections alias (for GPT intent) --------
@router.get("/projections")
async def cbb_projections_api(
    date: Optional[str] = None,
    scope: str = Query("1H", pattern="^(1H|FG)$"),
    include_markets: bool = False,
):
    return await cbb_slate(date=date, scope=scope, include_markets=include_markets)

# -------- Single matchup (optional) --------
@router.get("/matchups/{gameId}", response_model=MatchupDetail)
async def cbb_matchup(gameId: str, scope: str = "1H"):
    try:
        games = await get_games_for_date()
    except Exception as e:
        logger.exception("cbb matchup list failed: %s", e)
        raise HTTPException(404, "Could not load matchups")

    ev = next((g for g in games if g.get("id") == gameId), None)
    if not ev:
        raise HTTPException(404, "Game not found")

    base = extract_matchup_detail(ev)

    m1h = project_cbb_1h(base["homeTeam"], base["awayTeam"])
    if scope == "1H":
        model = m1h
    else:
        model = {
            "projTotal": round(m1h["projTotal"] * 2.02, 1),
            "projSpreadHome": round(m1h["projSpreadHome"] * 2.0, 1),
            "confidence": m1h["confidence"],
        }

    return {**base, "notes": None, "model": {"gameId": base["gameId"], "scope": scope, **model}}
