# app/services/props_nfl.py
from typing import Dict, Any, List
import hashlib, random

# ---- Replace these with a real data provider later ----
async def fetch_defense_allowed_last5(team_name: str) -> Dict[str, float]:
    """
    Return opponent defense allowed stats (last 5) like:
      {"passYds": 238.4, "rushYds": 112.7, "recYds": 151.3, "passTD": 1.6, "rushTD": 0.8}
    Currently a deterministic stub (seeded by team name).
    """
    rnd = random.Random(int(hashlib.md5(team_name.encode()).hexdigest()[:8], 16))
    return {
        "passYds": round(210 + rnd.random() * 90, 1),
        "rushYds": round(90 + rnd.random() * 70, 1),
        "recYds": round(140 + rnd.random() * 80, 1),
        "passTD": round(1.1 + rnd.random() * 1.2, 2),
        "rushTD": round(0.6 + rnd.random() * 0.9, 2),
    }

def project_player_line(player: str, position: str, opp_def: Dict[str, float]) -> List[Dict[str, Any]]:
    """
    Produce a few props with naive scaling off opponent allowed metrics.
    """
    rnd = random.Random(int(hashlib.sha1((player + position).encode()).hexdigest()[:8], 16))
    out: List[Dict[str, Any]] = []

    if position.upper() in ("QB"):
        line = round(opp_def["passYds"] * (0.95 + 0.1 * rnd.random()), 1)
        out.append({"prop": "Pass Yds", "line": line, "notes": "scaled vs opp pass yards allowed (L5)"})
        tds = round(opp_def["passTD"] * (0.95 + 0.2 * rnd.random()), 2)
        out.append({"prop": "Pass TD", "line": tds, "notes": "scaled vs opp pass TD allowed (L5)"})
    elif position.upper() in ("RB"):
        rush = round(opp_def["rushYds"] * (0.9 + 0.25 * rnd.random()), 1)
        out.append({"prop": "Rush Yds", "line": rush, "notes": "scaled vs opp rush yards allowed (L5)"})
        tds = round(opp_def["rushTD"] * (0.9 + 0.3 * rnd.random()), 2)
        out.append({"prop": "Rush TD", "line": tds, "notes": "scaled vs opp rush TD allowed (L5)"})
        rec = round(opp_def["recYds"] * (0.6 + 0.25 * rnd.random()), 1)
        out.append({"prop": "Rec Yds", "line": rec, "notes": "scaled vs opp receiving yards allowed (L5)"})
    else:  # WR/TE etc.
        rec = round(opp_def["recYds"] * (0.9 + 0.25 * rnd.random()), 1)
        out.append({"prop": "Rec Yds", "line": rec, "notes": "scaled vs opp receiving yards allowed (L5)"})
        tds = round(opp_def["passTD"] * (0.7 + 0.4 * rnd.random()), 2)
        out.append({"prop": "Anytime TD (model rate)", "line": tds, "notes": "scaled vs opp pass TD allowed (L5)"})
    return out
