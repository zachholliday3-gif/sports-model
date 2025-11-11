# app/services/last5_form.py

from __future__ import annotations

from typing import Dict, Any, List, Tuple
from datetime import datetime, timedelta, timezone
import httpx
import logging

logger = logging.getLogger("app.last5")

ESPN_SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports"

# Supported sports and how to interpret periods
SPORT_CONFIG = {
    "cbb": {
        "site_path": "basketball/mens-college-basketball",
        "period_mode": "half",   # 1H = first half
    },
    "nfl": {
        "site_path": "football/nfl",
        "period_mode": "quarter",  # 1H = Q1 + Q2
    },
    "cfb": {
        "site_path": "football/college-football",
        "period_mode": "quarter",  # 1H = Q1 + Q2
    },
    "nhl": {
        "site_path": "hockey/nhl",
        "period_mode": "period",   # "1H" = 1st period
    },
}


async def _get_json(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Simple HTTP GET with basic error handling for ESPN JSON.
    """
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(url, params=params)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.warning("last5 _get_json failed: %s", e)
            raise
        return resp.json()


def _extract_scores_for_team(
    comp: Dict[str, Any],
    team_id: str,
    sport: str,
) -> Tuple[int, int, int, int]:
    """
    From one ESPN competition, pull full-game + 1H-equivalent scores
    for the given team_id and its opponent.

    Returns: (team1H, opp1H, teamFull, oppFull)
    """
    competitors = comp.get("competitors") or comp.get("competitors", [])
    if not competitors or len(competitors) < 2:
        return 0, 0, 0, 0

    idx = None
    for i, c in enumerate(competitors):
        team = c.get("team") or {}
        if str(team.get("id")) == str(team_id):
            idx = i
            break

    if idx is None:
        return 0, 0, 0, 0

    team_comp = competitors[idx]
    opp_comp = competitors[1 - idx]

    # Full-game scores
    try:
        team_full = int(team_comp.get("score") or 0)
    except ValueError:
        team_full = 0
    try:
        opp_full = int(opp_comp.get("score") or 0)
    except ValueError:
        opp_full = 0

    # 1H-equivalent scores
    lines_team = team_comp.get("linescores") or []
    lines_opp = opp_comp.get("linescores") or []

    def _val(ls, i):
        try:
            return int(ls[i].get("value") or 0)
        except Exception:
            return 0

    mode = SPORT_CONFIG[sport]["period_mode"]

    if mode == "half":
        # 1H = first half only
        team_1h = _val(lines_team, 0)
        opp_1h = _val(lines_opp, 0)
    elif mode == "quarter":
        # 1H = Q1 + Q2
        team_1h = _val(lines_team, 0) + _val(lines_team, 1)
        opp_1h = _val(lines_opp, 0) + _val(lines_opp, 1)
    elif mode == "period":
        # 1H = first period
        team_1h = _val(lines_team, 0)
        opp_1h = _val(lines_opp, 0)
    else:
        team_1h = 0
        opp_1h = 0

    return team_1h, opp_1h, team_full, opp_full


async def get_last5_for_team(
    sport: str,
    team_id: str,
    n: int = 5,
    max_days_back: int = 90,
) -> Dict[str, Any]:
    """
    Generic ESPN-based "last N games" fetcher.

    - sport: one of "cbb", "nfl", "cfb", "nhl"
    - team_id: ESPN team id as string
    - n: number of games to collect (default 5)
    - max_days_back: safety window so we don't loop forever

    Returns structure with games + averages.
    """
    sport = sport.lower()
    if sport not in SPORT_CONFIG:
        raise ValueError(f"Unsupported sport: {sport}")

    site_path = SPORT_CONFIG[sport]["site_path"]
    url = f"{ESPN_SITE_BASE}/{site_path}/scoreboard"

    today = datetime.now(timezone.utc).date()
    games: List[Dict[str, Any]] = []
    team_name = None

    # Look backwards day-by-day until we get N games or hit max_days_back
    for day_offset in range(max_days_back):
        if len(games) >= n:
            break

        day = today - timedelta(days=day_offset + 1)  # only past days
        dates_param = day.strftime("%Y%m%d")
        params = {
            "dates": dates_param,
            "limit": 500,
        }

        try:
            data = await _get_json(url, params)
        except Exception as e:
            logger.warning("last5 scoreboard fetch failed: sport=%s date=%s err=%s", sport, dates_param, e)
            continue

        events = data.get("events") or []
        for ev in events:
            if len(games) >= n:
                break

            # competitions[0] is the game
            comps = ev.get("competitions") or []
            if not comps:
                continue
            comp = comps[0]

            status = comp.get("status", {}).get("type", {})
            state = status.get("state")
            completed = status.get("completed", False)
            if not completed and state != "post":
                # skip non-final games
                continue

            competitors = comp.get("competitors") or []
            if len(competitors) < 2:
                continue

            # Is our team in this game?
            this_idx = None
            for i, c in enumerate(competitors):
                t = c.get("team") or {}
                if str(t.get("id")) == str(team_id):
                    this_idx = i
                    if team_name is None:
                        team_name = t.get("displayName") or t.get("name") or str(team_id)
                    break

            if this_idx is None:
                continue  # not our team

            this_comp = competitors[this_idx]
            opp_comp = competitors[1 - this_idx]
            opp_team = opp_comp.get("team") or {}

            team_1h, opp_1h, team_full, opp_full = _extract_scores_for_team(comp, team_id, sport)

            # Build row
            row = {
                "eventId": str(ev.get("id") or comp.get("id")),
                "date": ev.get("date"),
                "opponent": opp_team.get("displayName") or opp_team.get("name"),
                "opponentId": str(opp_team.get("id")) if opp_team.get("id") is not None else None,
                "homeAway": this_comp.get("homeAway"),
                "teamScore1H": team_1h,
                "oppScore1H": opp_1h,
                "total1H": team_1h + opp_1h,
                "teamScoreFull": team_full,
                "oppScoreFull": opp_full,
                "totalFull": team_full + opp_full,
                "result": None,
            }

            if team_full > opp_full:
                row["result"] = "W"
            elif team_full < opp_full:
                row["result"] = "L"
            else:
                row["result"] = "T"

            games.append(row)

        # end for events

    # Compute averages
    def _avg(vals: List[int]) -> float:
        if not vals:
            return 0.0
        return round(sum(vals) / len(vals), 2)

    avg_1h_scored = _avg([g["teamScore1H"] for g in games])
    avg_1h_allowed = _avg([g["oppScore1H"] for g in games])
    avg_1h_total = _avg([g["total1H"] for g in games])

    avg_full_scored = _avg([g["teamScoreFull"] for g in games])
    avg_full_allowed = _avg([g["oppScoreFull"] for g in games])
    avg_full_total = _avg([g["totalFull"] for g in games])

    return {
        "sport": sport,
        "teamId": str(team_id),
        "teamName": team_name or str(team_id),
        "nRequested": n,
        "nFound": len(games),
        "games": games,
        "avg1H_scored": avg_1h_scored,
        "avg1H_allowed": avg_1h_allowed,
        "avg1H_total": avg_1h_total,
        "avgFull_scored": avg_full_scored,
        "avgFull_allowed": avg_full_allowed,
        "avgFull_total": avg_full_total,
    }
