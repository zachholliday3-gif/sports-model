# app/services/espn_common.py

from datetime import datetime
from typing import Dict, Any

# If you have a GameLite Pydantic model, import it and return that instead of a dict:
# from app.schemas.common import GameLite

def extract_game_lite(ev: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a single ESPN scoreboard event into a lightweight game object.
    Adjust keys/structure to match your GameLite schema.
    """
    comp = ev["competitions"][0]
    # ESPN marks competitors as "home"/"away"
    home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
    away = next(c for c in comp["competitors"] if c["homeAway"] == "away")

    return {
        "event_id": ev["id"],
        "start_time": ev["date"],  # or parse to datetime if your schema needs it
        "short_name": ev.get("shortName"),
        "status": comp["status"]["type"]["name"],   # e.g. "pre", "in", "post"
        "home_team_id": int(home["team"]["id"]),
        "home_team_name": home["team"]["displayName"],
        "away_team_id": int(away["team"]["id"]),
        "away_team_name": away["team"]["displayName"],
        "neutral_site": comp.get("neutralSite", False),
    }

    # If you have a GameLite model, do this instead:
    # return GameLite(
    #     event_id=ev["id"],
    #     start_time=ev["date"],
    #     short_name=ev.get("shortName"),
    #     status=comp["status"]["type"]["name"],
    #     home_team_id=int(home["team"]["id"]),
    #     home_team_name=home["team"]["displayName"],
    #     away_team_id=int(away["team"]["id"]),
    #     away_team_name=away["team"]["displayName"],
    #     neutral_site=comp.get("neutralSite", False),
    # )
