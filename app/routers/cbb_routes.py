# app/routers/cbb_routes.py
from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
import logging

from app.models.cbb_types import GameLite, Projection, MatchupDetail
from app.models.cbb_model import project_cbb_1h
from app.services.espn_cbb import (
    get_games_for_date,
    extract_game_lite,
    extract_matchup_detail,
)
from app.services.odds_api import get_cbb_1h_lines

logger = logging.getLogger("app")
router = APIRouter()


# ----------------- Helpers -----------------
def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


# ----------------- Schedule -----------------
@router.get("/schedule", response_model=List[GameLite])
async def cbb_schedule(date: Optional[str] = None):
    try:
        games = await get_games_for_date(date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("schedule failed for date=%s: %s", date, e)
        return []
    logger.info("schedule: %d games for date=%s", len(games), date)
    return [extract_game_lite(ev) for ev in games]


# ----------------- Projections -----------------
@router.get("/projections", response_model=List[Projection])
async def cbb_projections(date: Optional[str] = None, scope: str = "1H"):
    if scope not in ("1H", "FG"):
        raise HTTPException(400, "scope must be '1H' or 'FG'")
    try:
        games = await get_games_for_date(date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("projections failed for date=%s: %s", date, e)
        return []
    logger.info("projections: %d games for date=%s", len(games), date)

    out: List[Projection] = []
    for ev in games:
        lite = extract_game_lite(ev)
        if scope == "1H":
            m = project_cbb_1h(lite["homeTeam"], lite["awayTeam"])
        else:
            base = project_cbb_1h(lite["homeTeam"], lite["awayTeam"])
            m = {
                "projTotal": round(base["projTotal"] * 2.02, 1),
                "projSpreadHome": round(base["projSpreadHome"] * 2.0, 1),
                "confidence": base["confidence"],
            }
        out.append({"gameId": lite["gameId"], "scope": scope, **m})
    return out


# ----------------- Single Matchup -----------------
@router.get("/matchups/{gameId}", response_model=MatchupDetail)
async def cbb_matchup(gameId: str, scope: str = "1H"):
    try:
        games = await get_games_for_date()
    except Exception as e:
        logger.exception("matchup failed for gameId=%s: %s", gameId, e)
        raise HTTPException(404, "Could not load matchups")
    ev = next((g for g in games if g.get("id") == gameId), None)
    if not ev:
        raise HTTPException(404, "Game not found")
    base = extract_matchup_detail(ev)

    if scope == "1H":
        m = project_cbb_1h(base["homeTeam"], base["awayTeam"])
    else:
        base_m = project_cbb_1h(base["homeTeam"], base["awayTeam"])
        m = {
            "projTotal": round(base_m["projTotal"] * 2.02, 1),
            "projSpreadHome": round(base_m["projSpreadHome"] * 2.0, 1),
            "confidence": base_m["confidence"],
        }
    return {**base, "notes": None, "model": {"gameId": base["gameId"], "scope": scope, **m}}


# ----------------- Slate (Schedule + Model + optional Markets/Edges) -----------------
@router.get("/slate")
async def cbb_slate(
    date: Optional[str] = None,
    scope: str = Query("1H", pattern="^(1H|FG)$"),
    include_markets: bool = True,
):
    """
    Returns schedule rows with model numbers; optionally includes market lines and edges.
    If markets are unavailable (no ODDS_API_KEY or provider empty), market/edge fields are null.
    """
    try:
        games = await get_games_for_date(date)
    except Exception as e:
        logger.exception("slate failed for date=%s: %s", date, e)
        return []
    logger.info("slate: %d games for date=%s", len(games), date)

    # Fetch markets (graceful if key missing or provider down)
    markets = {}
    if include_markets:
        try:
            markets = await get_cbb_1h_lines(None)
        except Exception as e:
            logger.exception("odds fetch failed: %s", e)
            markets = {}

    rows = []
    for ev in games:
        lite = extract_game_lite(ev)

        # Model
        if scope == "1H":
            m = project_cbb_1h(lite["homeTeam"], lite["awayTeam"])
        else:
            base = project_cbb_1h(lite["homeTeam"], lite["awayTeam"])
            m = {
                "projTotal": round(base["projTotal"] * 2.02, 1),
                "projSpreadHome": round(base["projSpreadHome"] * 2.0, 1),
                "confidence": base["confidence"],
            }

        # Market matching by normalized "away|home"
        token = f"{_norm(lite['awayTeam'])}|{_norm(lite['homeTeam'])}"
        mk = markets.get(token, {}) if include_markets else {}
        market_total = mk.get("marketTotal")
        market_spread_home = mk.get("marketSpreadHome")

        edge_total = None
        edge_spread = None
        if isinstance(market_total, (int, float)):
            edge_total = round(m["projTotal"] - market_total, 2)
        if isinstance(market_spread_home, (int, float)):
            edge_spread = round(m["projSpreadHome"] - market_spread_home, 2)

        rows.append({
            **lite,
            "model": {"scope": scope, **m},
            "market": {
                "total": market_total,
                "spreadHome": market_spread_home,
                "book": mk.get("book"),
            },
            "edge": {
                "total": edge_total,
                "spreadHome": edge_spread,
            },
        })
    return rows


# ----------------- Edges (ranked) -----------------
@router.get("/edges")
async def cbb_edges(
    date: Optional[str] = None,
    scope: str = Query("1H", pattern="^(1H|FG)$"),
    sort: str = Query("spread", pattern="^(spread|total)$"),
    limit: int = 25,
):
    """
    Returns slate with model + market + edge, sorted by absolute edge.
    If markets are unavailable, edge fields are null and such rows sort to the bottom.
    """
    rows = await cbb_slate(date=date, scope=scope, include_markets=True)
    key = "spreadHome" if sort == "spread" else "total"

    def _abs_edge(row):
        val = (row.get("edge") or {}).get(key)
        return abs(val) if isinstance(val, (int, float)) else -1.0

    ranked = sorted(rows, key=_abs_edge, reverse=True)
    return ranked[:max(1, min(limit, 100))]


# ----------------- Mock Slate (No ESPN Required) -----------------
@router.get("/mock_slate")
async def cbb_mock_slate(scope: str = "1H"):
    sample_games = [
        {"gameId": "M1", "homeTeam": "Purdue Boilermakers", "awayTeam": "Evansville Purple Aces"},
        {"gameId": "M2", "homeTeam": "Duke Blue Devils", "awayTeam": "Michigan State Spartans"},
    ]
    out = []
    for g in sample_games:
        if scope == "1H":
            m = project_cbb_1h(g["homeTeam"], g["awayTeam"])
        else:
            base = project_cbb_1h(g["homeTeam"], g["awayTeam"])
            m = {
                "projTotal": round(base["projTotal"] * 2.02, 1),
                "projSpreadHome": round(base["projSpreadHome"] * 2.0, 1),
                "confidence": base["confidence"],
            }
        out.append({**g, "model": {"scope": scope, **m}})
    return out
