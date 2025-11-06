# app/models/nfl_props_model.py
from __future__ import annotations

from typing import Dict

# Simple baselines per position/stat. These get adjusted by game total & spread.
BASELINES = {
    "QB": {"passYds": 225.0, "passTDs": 1.5, "rushYds": 15.0},
    "RB": {"rushYds": 55.0, "recYds": 15.0, "receptions": 2.0},
    "WR": {"recYds": 55.0, "receptions": 4.0},
    "TE": {"recYds": 35.0, "receptions": 3.0},
}

# League-average FG total we use as a pace/volume anchor
LEAGUE_AVG_TOTAL = 43.0

def _pos_from_name(name: str) -> str:
    """Ultra-light guess if position missing in odds payloads."""
    n = name.lower()
    if any(k in n for k in ["qb ", "qb-", "(qb)"]): return "QB"
    if any(k in n for k in [" rb ", "rb-", "(rb)"]): return "RB"
    if any(k in n for k in [" wr ", "wr-", "(wr)"]): return "WR"
    if any(k in n for k in [" te ", "te-", "(te)"]): return "TE"
    # default to WR-like receiving profile
    return "WR"

def _adj_factor(game_total: float | None, team_spread: float | None) -> float:
    """
    Scales volume by (game_total - league_avg).
    Spread gives a mild bias toward pass (as underdog) or run (as favorite).
    """
    if not isinstance(game_total, (int, float)):
        game_total = LEAGUE_AVG_TOTAL
    if not isinstance(team_spread, (int, float)):
        team_spread = 0.0

    pace = 1.0 + (game_total - LEAGUE_AVG_TOTAL) * 0.01            # +/-1% per point off 43
    bias = 1.0 + (-team_spread) * 0.005                             # underdog (negative spread) -> +pass/rec
    return max(0.85, min(pace * bias, 1.15))                        # clamp to keep sane

def project_player_props(
    player_name: str,
    position: str | None,
    team: str,
    opponent: str,
    game_total: float | None,
    team_spread_home: float | None,
) -> Dict[str, float]:
    """
    Returns a dict of projections for supported stats. Very-simple, explainable heuristic:
    baseline(position, stat) * adj_factor(total, spread).
    """
    pos = (position or "").upper() or _pos_from_name(player_name)
    bases = BASELINES.get(pos, BASELINES["WR"])
    adj = _adj_factor(game_total, team_spread_home)

    proj: Dict[str, float] = {}
    for stat, base in bases.items():
        val = round(base * adj, 1)
        # Touchdowns benefit a tad more from pace
        if stat == "passTDs":
            val = round(base * (0.9 + (game_total or LEAGUE_AVG_TOTAL) / 50.0), 2)
        proj[stat] = val

    return proj
