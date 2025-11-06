# app/services/odds_api_nfl_props.py
from __future__ import annotations

import os
import asyncio
from typing import Any, Dict, List, Tuple

import httpx

ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# Odds API markets we’ll try to pull; map -> our normalized stat keys
MARKET_MAP = {
    "player_pass_yds": "passYds",
    "player_pass_tds": "passTDs",
    "player_rush_yds": "rushYds",
    "player_rec_yds": "recYds",
    "player_receptions": "receptions",
}

ODDS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())

async def _get_json(client: httpx.AsyncClient, params: Dict[str, Any]):
    r = await client.get(ODDS_URL, params=params)
    r.raise_for_status()
    return r.json()

async def get_nfl_player_prop_lines(
    season: int,
    week: int,
    want_stats: List[str] | None = None,
    region: str = "us",
    bookmakers: List[str] | None = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Returns: { "playerToken|TEAM|OPP": { team, opponent, position:None, book, markets:{stat: line} } }
    We aggregate best-available line per stat across allowed books.
    """
    if not ODDS_API_KEY:
        return {}

    # Filter requested stats to supported keys
    want_stats = want_stats or list(MARKET_MAP.values())
    rev = {v: k for k, v in MARKET_MAP.items()}
    requested_markets = [rev[s] for s in want_stats if s in rev]

    params = {
        "apiKey": ODDS_API_KEY,
        "regions": region,
        "markets": ",".join(requested_markets),
        "oddsFormat": "american",
        "bookmakers": ",".join(bookmakers) if bookmakers else None,
        # HACK: The Odds API doesn't filter by NFL week directly. We’ll fetch all and filter by matchup tokens.
    }

    out: Dict[str, Dict[str, Any]] = {}

    async with httpx.AsyncClient(timeout=12.0, headers=HEADERS) as client:
        data = await _get_json(client, {k: v for k, v in params.items() if v})
        # Shape: list of games; each has 'home_team','away_team','bookmakers' -> markets -> outcomes (player, line)
        for game in data or []:
            home = game.get("home_team") or ""
            away = game.get("away_team") or ""
            token_game = f"{_norm(away)}|{_norm(home)}"

            for bk in game.get("bookmakers") or []:
                book = bk.get("title") or bk.get("key")
                for m in bk.get("markets") or []:
                    market_key = m.get("key")
                    stat = MARKET_MAP.get(market_key)
                    if not stat:
                        continue
                    for outcome in m.get("outcomes") or []:
                        player = outcome.get("description") or ""
                        # The Odds API gives 'price' and sometimes 'point' for line
                        line = outcome.get("point")
                        if line is None:
                            continue
                        # Try to infer team from player name in the label (not always present); fall back to game teams
                        # We’ll just assign to both sides via markets; callers combine with our schedule to refine.
                        # Build two entries (player on home or away). GPT can still use them.
                        for team_name, opp_name in [(home, away), (away, home)]:
                            pkey = f"{_norm(player)}|{_norm(team_name)}|{_norm(opp_name)}"
                            entry = out.setdefault(pkey, {
                                "player": player,
                                "team": team_name,
                                "opponent": opp_name,
                                "position": None,
                                "book": book,
                                "markets": {},
                                "gameToken": token_game,
                            })
                            # keep the most "juicy" (largest magnitude) line seen for this stat
                            prev = entry["markets"].get(stat)
                            if prev is None or abs(float(line)) > abs(float(prev)):
                                entry["markets"][stat] = float(line)
                                entry["book"] = book
    return out
