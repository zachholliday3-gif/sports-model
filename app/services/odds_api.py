# app/services/odds_api.py
import os
import httpx
import asyncio
from typing import Dict, Any, List, Tuple

# Provider: The Odds API (or similar). If no API key is set, we return {} so the app still works.
# Set ODDS_API_KEY in Railway (or .env locally). We’ll target NCAAB spreads & totals.
THE_ODDS_API = "https://api.the-odds-api.com/v4/sports/basketball_ncaab/odds"

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())

def _best_price(lines: List[Dict[str, Any]], key: str) -> float | None:
    """Given a list of outcomes with a 'point' field, pick the consensus/first available."""
    # Some books provide multiple markets; we take the first valid point.
    for o in lines or []:
        pt = o.get("point")
        if isinstance(pt, (int, float)):
            return float(pt)
    return None

async def _fetch(url: str, params: Dict[str, str]) -> Any:
    async with httpx.AsyncClient(timeout=20.0, headers=HEADERS) as client:
        last = None
        for i in range(3):
            try:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
                await asyncio.sleep(0.7 * (i + 1))
        return {"_error": str(last or "unknown")}

async def get_cbb_1h_lines(date_iso: str | None = None) -> Dict[str, Dict[str, Any]]:
    """
    Returns a dict keyed by a normalized matchup token (away|home), with:
      {
        token: {
          'marketSpreadHome': float | None,
          'marketTotal': float | None,
          'book': str | None
        }
      }
    If no ODDS_API_KEY, returns {} (caller should handle gracefully).
    """
    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        return {}  # graceful: markets unavailable

    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": "spreads,totals",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    # NOTE: Many providers don’t expose 1H separately in the base endpoint.
    # We pull full game lines here as a starting point; you can swap to a 1H-capable provider later.
    # (Your model is 1H; full-game market still gives you a sense of directional value.)
    data = await _fetch(THE_ODDS_API, params)
    if not isinstance(data, list):
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for ev in data:
        # Expected shape: { 'home_team': 'X', 'away_team': 'Y', 'bookmakers': [ ... ] }
        home = ev.get("home_team")
        away = ev.get("away_team")
        if not home or not away:
            continue
        token = f"{_norm(away)}|{_norm(home)}"

        # scan books; pick the first with both a spread/total point
        book_name = None
        m_spread_home = None
        m_total = None

        for bm in ev.get("bookmakers", []):
            mkts = {m.get("key"): m for m in bm.get("markets", []) if isinstance(m, dict)}
            # spreads market: outcomes typically ["home","away"] with "point"
            spreads = mkts.get("spreads", {}).get("outcomes", [])
            totals = mkts.get("totals", {}).get("outcomes", [])

            # spread home side: find outcome where "name" equals the home team (or contains it)
            sp_home = None
            for o in spreads:
                name = (o.get("name") or "").lower()
                if _norm(name) in (_norm(home), "home"):
                    sp_home = o
                    break
            if sp_home is None and spreads:
                # fallback: pick the first outcome’s point sign relative to home/away indistinct
                sp_home = spreads[0]

            s_point = sp_home.get("point") if sp_home else None
            t_point = _best_price(totals, "point")

            if isinstance(s_point, (int, float)) and isinstance(t_point, (int, float)):
                m_spread_home = float(s_point)
                m_total = float(t_point)
                book_name = bm.get("title") or bm.get("key")
                break

        out[token] = {
            "marketSpreadHome": m_spread_home,
            "marketTotal": m_total,
            "book": book_name,
        }

    return out
