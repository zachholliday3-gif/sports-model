# app/services/espn_nhl.py
from __future__ import annotations

import httpx
import logging
from datetime import datetime, timezone
import re
import random
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.espn_nhl")

SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _today_iso() -> str:
    """UTC 'today' as YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _normalize_date(date_str: Optional[str]) -> str:
    """
    Accept 'YYYY-MM-DD', 'YYYYMMDD', or similar, and normalize to 'YYYY-MM-DD'.
    If None/invalid, use today.
    """
    if not date_str:
        return _today_iso()

    s = date_str.strip()
    digits = re.sub(r"\D", "", s)
    if len(digits) == 8:
        # YYYYMMDD -> YYYY-MM-DD
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"

    # Fallback: keep first 10 chars (likely ISO)
    return s[:10]


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


async def get_games_for_date(date: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetch NHL games for the given date.
    - date: 'YYYY-MM-DD' or 'YYYYMMDD' or None (today UTC)
    """
    d = _normalize_date(date)
    params = {"dates": f"{d}-{d}", "limit": 500}
    logger.info("NHL get_games_for_date params=%s", params)

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
    Later you can replace this with a real model.
    """
    # Base total range ~ typical NHL scoring environment
    base_total = round(random.uniform(5.5, 6.8), 1)

    # Slight home advantage baked in
    spread = round(random.uniform(-1.5, 1.5), 1)

    # Arbitrary confidence for now
    confidence = round(random.uniform(0.55, 0.9), 2)

    return {
        "projTotal": base_total,
        "projSpreadHome": spread,
        "confidence": confidence,
    }
