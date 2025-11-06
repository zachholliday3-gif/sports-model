# app/services/espn_cbb.py
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

SITE_API = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"


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
                await asyncio.sleep(0.4 * (i + 1))
        raise last or RuntimeError("unknown http error")


async def get_games_for_date(date: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Loads CBB games for a given date using ESPN site.api scoreboard.
    ESPN expects: dates=YYYYMMDD-YYYYMMDD (inclusive range).
    If no events are returned (late posting around midnight ET), we also try (date-1) and (date+1).
    """
    d = _coerce_yyyymmdd(date)

    # primary
    params = {"dates": f"{d}-{d}", "limit": 500}
    data = await _get_json(SITE_API, params)
    events = data.get("events") or []
    if isinstance(events, list) and events:
        return events

    # soft fallback: sometimes schedules land near midnight ET
    dt = datetime.strptime(d, "%Y%m%d")
    for delta in (-1, 1):
        alt = (dt + timedelta(days=delta)).strftime("%Y%m%d")
        try:
            alt_data = await _get_json(SITE_API, {"dates": f"{alt}-{alt}", "limit": 500})
            evs = alt_data.get("events") or []
            if isinstance(evs, list) and evs:
                return evs
        except Exception:
            continue

    # truly no data
    return []


def _team_name(comp: Dict[str, Any]) -> str:
    team = (comp or {}).get("team") or {}
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


def extract_matchup_detail(ev: Dict[str, Any]) -> Dict[str, Any]:
    """
    Detailed single matchup model shell (expand later as needed).
    """
    lite = extract_game_lite(ev)
    return {**lite, "notes": None}
