# app/services/nfl_weeks.py
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def _first_sept_sunday(year: int) -> datetime:
    d = datetime(year, 9, 1, tzinfo=ZoneInfo("America/New_York"))
    while d.weekday() != 6:  # Sunday
        d += timedelta(days=1)
    return d

def week_window(season: int, week: int) -> tuple[str, str]:
    """
    Return (start_yyyymmdd, end_yyyymmdd) for the given NFL (season, week).

    Uses env var NFL_SEASON_{season}_WEEK1 as the Tuesday of week 1 (YYYYMMDD).
    If not set, estimate as the Tuesday before the first Sunday in September.

    Each week covers Tue..Mon (7 days) to span TNF/MNF windows.
    """
    key = f"NFL_SEASON_{season}_WEEK1"
    wk1 = os.getenv(key)

    if wk1 and len(wk1) == 8 and wk1.isdigit():
        t0 = datetime.strptime(wk1, "%Y%m%d").replace(tzinfo=ZoneInfo("America/New_York"))
    else:
        first_sun = _first_sept_sunday(season)  # a Sunday
        # Tuesday before that Sunday = Sunday - 5 days
        t0 = first_sun - timedelta(days=5)

    start = t0 + timedelta(days=(week - 1) * 7)
    end = start + timedelta(days=6)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _week1_tuesday(season: int) -> datetime:
    # reuse the logic in week_window(): Tuesday before first Sep Sunday, in NY
    first_sun = _first_sept_sunday(season)
    return (first_sun - timedelta(days=5)).replace(tzinfo=ZoneInfo("America/New_York"))

def current_season_week(now: datetime | None = None) -> tuple[int, int]:
    """
    Determine (season, week) for 'now' in America/New_York.
    Week runs Tue..Mon (consistent with week_window()).
    """
    tz = ZoneInfo("America/New_York")
    today = (now or datetime.now(tz)).astimezone(tz)

    # NFL regular season spans Sep-Jan; if in Jan/Feb, season is previous year.
    season = today.year
    t0 = _week1_tuesday(season)
    if today < t0:
        # before week1 of this year; use last season (e.g., Aug)
        season -= 1
        t0 = _week1_tuesday(season)

    # count weeks from t0 in 7-day buckets until today <= end
    w = 1
    while True:
        start = t0 + timedelta(days=(w - 1) * 7)
        end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        if start <= today <= end:
            return season, w
        w += 1
        # safety stop after 30 weeks
        if w > 30:
            # default fallback: week 1
            return season, 1
