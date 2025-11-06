# app/services/odds_api_nfl_props.py
from __future__ import annotations

import os
import asyncio
from typing import Any, Dict, List, Optional

import httpx

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
BASE = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_nfl"

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# Correct market keys for NFL props (per The Odds API docs)
# https://the-odds-api.com/sports-odds-data/betting-markets.html
MARKET_MAP = {
    "player_pass_yds": "passYds",
    "player_pass_tds": "passTDs",
    "player_rush_yds": "rushYds",
    "player_reception_yds": "recYds",     # <- important fix (was player_rec_yds)
    "player_receptions": "receptions",
}

def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())

async def _get(client: httpx.AsyncClient, path: str, params: Dict[str, Any]) -> Any:
    r = await client.get(f"{BASE}{path}", params=params)
    r.raise_for_status()
    return r.json()

async def _list_events(client: httpx.AsyncClient) -> List[dict]:
    # Free: returns id, teams, commence_time (no odds). We’ll time-filter on the caller side.
    return await _get(client, f"/sports/{SPORT}/events", {"apiKey": ODDS_API_KEY})

async def _event_odds(
    client: httpx.AsyncClient,
    event_id: str,
    markets: List[str],
    region: str,
    bookmakers: Optional[List[str]] = None,
) -> dict:
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": region,
        "markets": ",".join(markets),
        "oddsFormat": "american",
    }
    if bookmakers:
        params["bookmakers"] = ",".join(bookmakers)
    return await _get(client, f"/sports/{SPORT}/events/{event_id}/odds", params)

def _within_iso_window(iso_ts: str, start_iso: str, end_iso: str) -> bool:
    return (iso_ts >= start_iso) and (iso_ts <= end_iso)

async def get_nfl_player_prop_lines(
    season: int,
    week: int,
    want_stats: List[str] | None = None,
    region: str = "us",
    bookmakers: Optional[List[str]] = None,
    start_iso: Optional[str] = None,
    end_iso: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Returns a dict keyed by player-token: {
      "playerToken|TEAM|OPP": {
         player, team, opponent, position:None, book, markets:{stat: line}, gameToken
      }
    }

    Implementation details:
      * Fetch the week's events (id/home/away/commence_time)
      * For each event, call /events/{eventId}/odds with desired player prop markets
      * Aggregate best-available line per stat across bookmakers
    """
    if not ODDS_API_KEY:
        return {}

    want_stats = want_stats or ["recYds", "receptions", "rushYds", "passYds", "passTDs"]
    # Map requested normalized stats -> official Odds API market keys
    rev = {v: k for k, v in MARKET_MAP.items()}
    requested_markets = [rev[s] for s in want_stats if s in rev]
    if not requested_markets:
        return {}

    out: Dict[str, Dict[str, Any]] = {}

    async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
        events = await _list_events(client)

        # If caller passed a time window, trim to that; otherwise leave as-is (this_week endpoints usually pass window)
        if start_iso and end_iso:
            events = [e for e in events or [] if _within_iso_window(e.get("commence_time", ""), start_iso, end_iso)]

        # Gather odds per event concurrently
        sem = asyncio.Semaphore(8)  # be nice to rate limits

        async def fetch_one(ev: dict):
            async with sem:
                try:
                    home = ev.get("home_team") or ""
                    away = ev.get("away_team") or ""
                    token_game = f"{_norm(away)}|{_norm(home)}"

                    payload = await _event_odds(
                        client,
                        ev["id"],
                        requested_markets,
                        region=region,
                        bookmakers=bookmakers,
                    )

                    for bk in payload.get("bookmakers") or []:
                        book = bk.get("title") or bk.get("key")
                        for m in bk.get("markets") or []:
                            mkey = m.get("key")
                            stat = MARKET_MAP.get(mkey)
                            if not stat:
                                continue
                            for oc in m.get("outcomes") or []:
                                player = oc.get("description") or oc.get("name") or ""
                                line = oc.get("point")
                                if line is None:
                                    continue

                                # We can’t 100% know team from outcome; attach both home/away variants.
                                for team_name, opp_name in [(home, away), (away, home)]:
                                    pkey = f"{_norm(player)}|{_norm(team_name)}|{_norm(opp_name)}"
                                    entry = out.setdefault(
                                        pkey,
                                        {
                                            "player": player,
                                            "team": team_name,
                                            "opponent": opp_name,
                                            "position": None,
                                            "book": book,
                                            "markets": {},
                                            "gameToken": token_game,
                                        },
                                    )
                                    prev = entry["markets"].get(stat)
                                    if prev is None or abs(float(line)) > abs(float(prev)):
                                        entry["markets"][stat] = float(line)
                                        entry["book"] = book
                except Exception:
                    # Don't let one event kill the batch
                    return

        await asyncio.gather(*[fetch_one(ev) for ev in events or []])

    return out
