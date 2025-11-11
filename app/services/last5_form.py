# app/services/last5_form.py

import logging
from datetime import datetime, timedelta

from app.services import espn_cbb, espn_nfl, espn_nhl, espn_cfb

logger = logging.getLogger("app.form")


# -------------------------------------------------
# Helper: choose correct ESPN getter based on sport
# -------------------------------------------------
async def _get_games_for_date(sport: str, date_str: str, d1_only: bool = True):
    """
    Dispatch to the correct ESPN date-based fetcher for each sport.
    For CBB we support the d1_only filter (via groups=50 on ESPN).
    Other sports ignore d1_only.
    """
    if sport == "cbb":
        # Your existing CBB helper already understands d1_only
        return await espn_cbb.get_games_for_date(date_str, d1_only=d1_only)
    elif sport == "nfl":
        return await espn_nfl.get_games_for_date(date_str)
    elif sport == "nhl":
        return await espn_nhl.get_games_for_date(date_str)
    elif sport == "cfb":
        return await espn_cfb.get_games_for_date(date_str)
    else:
        raise ValueError(f"Unsupported sport: {sport}")


# -------------------------------------------------
# Core logic: last N completed games for one team
# -------------------------------------------------
async def _get_last_n_games_for_team(sport: str, team_id: str, n: int = 5):
    """
    Walk backward day by day until we collect up to `n` completed games for this team.

    To avoid hammering off-season dates, we:
      - Only look back up to ~90 days.
      - Stop early once we cross a rough season-start cutoff per sport.
      - Stop if we see 15 consecutive days with no games for this sport/team.
    """
    today = datetime.utcnow().date()
    results = []
    zero_days = 0

    # Approximate season start cutoffs
    cutoff_map = {
        "cbb": datetime(today.year, 11, 1).date(),   # early November
        "cfb": datetime(today.year, 8, 15).date(),   # mid-August
        "nfl": datetime(today.year, 9, 1).date(),    # early September
        "nhl": datetime(today.year, 9, 15).date(),   # mid-September
    }
    cutoff_date = cutoff_map.get(sport, datetime(today.year, 1, 1).date())

    for i in range(0, 90):  # up to ~90 days back
        dt = today - timedelta(days=i)

        # Don't search before the approximate season start
        if dt < cutoff_date:
            logger.info(
                "%s reached season cutoff %s for team %s, stopping search.",
                sport.upper(),
                cutoff_date,
                team_id,
            )
            break

        date_str = dt.strftime("%Y%m%d")
        games = await _get_games_for_date(sport, date_str, d1_only=True)

        if not games:
            zero_days += 1
            logger.info("FORM %s %s %s -> 0 events", sport, team_id, date_str)
            if zero_days >= 15:
                logger.info(
                    "%s no games for team %s after %d days — stopping early.",
                    sport.upper(),
                    team_id,
                    zero_days,
                )
                break
            continue

        zero_days = 0  # reset after a day that has games

        # Filter games for this team & that are final
        for g in games:
            home_id = str(g.get("homeId"))
            away_id = str(g.get("awayId"))
            if str(team_id) not in (home_id, away_id):
                continue

            # Only use completed / final games
            status = (g.get("status") or "").lower()
            if status not in ("final", "completed", "post"):
                continue

            results.append(g)
            if len(results) >= n:
                break

        if len(results) >= n:
            break

    logger.info("FORM %s %s -> %d games found", sport, team_id, len(results))
    return results


# -------------------------------------------------
# Public helpers for routers
# -------------------------------------------------
async def get_form_summary(sport: str, team_id: str, n: int = 5):
    """
    Return a summary of recent form for one team:
    - nRequested / nFound
    - avgFull_scored / avgFull_allowed
    - avg1H_scored / avg1H_allowed (when available in the ESPN payload)
    """
    games = await _get_last_n_games_for_team(sport, team_id, n)
    if not games:
        return {
            "sport": sport,
            "teamId": team_id,
            "nRequested": n,
            "nFound": 0,
        }

    scored_full = []
    allowed_full = []
    scored_1h = []
    allowed_1h = []

    for g in games:
        home_id = str(g.get("homeId"))
        away_id = str(g.get("awayId"))

        # ESPN-normalized scores (your event extraction should already set these)
        home_full = g.get("homeScore")
        away_full = g.get("awayScore")
        home_1h = g.get("homeScore1H")
        away_1h = g.get("awayScore1H")

        if str(team_id) == home_id:
            scored_full.append(home_full)
            allowed_full.append(away_full)
            scored_1h.append(home_1h)
            allowed_1h.append(away_1h)
        elif str(team_id) == away_id:
            scored_full.append(away_full)
            allowed_full.append(home_full)
            scored_1h.append(away_1h)
            allowed_1h.append(home_1h)

    def avg(arr):
        arr = [a for a in arr if isinstance(a, (int, float))]
        return round(sum(arr) / len(arr), 1) if arr else None

    return {
        "sport": sport,
        "teamId": team_id,
        "nRequested": n,
        "nFound": len(games),
        "avgFull_scored": avg(scored_full),
        "avgFull_allowed": avg(allowed_full),
        "avg1H_scored": avg(scored_1h),
        "avg1H_allowed": avg(allowed_1h),
    }


async def get_matchup_form(sport: str, team1_id: str, team2_id: str, n: int = 5):
    """
    Side-by-side form summary for both teams in a matchup.
    """
    team1 = await get_form_summary(sport, team1_id, n)
    team2 = await get_form_summary(sport, team2_id, n)
    return {
        "sport": sport,
        "nRequested": n,
        "team1": team1,
        "team2": team2,
    }
