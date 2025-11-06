# app/routers/nfl_debug_routes.py
from __future__ import annotations

import os
import asyncio
from typing import Any, Dict, Optional, List

import httpx
from fastapi import APIRouter, Query

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
BASE = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_nfl"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

router = APIRouter(tags=["nfl-debug"])

async def _get(client: httpx.AsyncClient, path: str, params: Dict[str, Any]) -> Any:
    try:
        r = await client.get(f"{BASE}{path}", params=params)
        return {
            "__status__": r.status_code,
            "__url__": str(r.request.url),
            "__ok__": r.is_success,
            "data": (r.json() if r.is_success else (await r.aread()).decode("utf-8", "ignore")),
        }
    except Exception as e:
        return {"__status__": None, "__ok__": False, "error": str(e)}

@router.get("/_debug/odds_events")
async def odds_events(region: str = "us"):
    """Raw: /v4/sports/{sport}/events (no odds). Confirms your key works + shows event ids."""
    if not ODDS_API_KEY:
        return {"ok": False, "error": "ODDS_API_KEY missing"}
    async with httpx.AsyncClient(timeout=12.0, headers=HEADERS) as client:
        res = await _get(client, f"/sports/{SPORT}/events", {"apiKey": ODDS_API_KEY})
        return res

@router.get("/_debug/odds_event")
async def odds_event(
    eventId: str,
    markets: str = "player_reception_yds",
    region: str = "us",
    bookmakers: Optional[str] = None,
):
    """Raw: /v4/sports/{sport}/events/{eventId}/odds with a props market (e.g., player_reception_yds)."""
    if not ODDS_API_KEY:
        return {"ok": False, "error": "ODDS_API_KEY missing"}
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": region,
        "markets": markets,
        "oddsFormat": "american",
    }
    if bookmakers:
        params["bookmakers"] = bookmakers
    async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
        res = await _get(client, f"/sports/{SPORT}/events/{eventId}/odds", params)
        return res

@router.get("/_debug/props_probe")
async def props_probe(
    region: str = "us",
    bookmakers: Optional[str] = None,
):
    """
    Minimal end-to-end probe: pull events, then fetch one event’s player_reception_yds.
    Returns only diagnostics (no rows).
    """
    if not ODDS_API_KEY:
        return {"ok": False, "error": "ODDS_API_KEY missing"}

    diag = {"events_total": 0, "tested_eventId": None, "event_status": None, "markets_ok": False, "url": None}
    async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
        evs = await _get(client, f"/sports/{SPORT}/events", {"apiKey": ODDS_API_KEY})
        diag["url"] = evs.get("__url__")
        if not evs.get("__ok__"):
            diag["event_status"] = evs.get("__status__")
            return {"ok": False, "step": "events", "diag": diag, "raw": evs}

        events = evs.get("data") or []
        diag["events_total"] = len(events)
        if not events:
            return {"ok": False, "step": "no_events", "diag": diag}

        event_id = str(events[0].get("id"))
        diag["tested_eventId"] = event_id

        params = {
            "apiKey": ODDS_API_KEY,
            "regions": region,
            "markets": "player_reception_yds",
            "oddsFormat": "american",
        }
        if bookmakers:
            params["bookmakers"] = bookmakers

        res = await _get(client, f"/sports/{SPORT}/events/{event_id}/odds", params)
        diag["event_status"] = res.get("__status__")
        diag["url"] = res.get("__url__")

        data = res.get("data")
        if res.get("__ok__") and isinstance(data, dict) and (data.get("bookmakers") or []):
            diag["markets_ok"] = True
            return {"ok": True, "diag": diag, "sample": (data.get("bookmakers") or [None])[0]}
        return {"ok": False, "step": "event_markets", "diag": diag, "raw": res}
