# app/services/nfl_weeks.py
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Tuple
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

# NFL regular season baseline:
# Week 1 typically starts the Thursday following Labor Day (first Monday of September).
# We compute a season baseline and then map week -> [Thu..Mon] as a conservative window.

def _labor_day(year: int) -> date:
    """First Monday of September."""
    d = date(year, 9, 1)
    # weekday(): Monday=0 ... Sunday=6
    shift = (0 - d.weekday()) % 7
    return d + timedelta(days=shift)

def _week1_thursday(year: int) -> date:
    """Thursday after Labor Day."""
    ld = _labor_day(year)
    # Thursday=3
    return ld + timedelta(days=(3 - ld.weekday()) % 7)

def current_season_week() -> Tuple[int, int]:
    """
    Rough mapping from today's date to (season, week).
    If before March, we treat season as previous year; else current year.
    Week is computed against Week 1 Thursday.
    """
    today = datetime.now(NY).date()
    # Season year heuristic: early-year (Jan/Feb) belongs to prior season.
    season = today.year if today.month >= 3 else today.year - 1

    w1 = _week1_thursday(season)
    if today < w1:
        # We are before the season start; use previous season, week 1
        season -= 1
        w1 = _week1_thursday(season)

    # Compute week index: Thu..Mon as one "week window"
    if today <= w1 + timedelta(days=4):  # Thu..Mon (5 days)
        wk = 1
    else:
        days = (today - w1).days
        # Each "week" we slide 7 days
        wk = 1 + days // 7
        # Cap within 1..18
    if wk < 1:
        wk = 1
    if wk > 18:
        wk = 18
    return season, wk

def week_window(season: int, week: int) -> Tuple[datetime, datetime]:
    """
    Returns NY-aware datetimes bracketing a week as [Thu 00:00:00 .. Mon 23:59:59].
    """
    if week < 1:
        week = 1
    if week > 18:
        week = 18
    w1 = _week1_thursday(season)
    start = w1 + timedelta(days=(week - 1) * 7)
    end = start + timedelta(days=4, hours=23, minutes=59, seconds=59)  # Thu..Mon
    return (
        datetime.combine(start, datetime.min.time(), tzinfo=NY),
        datetime.combine(end, datetime.min.time(), tzinfo=NY),
    )
