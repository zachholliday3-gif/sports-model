# app/routers/form_routes.py

from fastapi import APIRouter, HTTPException, Query
import logging
from datetime import date, timedelta
from typing import List, Dict, Any

# Re-use your existing ESPN service modules instead of calling scoreboard directly
from app.services.espn_cbb import get_games_for_date as get_cbb_games_for_date
from app.services.espn_nfl import get_games_for_date as get_nfl_games_for_date
from app.services.espn_nhl import get_games_for_date as get_nhl_games_for_date
from app.services.espn_cfb import get_games_for_date as get_cfb_games_for_date

logger = logging.getLogger("app.form")

router = APIRouter(prefix="/form", tags=["Form"])


async def _fetch_games_for_day(sport: str, day: date) -> List[Dict[str, Any]]:
    """
    Use Zach's existing ESPN wrappers to fetch that day's games for a sport.

    We rely on:
      - app.services.espn_cbb.get_games_for_date
      - app.services.espn_nfl.get_games_for_date
      - app.services.espn_nhl.get_games_for_date
      - app.services.espn_cfb.get_games_for_date
    """
    date_str = day.strftime("%Y%m%d")

    if sport == "cbb":
        # d1_only=True to stay consistent with your main CBB slate behavior
        games = await get_cbb_games_for_date(date_str, d1_only=True)
    elif sport == "nfl":
        games = await get_nfl_games_for_date(date_str)
    elif sport == "nhl":
        games = await get_nhl_games_for_date(date_str)
    elif sport == "cfb":
        games = await get_cfb_games_for_date(date_str)
    else:
        raise ValueError(f"Unsupported sport for form: {sport}")

    logger.info("FORM %s %s -> %d events", sport, date_str, len(games))
    return games or []


def _event_includes_team(ev: Dict[str, Any], team_id: str) -> bool:
    comp = (ev.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []
    for c in competitors:
        team = c.get("team") or {}
        if str(team.get("id")) == str(team_id):
            return True
    return False


def _event_completed(ev: Dict[str, Any]) -> bool:
    comp = (ev.get("competitions") or [{}])[0]
    status = comp.get("status") or {}
    stype = status.get("type") or {}
    return bool(stype.get("completed"))


def _find_team_and_opp(ev: Dict[str, Any], team_id: str):
    """
    Given an ESPN event and a team id, return (our_competitor, opp_competitor).
    """
    comp = (ev.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []

    ours = None
    opp = None
    for c in competitors:
        team = c.get("team") or {}
        if str(team.get("id")) == str(team_id):
            ours = c
        else:
            opp = c

    return ours, opp


async def _get_last_n_games_for_team(
    sport: str,
    team_id: str,
    n: int = 5,
    max_back_days: int = 90,
) -> List[Dict[str, Any]]:
    """
    Look back from TODAY up to max_back_days, walking backwards day by day
    and collecting COMPLETED games for this team_id, up to at most n.

    Uses the same ESPN service functions as your main slates/projections,
    so team IDs and event shapes are consistent.
    """
    today = date.today()
    collected: List[Dict[str, Any]] = []

    for delta in range(max_back_days):
        if len(collected) >= n:
            break

        day = today - timedelta(days=delta)
        try:
            events = await _fetch_games_for_day(sport, day)
        except Exception as e:
            # Any HTTP or parsing issue for that day → log and skip
            logger.warning("FORM %s day=%s failed: %s", sport, day, e)
            continue

        for ev in events:
            if not _event_includes_team(ev, team_id):
                continue
            if not _event_completed(ev):
                continue

            collected.append(ev)

            if len(collected) >= n:
                break

    logger.info(
        "FORM %s team_id=%s requested=%d found=%d",
        sport,
        team_id,
        n,
        len(collected),
    )
    return collected


def _summarize_team_form(
    sport: str,
    team_id: str,
    games: List[Dict[str, Any]],
    n_requested: int,
) -> Dict[str, Any]:
    """
    Turn a list of ESPN events into averages.

    For now we compute FULL GAME scoring only.
    1H fields are included but left as None until we wire robust period parsing.
    """
    n_found = len(games)
    if n_found == 0:
        return {
            "sport": sport,
            "teamId": str(team_id),
            "teamName": str(team_id),
            "nRequested": n_requested,
            "nFound": 0,
            "avg1H_scored": None,
            "avg1H_allowed": None,
            "avgFull_scored": None,
            "avgFull_allowed": None,
        }

    total_full_scored = 0.0
    total_full_allowed = 0.0
    team_name = None

    for ev in games:
        ours, opp = _find_team_and_opp(ev, team_id)
        if not ours or not opp:
            continue

        team = ours.get("team") or {}
        if not team_name:
            team_name = (
                team.get("displayName")
                or team.get("name")
                or str(team_id)
            )

        # Scores come in as strings
        try:
            scored = float(ours.get("score") or 0)
        except ValueError:
            scored = 0.0
        try:
            allowed = float(opp.get("score") or 0)
        except ValueError:
            allowed = 0.0

        total_full_scored += scored
        total_full_allowed += allowed

    if not team_name:
        team_name = str(team_id)

    avg_full_scored = total_full_scored / n_found if n_found else None
    avg_full_allowed = total_full_allowed / n_found if n_found else None

    # 1H metrics left as None for now (can be wired later with period parsing)
    return {
        "sport": sport,
        "teamId": str(team_id),
        "teamName": team_name,
        "nRequested": n_requested,
        "nFound": n_found,
        "avg1H_scored": None,
        "avg1H_allowed": None,
        "avgFull_scored": avg_full_scored,
        "avgFull_allowed": avg_full_allowed,
    }


@router.get("/last5_team")
async def last5_team(
    sport: str = Query(..., regex="^(cbb|nfl|nhl|cfb)$"),
    teamId: str = Query(...),
    n: int = Query(5, ge=1, le=20),
):
    """
    Get last-N COMPLETED games for a single team, summarized as averages.
    Uses Zach's ESPN service functions and looks back from TODAY up to ~90 days.
    """
    games = await _get_last_n_games_for_team(sport, teamId, n=n)
    summary = _summarize_team_form(sport, teamId, games, n_requested=n)
    return summary


@router.get("/matchup")
async def matchup_form(
    sport: str = Query(..., regex="^(cbb|nfl|nhl|cfb)$"),
    team1Id: str = Query(...),
    team2Id: str = Query(...),
    n: int = Query(5, ge=1, le=20),
):
    """
    Get last-N form for two teams (team1 vs team2) for a given sport.

    Response shape:
    {
      "sport": "cbb",
      "nRequested": 5,
      "team1": { ... },
      "team2": { ... }
    }
    """
    try:
        games1 = await _get_last_n_games_for_team(sport, team1Id, n=n)
        games2 = await _get_last_n_games_for_team(sport, team2Id, n=n)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    team1_summary = _summarize_team_form(sport, team1Id, games1, n_requested=n)
    team2_summary = _summarize_team_form(sport, team2Id, games2, n_requested=n)

    return {
        "sport": sport,
        "nRequested": n,
        "team1": team1_summary,
        "team2": team2_summary,
    }
