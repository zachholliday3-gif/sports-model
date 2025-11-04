from typing import Dict
import hashlib

def _hash_to_range(s: str, lo: float, hi: float) -> float:
    h = int(hashlib.sha256(s.encode()).hexdigest(), 16)
    r = (h % 10_000) / 10_000.0
    return lo + (hi - lo) * r

def project_cbb_1h(home_team: str, away_team: str) -> Dict[str, float]:
    # Placeholder — replace with your real 1H model later
    key = f"{home_team}-{away_team}"
    total = _hash_to_range(key, 64.0, 72.0)
    spread = _hash_to_range(key[::-1], -4.0, 4.0)  # home minus away
    conf = _hash_to_range(key[::2], 0.55, 0.65)
    return {
        "projTotal": round(total, 1),
        "projSpreadHome": round(spread, 1),
        "confidence": round(conf, 3),
    }
