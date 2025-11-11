# app/services/espn_cbb.py
from __future__ import annotations

import httpx
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone
import logging
import re

logger = logging.getLogger("app.espn_cbb")

# ESPN "site" scoreboard base for Men's college basketball
SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _ny_today_yyyymmdd() -> str:
    """Treat 'today' as America/New_York (project convention: UTC-5 fallback)."""
    ny_now = datetime.now(timezone.utc) - timedelta(hours=5)
    return ny_now.strftime("%Y%m%d")


def _yyyymmdd(date_str: Optional[str]) -> str:
    """
    Normalize incoming date to strictly 'YYYYMMDD'.
    Accepts YYYYMMDD, YYYY-MM-DD, or ISO-like values; falls back to NY 'today'.
    """
    if not date_str:
        return _ny_today_yyyymmdd()

    digits = re.sub(r"\D", "", date_str)
    if len(digits) == 8:
        return digits

    try:
        dt = datetime.fromisoformat(date_str[:10])
        return dt.strftime("%Y%m%d")
    except Exception:
        logger.warning("espn_cbb: could not parse date '%s', falling back to NY today", date_str)
        return _ny_today_yyyymmdd()


async def _get_json(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Tiny retry wrapper."""
    last = None
    async with httpx.AsyncClient(timeout=12.0, headers=HEADERS) as client:
        for attempt in range(2):
            try:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
                logger.warning("espn_cbb _get_json attempt %d failed: %s", attempt + 1, e)
    raise last or RuntimeError("unknown http error")


def _site_params(date_yyyymmdd: str, d1_only: bool, add_range: bool = False) -> Dict[str, Any]:
    """
    Build ESPN site params.
    Prefer single-day 'dates=YYYYMMDD'; fall back to range if we must.
    """
    dates_val = f"{date_yyyymmdd}-{date_yyyymmdd}" if add_range else date_yyyymmdd
    params: Dict[str, Any] = {"dates": dates_val, "limit": 500}
    if d1_only:
        params["groups"] = 50  # Division I filter (matches ESPN UI)
    return params


async def _fetch_site(date_yyyymmdd: str, d1_only: bool) -> Dict[str, Any]:
    """
    Try single-day + groups first; on 400, retry without groups; then try range.
    """
    # 1) single-day with groups (if any)
    params = _site_params(date_yyyymmdd, d1_only=d1_only, add_range=False)
    try:
        return await _get_json(SITE_BASE, params)
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 400:
            logger.info("espn_cbb: retrying without groups for date=%s", date_yyyymmdd)
            # 2) single-day without groups
            params2 = _site_params(date_yyyymmdd, d1_only=False, add_range=False)
            try:
                return await _get_json(SITE_BASE, params2)
            except httpx.HTTPStatusError as e2:
                if e2.response is not None and e2.response.status_code == 400:
                    logger.info("espn_cbb: retrying with range format for date=%s", date_yyyymmdd)
                    # 3) range with/without groups as last resort
                    params3 = _site_params(date_yyyymmdd, d1_only=d1_only, add_range=True)
                    try:
                        return await _get_json(SITE_BASE, params3)
                    except Exception:
                        params4 = _site_params(date_yyyymmdd, d1_only=False, add_range=True)
                        return await _get_json(SITE_BASE, params4)
                raise
        raise


# app/services/espn_cbb.py

def extract_game_lite(ev: dict) -> dict:
    """
    Flatten an ESPN CBB event into a lite row for the API.
    Now includes ESPN team IDs for use by /api/form/matchup and GPT.
    """
    comp = (ev.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []

    home = next(
        (c for c in competitors if c.get("homeAway") == "home"),
        competitors[0] if competitors else {},
    )
    away = next(
        (c for c in competitors if c.get("homeAway") == "away"),
        competitors[1] if len(competitors) > 1 else {},
    )

    home_team = home.get("team") or {}
    away_team = away.get("team") or {}

    return {
        "gameId": ev.get("id"),
        "homeTeam": home_team.get("displayName"),
        "homeTeamId": home_team.get("id"),
        "awayTeam": away_team.get("displayName"),
        "awayTeamId": away_team.get("id"),
        "startTime": ev.get("date"),
        "league": (ev.get("league") or {}).get("name"),
    }



def extract_matchup_detail(event: Dict[str, Any]) -> Dict[str, Any]:
    lite = extract_game_lite(event)
    return {
        "gameId": lite["gameId"],
        "homeTeam": lite["homeTeam"],
        "awayTeam": lite["awayTeam"],
        "startTime": lite["startTime"],
        "notes": None,
    }


async def get_games_for_date(date: Optional[str] = None, d1_only: bool = True) -> List[Dict[str, Any]]:
    """
    Load ESPN CBB scoreboard for a single date.
    - date: 'YYYYMMDD' or 'YYYY-MM-DD' or None (today in NY)
    - d1_only: True -> request NCAA Division I only (groups=50).
               If no D1 events found, we auto-fallback to ALL levels for that date.
    """
    d = _yyyymmdd(date)
    logger.info("CBB get_games_for_date date=%s d1_only=%s", d, d1_only)

    # First try (with current d1_only setting)
    data = await _fetch_site(d, d1_only=d1_only)
    events = data.get("events") or []
    if d1_only and len(events) == 0:
        logger.info("CBB get_games_for_date: D1 returned 0 events for %s — auto-fallback to ALL levels", d)
        data2 = await _fetch_site(d, d1_only=False)
        events = data2.get("events") or []

    logger.info("CBB get_games_for_date returned %d events", len(events))
    return events


# ---------- Optional: Top-25 helpers ----------
from typing import Optional as _Optional

def _team_rank(team: dict) -> _Optional[int]:
    try:
        r = (team.get("curatedRank") or {}).get("current", None)
        if r is None:
            r = team.get("rank", None)
        return int(r) if r is not None else None
    except Exception:
        return None

def _event_has_top25(event: dict, only_unranked_opponent: bool = False) -> bool:
    comps = (((event or {}).get("competitions") or [{}])[0].get("competitors")) or []
    ranks = []
    for c in comps:
        team = (c or {}).get("team") or {}
        ranks.append(_team_rank(team))
    if not ranks:
        return False
    top25_count = sum(1 for r in ranks if (r is not None and r <= 25))
    if top25_count == 0:
        return False
    if not only_unranked_opponent:
        return True
    return top25_count == 1

async def get_top25_for_date(date: _Optional[str] = None, only_unranked_opponent: bool = False, d1_only: bool = True) -> list[dict]:
    events = await get_games_for_date(date, d1_only=d1_only)
    return [ev for ev in events if _event_has_top25(ev, only_unranked_opponent)]
# ---------------------------------------------
