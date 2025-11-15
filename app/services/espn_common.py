# app/services/espn_common.py

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

import httpx
from zoneinfo import ZoneInfo

logger = logging.getLogger("app.espn_common")


# -----------------------------------------------------------
# Shared HTTP helper with retries
# -----------------------------------------------------------
async def _get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    max_tries: int = 2,
) -> Dict[str, Any]:
    """
    Small shared helper for ESPN JSON fetch with basic retry + logging.

    Used by:
      - espn_cbb
      - espn_nfl
      - espn_nhl
      - espn_cfb
    """
    last: Optional[Exception] = None

    for attempt in range(1, max_tries + 1):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            last = e
            logger.warning(
                "espn_common _get_json attempt %s failed: %s",
                attempt,
                repr(e),
            )

    # If we get here, all retries failed
    logger.error(
        "espn_common _get_json giving up after %s attempts: %s",
        max_tries,
        repr(last),
    )
    raise last or RuntimeError("unknown http error")


# -----------------------------------------------------------
# Date normalization helper (NY-local “today” by default)
# -----------------------------------------------------------
def normalize_date_param(date: Optional[str]) -> str:
    """
    Normalize a date for ESPN's `dates` param.

    Accepts:
      - None            -> today's date in America/New_York, YYYYMMDD
      - 'YYYYMMDD'      -> returned unchanged
      - 'YYYY-MM-DD'    -> dashes removed
      - anything else   -> returned as-is (caller responsibility)
    """
    if date:
        s = date.strip()
        # Already in YYYYMMDD
        if len(s) == 8 and s.isdigit():
            return s
        # Try simple YYYY-MM-DD -> YYYYMMDD
        if len(s) == 10 and "-" in s:
            parts = s.split("-")
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                return "".join(parts)
        # Fallback: trust caller
        return s

    # Default: "today" in America/New_York
    try:
        now = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        # Fallback to naive local time if zoneinfo fails for any reason
        now = datetime.now()

    return now.strftime("%Y%m%d")


# -----------------------------------------------------------
# Generic GameLite extraction helper
# -----------------------------------------------------------
def extract_game_lite(ev: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a single ESPN scoreboard event into a lightweight game object.
    Adjust keys/structure to match your GameLite schema.
    """
    comp = ev["competitions"][0]
    # ESPN marks competitors as "home"/"away"
    home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
    away = next(c for c in comp["competitors"] if c["homeAway"] == "away")

    return {
        "event_id": ev["id"],
        "start_time": ev["date"],  # or parse to datetime if your schema needs it
        "short_name": ev.get("shortName"),
        "status": comp["status"]["type"]["name"],   # e.g. "pre", "in", "post"
        "home_team_id": int(home["team"]["id"]),
        "home_team_name": home["team"]["displayName"],
        "away_team_id": int(away["team"]["id"]),
        "away_team_name": away["team"]["displayName"],
        "neutral_site": comp.get("neutralSite", False),
    }

    # If you have a GameLite model, you could do:
    # return GameLite(
    #     event_id=ev["id"],
    #     start_time=ev["date"],
    #     short_name=ev.get("shortName"),
    #     status=comp["status"]["type"]["name"],
    #     home_team_id=int(home["team"]["id"]),
    #     home_team_name=home["team"]["displayName"],
    #     away_team_id=int(away["team"]["id"]),
    #     away_team_name=away["team"]["displayName"],
    #     neutral_site=comp.get("neutralSite", False),
    # )
