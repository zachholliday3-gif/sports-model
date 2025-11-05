# app/services/espn_nfl.py
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

SITE_API = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())

def _today_yyyymmdd() -> str:
    now = datetime.now(NY)
    return now.strftime("%Y%m%d")

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
        # YYYY-MM-DD -> YYYYMMDD
        try:
            dt = datetime.strptime(ds, "%Y-%m-%d")
            return dt.strftime("%Y%m%d")
        except Exception:
            pass
    if len(ds) == 8 and ds.isdigit():
        # YYYYMMDD
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
                await asyncio.sleep(0.4 * (i + 1))
        raise last or RuntimeError("unknown http error")

async def get_games_for_date(date: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Loads NFL games for a given date using ESPN site.api scoreboard.
    ESPN wants: dates=YYYYMMDD-YYYYMMDD (inclusive range), not ISO with dashes.
    """
    d = _coerce_yyyymmdd(date)
    params = {
        "dates": f"{d}-{d}",
        "limit": 500,
    }
    data = await _get_json(SITE_API, params)
    events = data.get("events") or []
    # Ensure list type
    if not isinstance(events, list):
        return []
    return events

async def get_games_for_range(start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """
    Inclusive day range in US/Eastern. Calls get_games_for_date() per day.
    """
    # floor to date in NY
    start_d = start.astimezone(NY).date()
    end_d = end.astimezone(NY).date()
    if end_d < start_d:
        start_d, end_d = end_d, start_d

    days = []
    cur = start_d
    while cur <= end_d:
        days.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)

    out: List[Dict[str, Any]] = []
    for ds in days:
        try:
            evs = await get_games_for_date(ds)
            out.extend(evs)
        except Exception:
            # skip day on failure
            continue
    return out

def _team_name(comp: Dict[str, Any]) -> str:
    team = (comp or {}).get("team") or {}
    # Prefer "displayName"; fall back to "location" or "name"
    return team.get("displayName") or team.get("location") or team.get("name") or "Unknown"

def extract_game_lite(ev: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize an ESPN event -> lite row used by routers.
    """
    game_id = ev.get("id") or ""
    date = ev.get("date")  # ISO timestamp
    comps = (ev.get("competitions") or [{}])
    comp = comps[0] if comps else {}
    competitors = comp.get("competitors") or []

    home_name, away_name = "Home", "Away"
    for c in competitors:
        if (c.get("homeAway") or "").lower() == "home":
            home_name = _team_name(c)
        elif (c.get("homeAway") or "").lower() == "away":
            away_name = _team_name(c)

    return {
        "gameId": str(game_id),
        "startTime": date,
        "homeTeam": home_name,
        "awayTeam": away_name,
    }
