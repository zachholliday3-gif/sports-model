# app/services/espn_cfb.py

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from app.services.espn_common import _get_json, normalize_date_param

logger = logging.getLogger("app.espn_cfb")

# ESPN CFB scoreboard endpoint
SITE_BASE = (
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
)


# ----------------------------------------------------------------------
# FETCH GAMES FOR A DATE — WITH AUTO-FALLBACK IF FBS-ONLY RETURNS 0
# ----------------------------------------------------------------------
async def get_games_for_date(
    date: Optional[str] = None,
    fbs_only: bool = True,
) -> List[Dict[str, Any]]:
    """
    Fetch CFB games for a date.

    LOGIC:
    - Normalize date (YYYYMMDD)
    - If fbs_only=True:
        → Try ESPN group=80 (FBS)
        → If 0 events → remove group filter and re-fetch ALL levels
    - If fbs_only=False:
        → Always fetch ALL levels
    """

    date_str = normalize_date_param(date)
    params: Dict[str, Any] = {"dates": date_str, "limit": 500}

    # First attempt: FBS-only
    if fbs_only:
        params["groups"] = 80  # ESPN’s FBS group

    logger.info(
        "CFB get_games_for_date date=%s fbs_only=%s params=%s",
        date_str, fbs_only, params,
    )

    # First fetch
    data = await _get_json(SITE_BASE, params)
    events = data.get("events") or []
    logger.info(
        "CFB get_games_for_date %s -> %d events (initial)",
        date_str, len(events)
    )

    # Fallback if FBS-only returned nothing
    if fbs_only and not events:
        logger.info(
            "CFB get_games_for_date: FBS group returned 0 events for %s — auto-fallback to ALL levels",
            date_str,
        )

        # Remove the FBS filter
        params.pop("groups", None)

        # Re-fetch ALL levels
        data = await _get_json(SITE_BASE, params)
        events = data.get("events") or []

        logger.info(
            "CFB get_games_for_date (fallback ALL) %s -> %d events",
            date_str, len(events)
        )

    return events


# ----------------------------------------------------------------------
# FULL-GAME PROJECTION (SIMPLE MODEL)
# ----------------------------------------------------------------------
def project_cfb_fg(game: Dict[str, Any]) -> Dict[str, Any]:
    """
    Very simple placeholder model: average team stats from ESPN game object.
    This was in your original code — preserved so API doesn't break.
    """
    comps = game.get("competitions", [{}])[0]
    if not comps:
        return {}

    competitors = comps.get("competitors", [])
    if len(competitors) != 2:
        return {}

    home = next((t for t in competitors if t.get("homeAway") == "home"), None)
    away = next((t for t in competitors if t.get("homeAway") == "away"), None)

    if not home or not away:
        return {}

    # naive projected scoring from ESPN's game summary
    home_score = float(home.get("score", 0))
    away_score = float(away.get("score", 0))

    proj_total = home_score + away_score
    proj_spread_home = home_score - away_score

    return {
        "homeTeam": home.get("team", {}).get("displayName"),
        "awayTeam": away.get("team", {}).get("displayName"),
        "homeId": home.get("team", {}).get("id"),
        "awayId": away.get("team", {}).get("id"),
        "projTotal": proj_total,
        "projSpreadHome": proj_spread_home,
        "confidence": 0.55,  # placeholder
    }
