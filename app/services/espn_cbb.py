# app/services/espn_cbb.py
from __future__ import annotations

import httpx
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone
import logging

logger = logging.getLogger("app.espn_cbb")

# ESPN "site" scoreboard base for Men's college basketball
SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _ny_today_yyyymmdd() -> str:
    """Treat 'today' as America/New_York (project convention: UTC-5 fallback)."""
    ny_now = datetime.now(timezone.utc) - timedelta(hours=5)
    return ny_now.strftime("%Y%m%d")


async def _get_json(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Tiny retry wrapper."""
    last = None
    async with httpx.AsyncClient(timeout=12.0, headers=HEADERS) as client:
        for attempt in range(2):
            try:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
                logger.warning("espn_cbb _get_json attempt %d failed: %s", attempt + 1, e)
    raise last or RuntimeError("unknown http error")


def _scoreboard_params_for_date(date_yyyymmdd: str, d1_only: bool) -> Dict[str, Any]:
    """
    ESPN accepts a dates range like 20251107-20251107; limit is generous.
    Division I filter = groups=50 (same as ESPN UI 'Division I').
    """
    params: Dict[str, Any] = {
        "dates": f"{date_yyyymmdd}-{date_yyyymmdd}",
        "limit": 500,
    }
    if d1_only:
        params["groups"] = 50
    return params


def extract_game_lite(event: Dict[str, Any]) -> Dict[str, Any]:
    comp = (event.get("competitions") or [{}])[0]
    comps = comp.get("competitors") or []
    home_name = away_name = None
    for c in comps:
        side = (c.get("homeAway") or "").lower()
        t = c.get("team") or {}
        nm = t.get("displayName") or t.get("name")
        if side == "home":
            home_name = nm
        elif side == "away":
            away_name = nm
    return {
        "gameId": event.get("id"),
        "homeTeam": home_name,
        "awayTeam": away_name,
        "startTime": comp.get("date"),
    }


def extract_matchup_detail(event: Dict[str, Any]) -> Dict[str, Any]:
    lite = extract_game_lite(event)
    return {
        "gameId": lite["gameId"],
        "homeTeam": lite["homeTeam"],
        "awayTeam": lite["awayTeam"],
        "startTime": lite["startTime"],
        "notes": None,
    }


async def get_games_for_date(date: Optional[str] = None, d1_only: bool = True) -> List[Dict[str, Any]]:
    """
    Load ESPN CBB scoreboard for a single date.
    - date: 'YYYYMMDD' or None (today in NY)
    - d1_only: True -> adds 'groups=50' to return NCAA Division I only
    """
    d = (date or _ny_today_yyyymmdd()).strip()
    params = _scoreboard_params_for_date(d, d1_only=d1_only)
    logger.info("CBB get_games_for_date date=%s d1_only=%s params=%s", d, d1_only, params)

    data = await _get_json(SITE_BASE, params)
    events = data.get("events") or []
    logger.info("CBB get_games_for_date returned %d events", len(events))
    return events


# ---------- Optional: Top-25 helpers (safe to keep if you added Top-25 route) ----------
from typing import Optional as _Optional

def _team_rank(team: dict) -> _Optional[int]:
    try:
        r = (team.get("curatedRank") or {}).get("current", None)
        if r is None:
            r = team.get("rank", None)
        return int(r) if r is not None else None
    except Exception:
        return None

def _event_has_top25(event: dict, only_unranked_opponent: bool = False) -> bool:
    comps = (((event or {}).get("competitions") or [{}])[0].get("competitors")) or []
    ranks = []
    for c in comps:
        team = (c or {}).get("team") or {}
        ranks.append(_team_rank(team))
    if not ranks:
        return False
    top25_count = sum(1 for r in ranks if (r is not None and r <= 25))
    if top25_count == 0:
        return False
    if not only_unranked_opponent:
        return True
    return top25_count == 1

async def get_top25_for_date(date: _Optional[str] = None, only_unranked_opponent: bool = False, d1_only: bool = True) -> list[dict]:
    events = await get_games_for_date(date, d1_only=d1_only)
    return [ev for ev in events if _event_has_top25(ev, only_unranked_opponent)]
# --------------------------------------------------------------------------------------
