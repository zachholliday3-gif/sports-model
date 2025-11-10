# app/services/espn_cfb.py
from __future__ import annotations

import httpx
import logging
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Dict, List, Optional
import random

logger = logging.getLogger("app.espn_cfb")

# ESPN College Football scoreboard (NOTE: this is football/college-football)
SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


# ---------- Date helpers (same style as CBB/NHL/NFL) ----------

def _ny_today_yyyymmdd() -> str:
    """
    Treat 'today' as America/New_York (UTC-5 style),
    consistent with your other sports.
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
    digits = re.sub(r"\D", "", s)
    if len(digits) == 8:
        return digits

    try:
        dt = datetime.fromisoformat(s[:10])
        return dt.strftime("%Y%mdd")
    except Exception:
        logger.warning("espn_cfb: could not parse date '%s', falling back to NY today", date_str)
        return _ny_today_yyyymmdd()


# ---------- HTTP helper ----------

async def _get_json(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Small retry wrapper for ESPN CFB."""
    last = None
    async with httpx.AsyncClient(timeout=10.0, headers=HEADERS) as client:
        for attempt in range(2):
            try:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
                logger.warning("espn_cfb _get_json attempt %d failed: %s", attempt + 1, e)
    raise last or RuntimeError("unknown http error")


# ---------- Params + extraction ----------

def _site_params(date_yyyymmdd: str, fbs_only: bool) -> Dict[str, Any]:
    """
    Build ESPN site params.

    - ESPN CFB 'groups=80' ~= FBS (top division)
    - If fbs_only=True, we send groups=80 first; if that returns 0 events,
      we'll retry without groups (all levels).
    """
    params: Dict[str, Any] = {
        "dates": date_yyyymmdd,
        "limit": 500,
    }
    if fbs_only:
        params["groups"] = 80  # FBS group
    return params


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

async def get_games_for_date(date: Optional[str] = None, fbs_only: bool = True) -> List[Dict[str, Any]]:
    """
    Fetch College Football games for the given date.

    - date: 'YYYYMMDD' or 'YYYY-MM-DD' or None (today in NY)
    - fbs_only: if True, try FBS (groups=80) first.
      If that returns 0 events, auto-fallback to ALL levels.
    """
    d = _yyyymmdd(date)
    logger.info("CFB get_games_for_date date=%s fbs_only=%s", d, fbs_only)

    # 1) Try with FBS-only if requested
    params = _site_params(d, fbs_only=fbs_only)
    data = await _get_json(SITE_BASE, params)
    events = data.get("events") or []

    # If we insisted on FBS and got nothing, try all levels
    if fbs_only and len(events) == 0:
        logger.info("CFB get_games_for_date: FBS-only returned 0 events for %s; retrying without group filter", d)
        params2 = _site_params(d, fbs_only=False)
        data2 = await _get_json(SITE_BASE, params2)
        events = data2.get("events") or []

    out: List[Dict[str, Any]] = []
    for ev in events:
        lite = _extract_game_lite(ev)
        if lite and lite.get("homeTeam") and lite.get("awayTeam"):
            out.append(lite)

    logger.info("CFB get_games_for_date %s -> %d events", d, len(out))
    return out


def project_cfb_fg(home_team: str, away_team: str) -> Dict[str, Any]:
    """
    Simple placeholder full-game projection for College Football.
    Later you can wire a real model into this.
    """
    # College scoring tends to be higher and more volatile
    base_total = round(random.uniform(48.0, 70.0), 1)
    # Spreads can be wide in CFB
    spread = round(random.uniform(-21.0, 21.0), 1)
    # Arbitrary confidence range
    confidence = round(random.uniform(0.55, 0.9), 2)

    return {
        "projTotal": base_total,
        "projSpreadHome": spread,
        "confidence": confidence,
    }
