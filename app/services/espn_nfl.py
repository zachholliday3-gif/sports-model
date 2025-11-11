# app/services/espn_nfl.py
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx

NY = ZoneInfo("America/New_York")
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# ESPN sometimes rotates where the working host is. Try both:
BASE_URLS = [
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
]


def _today_yyyymmdd() -> str:
    return datetime.now(NY).strftime("%Y%m%d")


def _coerce_yyyymmdd(date_str: Optional[str]) -> str:
    """
    Accepts:
      - None -> today (US/Eastern)
      - 'YYYYMMDD' -> returns as-is if valid
      - 'YYYY-MM-DD' -> converts to 'YYYYMMDD'
    Raises ValueError on other inputs.
    """
    if not date_str:
        return _today_yyyymmdd()
    ds = date_str.strip()
    if len(ds) == 10 and ds[4] == "-" and ds[7] == "-":
        dt = datetime.strptime(ds, "%Y-%m-%d")
        return dt.strftime("%Y%m%d")
    if len(ds) == 8 and ds.isdigit():
        return ds
    raise ValueError("date must be YYYYMMDD (or YYYY-MM-DD)")


async def _get_json(url: str, params: Dict[str, Any]) -> Any:
    async with httpx.AsyncClient(timeout=12.0, headers=HEADERS) as client:
        last = None
        for i in range(2):
            try:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
                await asyncio.sleep(0.35 * (i + 1))
        raise last or RuntimeError("unknown http error")


async def _try_variants(date_str: str) -> List[Dict[str, Any]]:
    """
    Try ESPN host/param variants NFL accepts:
      - dates=YYYYMMDD
      - dates=YYYYMMDD-YYYYMMDD
    Return the first non-empty events list; otherwise [].
    """
    candidates: List[tuple[str, Dict[str, Any]]] = []
    single = {"dates": date_str, "limit": 500}
    rng = {"dates": f"{date_str}-{date_str}", "limit": 500}

    for base in BASE_URLS:
        candidates.append((base, single))
        candidates.append((base, rng))

    for url, params in candidates:
        try:
            data = await _get_json(url, params)
            events = data.get("events") or []
            if isinstance(events, list) and events:
                return events
        except Exception:
            continue
    return []


async def get_games_for_date(date: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Loads NFL games for a given date using robust ESPN variants.
    If no events are returned, we do NOT jump to adjacent dates automatically here
    (week-based router handles widening/fallback).
    """
    d = _coerce_yyyymmdd(date)
    return await _try_variants(d)


async def get_games_for_range(start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """
    Inclusive day range in US/Eastern. Calls get_games_for_date() per day.
    """
    start_d = start.astimezone(NY).date()
    end_d = end.astimezone(NY).date()
    if end_d < start_d:
        start_d, end_d = end_d, start_d

    out: List[Dict[str, Any]] = []
    cur = start_d
    while cur <= end_d:
        ds = cur.strftime("%Y%m%d")
        try:
            evs = await get_games_for_date(ds)
            out.extend(evs)
        except Exception:
            pass
        cur += timedelta(days=1)
    return out


def _team_name(comp: Dict[str, Any]) -> str:
    team = (comp or {}).get("team") or {}
    return team.get("displayName") or team.get("location") or team.get("name") or "Unknown"


# app/services/espn_nfl.py

def extract_game_lite(ev: dict) -> dict:
    """
    Flatten an ESPN NFL event into a lite row with team IDs.
    """
    comp = (ev.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []

    home = next(
        (c for c in competitors if c.get("homeAway") == "home"),
        competitors[0] if competitors else {},
    )
    away = next(
        (c for c in competitors if c.get("homeAway") == "away"),
        competitors[1] if len(competitors) > 1 else {},
    )

    home_team = home.get("team") or {}
    away_team = away.get("team") or {}

    return {
        "gameId": ev.get("id"),
        "homeTeam": home_team.get("displayName"),
        "homeTeamId": home_team.get("id"),
        "awayTeam": away_team.get("displayName"),
        "awayTeamId": away_team.get("id"),
        "startTime": ev.get("date"),
        "league": (ev.get("league") or {}).get("name"),
    }

