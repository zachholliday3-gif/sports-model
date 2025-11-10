# app/services/espn_nhl.py
import httpx
import logging
from datetime import datetime
from app.models.shared import GameLite

logger = logging.getLogger("app.nhl")

SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"

async def _get_json(base, params):
    last = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(base, params=params)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            last = e
            logger.warning("espn_nhl _get_json attempt %s failed: %s", attempt + 1, e)
    raise last or RuntimeError("unknown http error")

def _extract_game_lite(ev):
    cid = ev.get("competitions", [{}])[0]
    comps = cid.get("competitors", [])
    if len(comps) < 2:
        return None

    home = next((c for c in comps if c.get("homeAway") == "home"), comps[0])
    away = next((c for c in comps if c.get("homeAway") == "away"), comps[1])

    return {
        "gameId": ev.get("id"),
        "homeTeam": home.get("team", {}).get("displayName"),
        "awayTeam": away.get("team", {}).get("displayName"),
        "status": cid.get("status", {}).get("type", {}).get("description"),
        "startTime": cid.get("date"),
    }

async def get_games_for_date(date=None):
    """Fetch NHL games for the given date (YYYY-MM-DD or None for today)."""
    if not date:
        date = datetime.utcnow().strftime("%Y-%m-%d")

    params = {"dates": f"{date}-{date}", "limit": 500}
    logger.info("NHL get_games_for_date %s", params)

    data = await _get_json(SITE_BASE, params)
    events = data.get("events", [])
    return [_extract_game_lite(e) for e in events if e.get("id")]

def project_nhl_fg(home_team, away_team):
    """Simple placeholder model for NHL full-game projection."""
    import random
    base_total = round(random.uniform(5.5, 6.5), 1)
    spread = round(random.uniform(-1.5, 1.5), 1)
    confidence = round(random.uniform(0.5, 0.95), 2)

    return {
        "projTotal": base_total,
        "projSpreadHome": spread,
        "confidence": confidence,
    }
