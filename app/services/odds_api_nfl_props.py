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
    # Be forgiving: if the call fails, return {} and let caller decide next step.
    try:
        r = await client.get(f"{BASE}{path}", params=params)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"__error__": str(e), "__status__": getattr(r, "status_code", None) if 'r' in locals() else None}

async def _list_events(client: httpx.AsyncClient) -> List[dict]:
    res = await _get(client, f"/sports/{SPORT}/events", {"apiKey": ODDS_API_KEY})
    if isinstance(res, dict) and res.get("__error__"):
        return []
    return res or []

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
    res = await _get(client, f"/sports/{SPORT}/events/{event_id}/odds", params)
    if isinstance(res, dict) and res.get("__error__"):
        return {}
    return res or {}

def _within_iso(iso_ts: str, start_iso: Optional[str], end_iso: Optional[str]) -> bool:
    if not start_iso or not end_iso:
        return True
    return (iso_ts >= start_iso) and (iso_ts <= end_iso)

async def _collect_for_events(
    client: httpx.AsyncClient,
    events: List[dict],
    markets: List[str],
    region: str,
    bookmakers: Optional[List[str]],
) -> Tuple[Dict[str, Dict[str, Any]], int]:
    out: Dict[str, Dict[str, Any]] = {}
    queried = 0
    sem = asyncio.Semaphore(6)

    async def fetch_one(ev: dict):
        nonlocal queried
        async with sem:
            try:
                home = ev.get("home_team") or ""
                away = ev.get("away_team") or ""
                token_game = f"{_norm(away)}|{_norm(home)}"

                payload = await _event_odds(client, ev.get("id", ""), markets, region=region, bookmakers=bookmakers)
                queried += 1

                for bk in payload.get("bookmakers") or []:
                    book = bk.get("title") or bk.get("key")
                    for m in bk.get("markets") or []:
                        mkey = m.get("key")
                        # ignore unknown markets
                        for api_key, norm_key in MARKET_MAP.items():
                            if mkey != api_key:
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
                                    prev = entry["markets"].get(norm_key)
                                    if prev is None or abs(float(line)) > abs(float(prev)):
                                        entry["markets"][norm_key] = float(line)
                                        entry["book"] = book
            except Exception:
                # ignore individual event failures
                return

    await asyncio.gather(*[fetch_one(ev) for ev in events])
    return out, queried

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
    Returns (props_by_player, diagnostics) with an internal degrade ladder.
    """
    diag = {
        "note": None,
        "events_total": 0,
        "events_in_window": 0,
        "queried": 0,
        "events_with_any_props": 0,
        "attempts": [],
        "sample": None,
    }

    if not ODDS_API_KEY:
        diag["note"] = "ODDS_API_KEY missing"
        return {}, diag

    want_stats = want_stats or ["recYds", "receptions", "rushYds", "passYds", "passTDs"]
    rev = {v: k for k, v in MARKET_MAP.items()}
    # Build initial market list from requested stats (drop unknowns)
    base_markets = [rev[s] for s in want_stats if s in rev]
    if not base_markets:
        diag["note"] = "no recognized markets requested"
        return {}, diag

    async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
        # load events and filter by week window if given
        all_events = await _list_events(client)
        diag["events_total"] = len(all_events)
        events_window = [e for e in all_events if _within_iso(e.get("commence_time", ""), start_iso, end_iso)]
        diag["events_in_window"] = len(events_window)

        # ladder of attempts
        attempts = [
            {"markets": base_markets, "bookmakers": bookmakers, "window_only": True, "label": "requested+books+window"},
            {"markets": ["player_reception_yds"], "bookmakers": bookmakers, "window_only": True, "label": "recYds+books+window"},
            {"markets": base_markets, "bookmakers": None, "window_only": True, "label": "requested+allbooks+window"},
            {"markets": ["player_reception_yds"], "bookmakers": None, "window_only": True, "label": "recYds+allbooks+window"},
            {"markets": base_markets, "bookmakers": bookmakers, "window_only": False, "label": "requested+books+allEvents"},
            {"markets": base_markets, "bookmakers": None, "window_only": False, "label": "requested+allbooks+allEvents"},
            {"markets": ["player_reception_yds"], "bookmakers": None, "window_only": False, "label": "recYds+allbooks+allEvents"},
        ]

        out_total: Dict[str, Dict[str, Any]] = {}

        for att in attempts:
            diag["attempts"].append(att["label"])
            events = events_window if att["window_only"] and events_window else all_events
            props, queried = await _collect_for_events(
                client, events, att["markets"], region=region, bookmakers=att["bookmakers"]
            )
            diag["queried"] += queried
            out_total.update(props)
            if props:
                break  # stop on first success

    # Count events that yielded any props (rough proxy using gameToken presence)
    game_tokens = set(v.get("gameToken") for v in out_total.values() if v.get("gameToken"))
    diag["events_with_any_props"] = len(game_tokens)

    # sample
    for _, v in out_total.items():
        diag["sample"] = {"player": v["player"], "team": v["team"], "markets": v["markets"], "book": v["book"]}
        break

    if not debug:
        # trim noise
        diag = {k: v for k, v in diag.items() if k in ("note", "events_total", "events_in_window", "events_with_any_props", "sample")}

    return out_total, diag
