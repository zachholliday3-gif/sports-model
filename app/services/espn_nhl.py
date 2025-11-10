# app/services/espn_nhl.py
from __future__ import annotations

import httpx
import logging
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Dict, List, Optional
import random

logger = logging.getLogger("app.espn_nhl")

# ESPN NHL scoreboard (site)
SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


# ---------- Date helpers (same pattern as CBB/NFL style) ----------

def _ny_today_yyyymmdd() -> str:
    """
    Treat 'today' as America/New_York (similar convention as CBB/NFL:
    UTC minus 5 hours).
    """
    ny_now = datetime.now(timezone.utc) - timedelta(hours=5)
    return ny_now.strftime("%Y%m%d")


def _yyyymmdd(date_str: Optional[str]) -> str:
    """
    Normalize incoming date to strictly 'YYYYMMDD'.

    Accepts:
      - 'YYYYMMDD'
      - 'YYYY-MM-DD'
      - other ISO-like strings

    If missing or invalid, falls back to NY 'today'.
    """
    if not date_str:
        return _ny_today_yyyymmdd()

    s = date_str.strip()
    # Strip everything but digits
    digits = re.sub(r"\D", "", s)
    if len(digits) == 8:
        return digits

    # Fallback: try parse first 10 chars (likely 'YYYY-MM-DD')
    try:
        dt = datetime.fromisoformat(s[:10])
        return dt.strftime("%Y%m%d")
    except Exception:
        logger.warning("espn_nhl: could not parse date '%s', falling back to NY today", date_str)
        return _ny_today_yyyymmdd()


# ---------- HTTP helper ----------

async def _get_json(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Small retry wrapper for ESPN NHL."""
    last = None
    async with httpx.AsyncClient(timeout=10.0, headers=HEADERS) as client:
        for attempt in range(2):
            try:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
                logger.warning("espn_nhl _get_json attempt %d failed: %s", attempt + 1, e)
    raise last or RuntimeError("unknown http error")


# ---------- Extraction ----------

def _extract_game_lite(ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    comps = (ev.get("competitions") or [{}])[0]
    teams = comps.get("competitors") or []
    if len(teams) < 2:
        return None

    home = next((c for c in teams if c.get("homeAway") == "home"), teams[0])
    away = next((c for c in teams if c.get("homeAway") == "away"), teams[-1])

    return {
        "gameId": ev.get("id"),
        "homeTeam": (home.get("team") or {}).get("displayName"),
        "awayTeam": (away.get("team") or {}).get("displayName"),
        "status": (comps.get("status") or {}).get("type", {}).get("description"),
        "startTime": comps.get("date"),
    }


# ---------- Public API ----------

async def get_games_for_date(date: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetch NHL games for the given date.

    - date: 'YYYYMMDD' or 'YYYY-MM-DD' or None (today in NY)
    - ESPN NHL expects 'dates' in 'YYYYMMDD' format (no range).
    """
    d = _yyyymmdd(date)
    params = {"dates": d, "limit": 500}
    logger.info("NHL get_games_for_date date=%s params=%s", d, params)

    data = await _get_json(SITE_BASE, params)
    events = data.get("events") or []
    out: List[Dict[str, Any]] = []

    for ev in events:
        lite = _extract_game_lite(ev)
        if lite and lite.get("homeTeam") and lite.get("awayTeam"):
            out.append(lite)

    logger.info("NHL get_games_for_date %s -> %d events", d, len(out))
    return out


def project_nhl_fg(home_team: str, away_team: str) -> Dict[str, Any]:
    """
    Simple placeholder NHL full-game projection.
    Later you can replace this with a real model fed from stats.
    """
    # Typical NHL scoring environment
    base_total = round(random.uniform(5.5, 6.8), 1)
    # Mild home edge in spread
    spread = round(random.uniform(-1.5, 1.5), 1)
    # Arbitrary confidence range
    confidence = round(random.uniform(0.55, 0.9), 2)

    return {
        "projTotal": base_total,
        "projSpreadHome": spread,
        "confidence": confidence,
    }
