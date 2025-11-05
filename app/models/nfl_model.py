# app/models/nfl_model.py
import hashlib, random

def _seed(home: str, away: str) -> int:
    h = hashlib.sha256((home + "|" + away).encode()).hexdigest()
    return int(h[:8], 16)

def project_nfl_fg(home_team: str, away_team: str) -> dict:
    rnd = random.Random(_seed(home_team, away_team))
    # FG total ~ 39..53; spread ~ +/- 0..9; winProb via logistic on spread
    total = round(39 + rnd.random() * 14, 1)
    spread = round((rnd.random() - 0.5) * 18, 1)  # home positive = home favored
    # convert spread to win prob (very rough)
    wp_home = 1 / (1 + pow(10, -spread / 6.0))
    conf = round(0.55 + 0.35 * abs(spread) / 9.0, 3)
    return {
        "projTotal": total,
        "projSpreadHome": spread,
        "winProbHome": round(wp_home, 3),
        "confidence": conf,
    }
