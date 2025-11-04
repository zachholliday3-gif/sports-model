import httpx
from typing import Any, Dict, List
from datetime import date

ESPN_SCOREBOARD = "https://sports.core.api.espn.com/v2/sports/basketball/mens-college-basketball/scoreboard"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

async def fetch_json(url: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0, headers=HEADERS) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()

async def get_scoreboard(yyyymmdd: str | None = None) -> Dict[str, Any]:
    q = yyyymmdd or date.today().strftime("%Y%m%d")
    return await fetch_json(f"{ESPN_SCOREBOARD}?dates={q}")

async def get_games_for_date(yyyymmdd: str | None = None) -> List[Dict[str, Any]]:
    sb = await get_scoreboard(yyyymmdd)
    events = sb.get("events", [])
    games: List[Dict[str, Any]] = []
    for ref in events:
        ev = await fetch_json(ref["$ref"])
        games.append(ev)
    return games

def extract_game_lite(ev: Dict[str, Any]) -> Dict[str, Any]:
    comp = ev["competitions"][0]
    teams = comp["competitors"]
    home = next(t for t in teams if t["homeAway"] == "home")
    away = next(t for t in teams if t["homeAway"] == "away")
    return {
        "gameId": ev["id"],
        "date": ev["date"],
        "status": comp["status"]["type"]["name"],
        "homeTeam": home["team"]["displayName"],
        "awayTeam": away["team"]["displayName"],
    }

def extract_matchup_detail(ev: Dict[str, Any]) -> Dict[str, Any]:
    comp = ev["competitions"][0]
    teams = comp["competitors"]
    home = next(t for t in teams if t["homeAway"] == "home")
    away = next(t for t in teams if t["homeAway"] == "away")
    venue = comp.get("venue", {}).get("fullName")
    return {
        "gameId": ev["id"],
        "date": ev["date"],
        "status": comp["status"]["type"]["name"],
        "homeTeam": home["team"]["displayName"],
        "awayTeam": away["team"]["displayName"],
        "venue": venue,
    }
