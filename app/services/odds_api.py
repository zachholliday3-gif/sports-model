# app/services/odds_api.py
import os, asyncio, httpx
from typing import Dict, Any, List, Optional

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
ODDS_REGIONS = os.getenv("ODDS_REGIONS", "us")
ODDS_BOOKMAKERS = os.getenv("ODDS_BOOKMAKERS")  # optional CSV of book keys
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
BASE = "https://api.the-odds-api.com/v4"

def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())

async def _get_json(url: str, params: Dict[str, str]) -> Any:
    # keep fast + resilient
    async with httpx.AsyncClient(timeout=8.0, headers=HEADERS) as client:
        last = None
        for i in range(2):
            try:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
                await asyncio.sleep(0.5 * (i + 1))
        return {"_error": str(last or "unknown"), "_url": url, "_params": params}

async def _list_events(sport_key: str) -> List[Dict[str, Any]]:
    if not ODDS_API_KEY:
        return []
    url = f"{BASE}/sports/{sport_key}/events"
    data = await _get_json(url, {"apiKey": ODDS_API_KEY})
    return data if isinstance(data, list) else []

async def _event_odds(sport_key: str, event_id: str) -> Any:
    if not ODDS_API_KEY:
        return {}
    url = f"{BASE}/sports/{sport_key}/events/{event_id}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": ODDS_REGIONS,
        "markets": "spreads,totals",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    if ODDS_BOOKMAKERS:
        params["bookmakers"] = ODDS_BOOKMAKERS
    return await _get_json(url, params)

def _pick_market_point(market: Dict[str, Any], home_norm: str) -> Optional[float]:
    try:
        outcomes = market.get("outcomes") or []
        if not outcomes:
            return None
        key = market.get("key")
        if key == "spreads":
            # prefer HOME spread
            for o in outcomes:
                nm = _norm(o.get("name") or "")
                if nm == home_norm or nm == "home":
                    pt = o.get("point")
                    return float(pt) if isinstance(pt, (int, float)) else None
            pt = outcomes[0].get("point")
            return float(pt) if isinstance(pt, (int, float)) else None
        if key == "totals":
            pt = outcomes[0].get("point")
            return float(pt) if isinstance(pt, (int, float)) else None
    except Exception:
        return None
    return None

async def _lines_for_events(sport_key: str, home_field: str, away_field: str) -> Dict[str, Dict[str, Any]]:
    """
    Returns dict keyed by "away|home":
      { token: { "marketSpreadHome": float|None, "marketTotal": float|None, "book": str|None } }
    """
    events = await _list_events(sport_key)
    if not events:
        return {}

    out: Dict[str, Dict[str, Any]] = {}

    async def process(ev: Dict[str, Any]):
        try:
            ev_id = ev.get("id")
            home = ev.get(home_field)
            away = ev.get(away_field)
            if not (ev_id and home and away):
                return
            home_n = _norm(home)
            token = f"{_norm(away)}|{home_n}"

            data = await _event_odds(sport_key, ev_id)
            if not isinstance(data, dict):
                return

            best_s, best_t, best_book = None, None, None
            for bm in (data.get("bookmakers") or []):
                mkts = bm.get("markets") or []
                m_spread = next((m for m in mkts if m.get("key") == "spreads"), None)
                m_total  = next((m for m in mkts if m.get("key") == "totals"), None)

                s = _pick_market_point(m_spread, home_n) if m_spread else None
                t = _pick_market_point(m_total, home_n) if m_total else None
                if s is not None or t is not None:
                    if best_book is None or (best_s is None and s is not None) or (best_t is None and t is not None):
                        best_s = s if s is not None else best_s
                        best_t = t if t is not None else best_t
                        best_book = bm.get("title") or bm.get("key")
                if best_s is not None and best_t is not None:
                    break

            out[token] = {"marketSpreadHome": best_s, "marketTotal": best_t, "book": best_book}
        except Exception:
            return

    sem = asyncio.Semaphore(int(os.getenv("ODDS_CONCURRENCY", "6")))
    async def guarded(ev):
        async with sem:
            await process(ev)

    await asyncio.gather(*(guarded(e) for e in events))
    return out

# -------- Public helpers (exported) --------
async def get_cbb_1h_lines(_: Any = None) -> Dict[str, Dict[str, Any]]:
    # Odds API exposes FG totals/spreads for NCAAB. If you have first-half endpoints on your plan,
    # you can extend _event_odds() to request those markets specifically.
    return await _lines_for_events("basketball_ncaab", "home_team", "away_team")

async def get_nfl_fg_lines() -> Dict[str, Dict[str, Any]]:
    return await _lines_for_events("americanfootball_nfl", "home_team", "away_team")

__all__ = ["get_cbb_1h_lines", "get_nfl_fg_lines"]
