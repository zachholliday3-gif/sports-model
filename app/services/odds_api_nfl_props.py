# app/services/odds_api_nfl_props.py
from __future__ import annotations

import os
import asyncio
from typing import Any, Dict, List, Optional, Tuple, Union
from datetime import datetime, timezone

import httpx

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
BASE = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_nfl"

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# Map Odds API markets -> normalized stat keys we use in the model
MARKET_MAP = {
    "player_pass_yds": "passYds",
    "player_pass_tds": "passTDs",
    "player_rush_yds": "rushYds",
    "player_reception_yds": "recYds",
    "player_receptions": "receptions",
}

def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())

def _to_dt(v: Union[str, datetime, None]) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = v.strip() if isinstance(v, str) else ""
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        try:
            dt = datetime.fromisoformat(s + "T00:00:00+00:00")
        except Exception:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def _within_iso(iso_ts: Union[str, datetime, None],
                start_iso: Union[str, datetime, None],
                end_iso: Union[str, datetime, None]) -> bool:
    t = _to_dt(iso_ts); s = _to_dt(start_iso); e = _to_dt(end_iso)
    if t is None:
        return not (s or e)
    if s and t < s:
        return False
    if e and t > e:
        return False
    return True

async def _get(client: httpx.AsyncClient, path: str, params: Dict[str, Any]) -> Any:
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
    params = {"apiKey": ODDS_API_KEY, "regions": region, "markets": ",".join(markets), "oddsFormat": "american"}
    if bookmakers:
        params["bookmakers"] = ",".join(bookmakers)
    res = await _get(client, f"/sports/{SPORT}/events/{event_id}/odds", params)
    if isinstance(res, dict) and res.get("__error__"):
        return {}
    return res or {}

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
            payload = await _event_odds(client, ev.get("id", ""), markets, region=region, bookmakers=bookmakers)
            queried += 1
            home = ev.get("home_team") or ""; away = ev.get("away_team") or ""
            token_game = f"{_norm(away)}|{_norm(home)}"
            for bk in payload.get("bookmakers") or []:
                book = bk.get("title") or bk.get("key")
                for m in bk.get("markets") or []:
                    nk = MARKET_MAP.get(m.get("key"))
                    if not nk:
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
                                {"player": player, "team": team_name, "opponent": opp_name, "position": None,
                                 "book": book, "markets": {}, "gameToken": token_game},
                            )
                            prev = entry["markets"].get(nk)
                            if prev is None or abs(float(line)) > abs(float(prev)):
                                entry["markets"][nk] = float(line)
                                entry["book"] = book
                            # position guess
                            if nk == "rushYds":
                                entry["position"] = entry.get("position") or "RB"
                            if nk in ("recYds", "receptions") and not entry.get("position"):
                                entry["position"] = "WR/TE"
                            if nk in ("passYds", "passTDs") and not entry.get("position"):
                                entry["position"] = "QB"

    await asyncio.gather(*[fetch_one(e) for e in events])
    return out, queried

async def get_nfl_player_prop_lines(
    season: int,
    week: int,
    want_stats: List[str] | None = None,
    region: str = "us",
    bookmakers: Optional[List[str]] = None,
    start_iso: Union[str, datetime, None] = None,
    end_iso: Union[str, datetime, None] = None,
    debug: bool = False,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """
    Fast, minimal attempts for Actions reliability.
    """
    diag = {"note": None, "events_total": 0, "events_in_window": 0, "events_with_any_props": 0, "sample": None}

    if not ODDS_API_KEY:
        diag["note"] = "ODDS_API_KEY missing"
        return {}, diag

    want_stats = want_stats or ["recYds"]
    rev = {v: k for k, v in MARKET_MAP.items()}
    base_markets = [rev[s] for s in want_stats if s in rev]

    # Include hints so we can guess RB/QB and filter to WR/TE
    if any(s in ("recYds", "receptions") for s in want_stats):
        if "player_rush_yds" not in base_markets:
            base_markets.append("player_rush_yds")
        if "player_pass_yds" not in base_markets:
            base_markets.append("player_pass_yds")

    # Tight timeout for the client and whole flow
    async with httpx.AsyncClient(timeout=5.0, headers=HEADERS) as client:
        all_events = await _list_events(client)
        diag["events_total"] = len(all_events)
        events_window = [e for e in all_events if _within_iso(e.get("commence_time"), start_iso, end_iso)]
        diag["events_in_window"] = len(events_window)

        # Slim attempts: fastest first, window-only when available
        attempts = [
            {"markets": ["player_reception_yds"], "bookmakers": None, "window_only": True},   # fastest
            {"markets": base_markets,              "bookmakers": None, "window_only": True},
            {"markets": base_markets,              "bookmakers": None, "window_only": False},
        ]

        out_total: Dict[str, Dict[str, Any]] = {}
        for att in attempts:
            events = events_window if (att["window_only"] and events_window) else all_events
            props, _ = await _collect_for_events(client, events, att["markets"], region=region, bookmakers=att["bookmakers"])
            out_total.update(props)
            if out_total:
                break

    if out_total:
        any_v = next(iter(out_total.values()))
        diag["events_with_any_props"] = 1
        diag["sample"] = {"player": any_v["player"], "team": any_v["team"], "markets": any_v["markets"], "book": any_v["book"], "position": any_v.get("position")}

    return out_total, diag
