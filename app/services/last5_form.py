# app/services/last5_form.py

import datetime as dt
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("app.form")

# -----------------------------
# ESPN SCOREBOARD CONFIG PER SPORT
# -----------------------------

SPORT_CONFIG = {
    # Men's College Basketball
    "cbb": {
        "url": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard",
        "params": {"groups": 50, "limit": 500},  # D1
    },
    # NFL
    "nfl": {
        "url": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
        "params": {"limit": 500},
    },
    # NHL
    "nhl": {
        "url": "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard",
        "params": {"limit": 500},
    },
    # College Football (FBS+)
    "cfb": {
        "url": "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
        "params": {"groups": 80, "limit": 500},  # all D1
    },
}


# -----------------------------
# HTTP + ESPN HELPERS
# -----------------------------

async def _get_json(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Simple wrapper around httpx to fetch JSON with basic retry.
    """
    last_exc: Optional[Exception] = None
    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(2):
            try:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()
            except Exception as exc:
                last_exc = exc
                logger.warning("FORM _get_json attempt %s failed: %s", attempt + 1, exc)
        logger.error("FORM _get_json failed for %s params=%s: %s", url, params, last_exc)
        raise last_exc or RuntimeError("unknown http error")


async def _fetch_scoreboard_events(
    sport: str,
    date_str: str,
) -> List[Dict[str, Any]]:
    """
    Fetch ESPN scoreboard events for a given sport + date (YYYYMMDD).
    """
    cfg = SPORT_CONFIG.get(sport)
    if not cfg:
        logger.warning("FORM: unsupported sport=%s", sport)
        return []

    params = dict(cfg["params"])
    params["dates"] = date_str

    data = await _get_json(cfg["url"], params)
    events = data.get("events") or []
    logger.info("FORM %s scoreboard %s -> %d events", sport, date_str, len(events))
    return events


def _extract_team_view_from_event(
    sport: str,
    team_id: str,
    event: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Given a raw ESPN scoreboard event and a team_id, return a "view" of that game
    from the perspective of that team: final score, 1H score, opponent, etc.

    Only returns data for COMPLETED games ("post").
    """
    try:
        competitions = event.get("competitions") or []
        if not competitions:
            return None
        comp = competitions[0]

        status = (comp.get("status") or {}).get("type") or {}
        state = status.get("state")
        if state != "post":
            # we only want completed games
            return None

        competitors = comp.get("competitors") or []
        if len(competitors) < 2:
            return None

        team_id_str = str(team_id)

        team_comp: Optional[Dict[str, Any]] = None
        opp_comp: Optional[Dict[str, Any]] = None

        for c in competitors:
            team = c.get("team") or {}
            tid = str(team.get("id"))
            if tid == team_id_str:
                team_comp = c
            else:
                # first non-matching competitor is our opponent
                if opp_comp is None:
                    opp_comp = c

        if not team_comp or not opp_comp:
            return None

        def _get_scores(c: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
            try:
                full = c.get("score")
                full_int = int(full) if full is not None and full != "" else None
            except Exception:
                full_int = None

            lines = c.get("linescores") or []
            first_half: Optional[int] = None
            if lines:
                # For CBB and many sports, first entry is 1H/1st period.
                try:
                    val = lines[0].get("value")
                    first_half = int(val) if val is not None and val != "" else None
                except Exception:
                    first_half = None
            return full_int, first_half

        t_full, t_1h = _get_scores(team_comp)
        o_full, o_1h = _get_scores(opp_comp)

        t_team = team_comp.get("team") or {}
        o_team = opp_comp.get("team") or {}

        view = {
            "eventId": event.get("id"),
            "sport": sport,
            "teamId": t_team.get("id"),
            "teamName": t_team.get("displayName") or t_team.get("name"),
            "teamAbbr": t_team.get("abbreviation"),
            "opponentId": o_team.get("id"),
            "opponentName": o_team.get("displayName") or o_team.get("name"),
            "opponentAbbr": o_team.get("abbreviation"),
            "isHome": (team_comp.get("homeAway") == "home"),
            "final": t_full,
            "oppFinal": o_full,
            "firstHalf": t_1h,
            "oppFirstHalf": o_1h,
            "state": state,
            "date": event.get("date"),
        }
        return view
    except Exception as exc:
        logger.exception("FORM extract_team_view failed: %s", exc)
        return None


async def _get_last_n_games_for_team_generic(
    sport: str,
    team_id: str,
    n: int = 5,
    max_days_back: int = 60,
) -> List[Dict[str, Any]]:
    """
    Generic "last N games" via ESPN scoreboard for any supported sport.

    - Looks back up to `max_days_back` days from TODAY (UTC).
    - Only counts completed games ("post").
    - Returns newest -> oldest, but capped at N.
    """
    today = dt.datetime.utcnow().date()
    team_id_str = str(team_id)

    games: List[Dict[str, Any]] = []

    for delta in range(max_days_back):
        if len(games) >= n:
            break

        day = today - dt.timedelta(days=delta)
        date_str = day.strftime("%Y%m%d")

        events = await _fetch_scoreboard_events(sport, date_str)
        if not events:
            continue

        for ev in events:
            if len(games) >= n:
                break
            view = _extract_team_view_from_event(sport, team_id_str, ev)
            if view is None:
                continue
            games.append(view)

    # Newest first (we're already going newest -> oldest by date, but sort just in case)
    games.sort(key=lambda g: g.get("date") or "", reverse=True)

    if len(games) > n:
        games = games[:n]

    logger.info("FORM %s team=%s -> %d games found", sport, team_id, len(games))
    return games


# -----------------------------
# PUBLIC API FOR ROUTERS
# -----------------------------

async def get_form_summary(
    sport: str,
    team_id: str | int,
    n: int = 5,
) -> Dict[str, Any]:
    """
    Returns last N games for a given team in a given sport.

    Shape:
    {
      "sport": "cbb",
      "teamId": "150",
      "nRequested": 5,
      "nFound": 2,
      "games": [ ... ]   # newest -> oldest
    }
    """
    sport = sport.lower()
    if sport not in SPORT_CONFIG:
        return {
            "sport": sport,
            "teamId": str(team_id),
            "nRequested": n,
            "nFound": 0,
            "games": [],
            "note": "unsupported sport",
        }

    games = await _get_last_n_games_for_team_generic(sport, str(team_id), n=n)
    return {
        "sport": sport,
        "teamId": str(team_id),
        "nRequested": n,
        "nFound": len(games),
        "games": games,
    }


async def get_matchup_form(
    sport: str,
    team1_id: str | int,
    team2_id: str | int,
    n: int = 5,
) -> Dict[str, Any]:
    """
    Returns last N games for both teams in a matchup.

    Shape:
    {
      "sport": "cbb",
      "nRecent": 5,
      "team1": { ... get_form_summary(...) ... },
      "team2": { ... get_form_summary(...) ... }
    }
    """
    sport = sport.lower()
    t1 = await get_form_summary(sport, team1_id, n)
    t2 = await get_form_summary(sport, team2_id, n)

    return {
        "sport": sport,
        "nRecent": n,
        "team1": t1,
        "team2": t2,
    }
