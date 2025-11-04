import httpx
import asyncio
from typing import Any, Dict, List, Tuple
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Two ESPN scoreboards:
# 1) CORE v2 (hypermedia $ref links)
CORE_URL = "https://sports.core.api.espn.com/v2/sports/basketball/mens-college-basketball/scoreboard"
# 2) SITE v2 (direct events payload)
SITE_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

# ------------- helpers -------------
async def fetch_json(url: str, params: Dict[str, str] | None = None) -> Dict[str, Any]:
    """HTTP GET with small retry loop; returns {} on failure."""
    async with httpx.AsyncClient(timeout=20.0, headers=HEADERS) as client:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_exc = e
                await asyncio.sleep(0.8 * (attempt + 1))
        # Final fallback: empty object with diagnostics
        return {"_error": str(last_exc or "unknown"), "_url": url, "_params": params or {}}

def _ny_date_str(dt: datetime | None = None) -> str:
    now_ny = (dt or datetime.now(ZoneInfo("America/New_York")))
    return now_ny.strftime("%Y%m%d")

def _normalize_date_str(yyyymmdd: str | None) -> str:
    # Accept None / "" / "today" → NY "today"
    if yyyymmdd is None:
        return _ny_date_str()
    y = yyyymmdd.strip().lower()
    if y in ("", "today"):
        return _ny_date_str()
    q = yyyymmdd.replace("-", "")
    if len(q) != 8 or not q.isdigit():
        raise ValueError("date must be YYYYMMDD or YYYY-MM-DD")
    return q

# ------------- main entrypoints -------------
async def get_scoreboard_core(q: str) -> Dict[str, Any]:
    """CORE v2 hypermedia scoreboard (often needs deref of events)."""
    return await fetch_json(CORE_URL, params={"dates": q})

async def get_scoreboard_site(q: str) -> Dict[str, Any]:
    """SITE v2 direct scoreboard (usually has events inline)."""
    # A large page size helps busy slates
    return await fetch_json(SITE_URL, params={"dates": q, "limit": "500"})

async def _events_from_core(sb: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    """Return full event docs by dereferencing $ref links (CORE)."""
    events = sb.get("events")
    if not isinstance(events, list) or not events:
        return ([], False)
    out: List[Dict[str, Any]] = []
    for ref in events:
        try:
            ev_url = ref.get("$ref") if isinstance(ref, dict) else None
            if not ev_url:
                continue
            ev = await fetch_json(ev_url)
            if isinstance(ev, dict) and ev.get("competitions"):
                out.append(ev)
        except Exception:
            continue
    return (out, True)

def _events_from_site(sb: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    """Return inline events from SITE payload."""
    events = sb.get("events")
    if isinstance(events, list) and events:
        # SITE events already have competitions inline; use as-is
        return (events, True)
    return ([], False)

async def get_games_for_date(yyyymmdd: str | None = None) -> List[Dict[str, Any]]:
    """
    Robust fetch:
      - Normalize date (NY today if None)
      - Try CORE; if empty, try SITE
      - If caller didn't pass a date and day is empty, try NY 'yesterday'
      - Always return a list (possibly empty), never raise here
    """
    def _try_all(q: str) -> List[Dict[str, Any]]:
        return asyncio.run(_try_all_async(q))  # not used actually; keep sync variant if needed

    q = _normalize_date_str(yyyymmdd)

    # primary attempts for the requested day
    core = await get_scoreboard_core(q)
    core_events, core_ok = await _events_from_core(core)
    if core_ok and core_events:
        return core_events

    site = await get_scoreboard_site(q)
    site_events, site_ok = _events_from_site(site)
    if site_ok and site_events:
        return site_events

    # Fallback to yesterday only if user didn't explicitly pass a date
    if yyyymmdd is None:
        y_ny = datetime.now(ZoneInfo("America/New_York")) - timedelta(days=1)
        qy = y_ny.strftime("%Y%m%d")

        core_y = await get_scoreboard_core(qy)
        core_events_y, core_ok_y = await _events_from_core(core_y)
        if core_ok_y and core_events_y:
            return core_events_y

        site_y = await get_scoreboard_site(qy)
        site_events_y, site_ok_y = _events_from_site(site_y)
        if site_ok_y and site_events_y:
            return site_events_y

    # Nothing found; return empty list
    return []

# ------------- extraction helpers (work for both shapes) -------------
def extract_game_lite(ev: Dict[str, Any]) -> Dict[str, Any]:
    comp = ev["competitions"][0]
    teams = comp["competitors"]
    # competitors can be list of dicts with homeAway flags
    home = next(t for t in teams if t.get("homeAway") == "home")
    away = next(t for t in teams if t.get("homeAway") == "away")
    # Some SITE payloads nest team objects under 'team'
    def _name(x: Dict[str, Any]) -> str:
        team = x.get("team") or {}
        return team.get("displayName") or team.get("name") or team.get("shortDisplayName") or "Unknown"
    return {
        "gameId": str(ev.get("id") or comp.get("id") or ""),
        "date": ev.get("date") or comp.get("date"),
        "status": comp.get("status", {}).get("type", {}).get("name", "STATUS_SCHEDULED"),
        "homeTeam": _name(home),
        "awayTeam": _name(away),
    }

def extract_matchup_detail(ev: Dict[str, Any]) -> Dict[str, Any]:
    comp = ev["competitions"][0]
    teams = comp["competitors"]
    home = next(t for t in teams if t.get("homeAway") == "home")
    away = next(t for t in teams if t.get("homeAway") == "away")
    venue = (comp.get("venue") or {}).get("fullName")
    def _name(x: Dict[str, Any]) -> str:
        team = x.get("team") or {}
        return team.get("displayName") or team.get("name") or team.get("shortDisplayName") or "Unknown"
    return {
        "gameId": str(ev.get("id") or comp.get("id") or ""),
        "date": ev.get("date") or comp.get("date"),
        "status": comp.get("status", {}).get("type", {}).get("name", "STATUS_SCHEDULED"),
        "homeTeam": _name(home),
        "awayTeam": _name(away),
        "venue": venue,
    }
