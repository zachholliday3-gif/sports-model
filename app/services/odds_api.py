# app/services/odds_api.py
import os
import httpx
import asyncio
from typing import Dict, Any, List

API_HOST = "https://api.the-odds-api.com/v4"
SPORT_CBB = "basketball_ncaab"

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())

async def _get_json(url: str, params: Dict[str, str]) -> Any:
    async with httpx.AsyncClient(timeout=20.0, headers=HEADERS) as client:
        last = None
        for i in range(3):
            try:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
                await asyncio.sleep(0.6 * (i + 1))
        return {"_error": str(last or "unknown"), "_url": url, "_params": params}

async def _list_events(api_key: str) -> List[Dict[str, Any]]:
    """List upcoming/live NCAAB events (no odds)."""
    url = f"{API_HOST}/sports/{SPORT_CBB}/events"
    data = await _get_json(url, {"apiKey": api_key})
    return data if isinstance(data, list) else []

async def _event_odds(api_key: str, event_id: str, regions: str, bookmakers: str | None) -> Any:
    """
    Fetch per-event odds for 1H markets.
    Markets: spreads_h1, totals_h1  (true first-half markets)
    """
    url = f"{API_HOST}/sports/{SPORT_CBB}/events/{event_id}/odds"
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": "spreads_h1,totals_h1",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    if bookmakers:
        params["bookmakers"] = bookmakers
    return await _get_json(url, params)

def _pick_point_from_market(market: Dict[str, Any], home_name_norm: str) -> float | None:
    """
    Given a The Odds API 'market' object (spreads_h1 or totals_h1),
    pick the relevant point:
      - spreads_h1: choose the outcome matching the home team (if present), else fallback to first outcome
      - totals_h1: choose the first outcome's 'point'
    """
    try:
        outcomes = market.get("outcomes") or []
        if not isinstance(outcomes, list) or not outcomes:
            return None

        key = market.get("key")
        if key == "spreads_h1":
            # Try to find the Home team outcome by name
            for o in outcomes:
                nm = _norm(o.get("name") or "")
                if nm == home_name_norm or nm == "home":
                    pt = o.get("point")
                    return float(pt) if isinstance(pt, (int, float)) else None
            # fallback: first available
            pt = outcomes[0].get("point")
            return float(pt) if isinstance(pt, (int, float)) else None

        elif key == "totals_h1":
            # First outcome typically has the total 'point'
            pt = outcomes[0].get("point")
            return float(pt) if isinstance(pt, (int, float)) else None

    except Exception:
        return None
    return None

async def get_cbb_1h_lines(_: str | None = None) -> Dict[str, Dict[str, Any]]:
    """
    Returns true 1H markets keyed by normalized 'away|home':
      {
        "away|home": {
          "marketSpreadHome": float | None,
          "marketTotal": float | None,
          "book": str | None
        }
      }

    Notes:
    - Requires ODDS_API_KEY (The Odds API).
    - Uses per-event endpoint with markets 'spreads_h1' and 'totals_h1'.
    - Regions default to 'us'. Limit books via ODDS_BOOKMAKERS (optional, comma-separated).
    """
    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        # Graceful no-key fallback: caller will see market=None/edge=None
        return {}

    regions = os.getenv("ODDS_REGIONS", "us")
    bookmakers = os.getenv("ODDS_BOOKMAKERS")  # optional: e.g. "fanduel,draftkings,betmgm"

    events = await _list_events(api_key)
    if not events:
        return {}

    # For quota efficiency, fetch event odds in small concurrent batches
    out: Dict[str, Dict[str, Any]] = {}

    async def process_event(ev: Dict[str, Any]):
        try:
            ev_id = ev.get("id")
            home = ev.get("home_team")
            away = ev.get("away_team")
            if not ev_id or not home or not away:
                return
            home_n = _norm(home)
            token = f"{_norm(away)}|{home_n}"

            data = await _event_odds(api_key, ev_id, regions, bookmakers)
            if not isinstance(data, dict):
                return
            # data.bookmakers -> each has markets [ { key: spreads_h1|totals_h1, outcomes: [...] } ]
            best_spread = None
            best_total = None
            best_book = None

            for bm in (data.get("bookmakers") or []):
                mkts = bm.get("markets") or []
                if not isinstance(mkts, list):
                    continue

                # find desired markets
                m_spread = next((m for m in mkts if m.get("key") == "spreads_h1"), None)
                m_total = next((m for m in mkts if m.get("key") == "totals_h1"), None)

                s_point = _pick_point_from_market(m_spread, home_n) if m_spread else None
                t_point = _pick_point_from_market(m_total, home_n) if m_total else None

                # prefer the first book that has BOTH; else keep whatever we get first
                if s_point is not None or t_point is not None:
                    if best_book is None or (best_spread is None and s_point is not None) or (best_total is None and t_point is not None):
                        best_spread = s_point if s_point is not None else best_spread
                        best_total = t_point if t_point is not None else best_total
                        best_book = bm.get("title") or bm.get("key")

                if best_spread is not None and best_total is not None:
                    break  # good enough

            out[token] = {
                "marketSpreadHome": best_spread,
                "marketTotal": best_total,
                "book": best_book,
            }
        except Exception:
            return

    # Run with limited concurrency to be nice to the API
    sem = asyncio.Semaphore(int(os.getenv("ODDS_CONCURRENCY", "6")))

    async def _guarded(ev):
        async with sem:
            await process_event(ev)

    await asyncio.gather(*(_guarded(ev) for ev in events))
    return out
