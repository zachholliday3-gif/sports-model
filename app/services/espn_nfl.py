# app/services/espn_nfl.py
import httpx, asyncio
from typing import Any, Dict, List, Tuple, Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

CORE_URL = "https://sports.core.api.espn.com/v2/sports/football/nfl/scoreboard"
SITE_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# ---------- helpers ----------
def _ny_date_str(dt: Optional[datetime] = None) -> str:
    now_ny = (dt or datetime.now(ZoneInfo("America/New_York")))
    return now_ny.strftime("%Y%m%d")

def _normalize_date_str(yyyymmdd: Optional[str]) -> str:
    if yyyymmdd is None:
        return _ny_date_str()
    y = yyyymmdd.strip().lower()
    if y in ("", "today"):
        return _ny_date_str()
    q = yyyymmdd.replace("-", "")
    if len(q) != 8 or not q.isdigit():
        raise ValueError("date must be YYYYMMDD or YYYY-MM-DD")
    return q

def _dash(q: str) -> str:
    return f"{q[:4]}-{q[4:6]}-{q[6:]}"

async def _get(url: str, params: Dict[str, str] | None = None) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0, headers=HEADERS) as client:
        last = None
        for i in range(3):
            try:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                last = e
                await asyncio.sleep(0.6 * (i + 1))
        return {"_error": str(last or "unknown"), "_url": url, "_params": params or {}}

async def _events_from_core(sb: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    evs = sb.get("events")
    if not isinstance(evs, list) or not evs: return ([], False)
    out: List[Dict[str, Any]] = []
    for ref in evs:
        try:
            ev_url = ref.get("$ref") if isinstance(ref, dict) else None
            if not ev_url: continue
            ev = await _get(ev_url)
            if isinstance(ev, dict) and ev.get("competitions"):
                out.append(ev)
        except Exception:
            continue
    return (out, True)

def _events_from_site(sb: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    evs = sb.get("events")
    if isinstance(evs, list) and evs: return (evs, True)
    return ([], False)

# ---------- public fetchers ----------
async def get_games_for_date(yyyymmdd: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Robust single-day fetcher that tries multiple 'dates' formats and both endpoints:
      - site.api with range (YYYYMMDD-YYYYMMDD)  <-- most reliable for NFL
      - site.api with dashed range (YYYY-MM-DD to same)
      - core.v2 with single, dashed, and range forms
    """
    q = _normalize_date_str(yyyymmdd)
    candidates: List[Dict[str, Any]] = []

    # Site API tends to like ranges even for one day
    candidates.append(("site", {"dates": f"{q}-{q}", "limit": "500"}))
    candidates.append(("site", {"dates": f"{_dash(q)}-{_dash(q)}", "limit": "500"}))

    # Core API sometimes accepts these
    candidates.append(("core", {"dates": q}))
    candidates.append(("core", {"dates": _dash(q)}))
    candidates.append(("core", {"dates": f"{q}-{q}"}))
    candidates.append(("core", {"dates": f"{_dash(q)}-{_dash(q)}"}))

    # try in order
    for endpoint, params in candidates:
        if endpoint == "site":
            sb = await _get(SITE_URL, params)
            evs, ok = _events_from_site(sb)
        else:
            sb = await _get(CORE_URL, params)
            evs, ok = _events_from_core(sb)
        if ok and evs:
            return evs

    # fallback if no explicit date was passed: try yesterday (common for late games)
    if yyyymmdd is None:
        y = datetime.now(ZoneInfo("America/New_York")) - timedelta(days=1)
        return await get_games_for_date(y.strftime("%Y%m%d"))

    return []

async def get_games_for_range(start_yyyymmdd: str, end_yyyymmdd: str) -> List[Dict[str, Any]]:
    """Gather games across [start, end] inclusive by calling per-day."""
    start = datetime.strptime(_normalize_date_str(start_yyyymmdd), "%Y%m%d")
    end = datetime.strptime(_normalize_date_str(end_yyyymmdd), "%Y%m%d")
    days = (end - start).days
    out: List[Dict[str, Any]] = []
    for i in range(days + 1):
        q = (start + timedelta(days=i)).strftime("%Y%m%d")
        out.extend(await get_games_for_date(q))
    # de-dup by id
    seen = set()
    uniq = []
    for ev in out:
        ev_id = ev.get("id") or ev.get("guid")
        if ev_id in seen: continue
        seen.add(ev_id)
        uniq.append(ev)
    return uniq

# ---------- extractors ----------
def _name(team_obj: Dict[str, Any]) -> str:
    team = team_obj.get("team") or {}
    return team.get("displayName") or team.get("name") or team.get("shortDisplayName") or "Unknown"

def extract_game_lite(ev: Dict[str, Any]) -> Dict[str, Any]:
    comp = ev["competitions"][0]
    teams = comp["competitors"]
    home = next(t for t in teams if t.get("homeAway") == "home")
    away = next(t for t in teams if t.get("homeAway") == "away")
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
    return {
        "gameId": str(ev.get("id") or comp.get("id") or ""),
        "date": ev.get("date") or comp.get("date"),
        "status": comp.get("status", {}).get("type", {}).get("name", "STATUS_SCHEDULED"),
        "homeTeam": _name(home),
        "awayTeam": _name(away),
        "venue": venue,
    }
