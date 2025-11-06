# app/services/odds_api_nfl_props.py
from __future__ import annotations

import os
import asyncio
from typing import Any, Dict, List, Optional, Tuple

import httpx

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
BASE = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_nfl"

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# Correct market keys for NFL props
# https://the-odds-api.com/sports-odds-data/betting-markets.html
MARKET_MAP = {
    "player_pass_yds": "passYds",
    "player_pass_tds": "passTDs",
    "player_rush_yds": "rushYds",
    "player_reception_yds": "recYds",
    "player_receptions": "receptions",
}

def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())

async def _get(client: httpx.AsyncClient, path: str, params: Dict[str, Any]) -> Any:
    r = await client.get(f"{BASE}{path}", params=params)
    r.raise_for_status()
    return r.json()

async def _list_events(client: httpx.AsyncClient) -> List[dict]:
    # events list (no odds)
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

def _within_iso(iso_ts: str, start_iso: Optional[str], end_iso: Optional[str]) -> bool:
    if not start_iso or not end_iso:
        return True
    return (iso_ts >= start_iso) and (iso_ts <= end_iso)

async def get_nfl_player_prop_lines(
    season: int,
    week: int,
    want_stats: List[str] | None = None,
    region: str = "us",
    bookmakers: Optional[List[str]] = None,
    start_iso: Optional[str] = None,
    end_iso: Optional[str] = None,
    debug: bool = False,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """
    Returns:
      (props_by_player, diagnostics)

      props_by_player: {
        "playerToken|TEAM|OPP": {
          player, team, opponent, position: None, book, markets:{stat: line}, gameToken
        }
      }

      diagnostics: counts and sample to understand coverage.
    """
    diag = {"events_total": 0, "events_in_window": 0, "events_with_markets": 0, "queried": 0, "sample": None}
    if not ODDS_API_KEY:
        diag["note"] = "ODDS_API_KEY missing"
        return {}, diag

    want_stats = want_stats or ["recYds", "receptions", "rushYds", "passYds", "passTDs"]
    rev = {v: k for k, v in MARKET_MAP.items()}
    requested_markets = [rev[s] for s in want_stats if s in rev]
    if not requested_markets:
        diag["note"] = "no recognized markets requested"
        return {}, diag

    out: Dict[str, Dict[str, Any]] = {}

    async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
        events = await _list_events(client)
        diag["events_total"] = len(events or [])

        # Time filter (week window) if provided; otherwise query all events (safer for coverage)
        evs = [e for e in (events or []) if _within_iso(e.get("commence_time", ""), start_iso, end_iso)]
        diag["events_in_window"] = len(evs)

        # If windowed set is empty, fall back to all events to maximize chance of props
        if not evs:
            evs = events or []

        sem = asyncio.Semaphore(6)

        async def fetch_one(ev: dict):
            async with sem:
                try:
                    home = ev.get("home_team") or ""
                    away = ev.get("away_team") or ""
                    token_game = f"{_norm(away)}|{_norm(home)}"
                    payload = await _event_odds(
                        client, ev["id"], requested_markets, region=region, bookmakers=bookmakers
                    )
                    diag["queried"] += 1

                    if payload.get("bookmakers"):
                        diag["events_with_markets"] += 1

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
                    return

        await asyncio.gather(*[fetch_one(ev) for ev in evs])

    # include a tiny sample in diagnostics
    for _, v in out.items():
        diag["sample"] = {"player": v["player"], "team": v["team"], "markets": v["markets"], "book": v["book"]}
        break

    if debug:
        diag["requested_markets"] = requested_markets
        if bookmakers:
            diag["bookmakers"] = bookmakers

    return out, diag
