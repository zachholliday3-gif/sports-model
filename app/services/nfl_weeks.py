# app/services/nfl_weeks.py
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

def _ny(dt: datetime) -> datetime:
    return dt.astimezone(ZoneInfo("America/New_York"))

def _first_sept_sunday(year: int) -> datetime:
    d = datetime(year, 9, 1, tzinfo=ZoneInfo("America/New_York"))
    while d.weekday() != 6:  # Sunday
        d += timedelta(days=1)
    return d

def week_window(season: int, week: int) -> tuple[str, str]:
    """
    Return (start_yyyymmdd, end_yyyymmdd) for the given NFL (season, week).
    Uses env var NFL_SEASON_{season}_WEEK1 as the Tuesday of week 1 (YYYYMMDD).
    If not set, we estimate as the Tuesday before the first Sunday of September.
    Each week covers Tue..Mon (7 days) to capture TNF/MNF windows.
    """
    key = f"NFL_SEASON_{season}_WEEK1"
    wk1 = os.getenv(key)
    if wk1 and len(wk1) == 8 and wk1.isdigit():
        t0 = datetime.strptime(wk1, "%Y%m%d").replace(tzinfo=ZoneInfo("America/New_York"))
    else:
        # estimate: Tuesday before first September Sunday
        first_sun = _first_sept_sunday(season)
        t0 = first_sun - timedelta(days=(first_sun.weekday() - 1) % 7 + 6)  # force Tu before that Sun
        # simpler: first Sept Sunday - 6 -> previous Monday; add 1 => Tuesday
        t0 = first_sun - timedelta(days=5)  # Tuesday before that Sunday

    start = t0 + timedelta(days=(week - 1) * 7)
    end = start + timedelta(days=6)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
