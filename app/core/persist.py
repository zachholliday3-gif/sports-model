# app/core/persist.py
from typing import Iterable, Dict, Any
from datetime import datetime, timezone
from app.core.db import exec_sql, exec_many

def _to_ts(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    # ESPN dates are ISO; rely on PG to parse or slice if needed
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

async def upsert_games(rows: Iterable[Dict[str, Any]], sport: str):
    sql = """
    INSERT INTO games (id, sport, date_utc, status, home_team, away_team, venue)
    VALUES (:id, :sport, :date_utc, :status, :home_team, :away_team, :venue)
    ON CONFLICT (id) DO UPDATE SET
      sport = EXCLUDED.sport,
      date_utc = EXCLUDED.date_utc,
      status = EXCLUDED.status,
      home_team = EXCLUDED.home_team,
      away_team = EXCLUDED.away_team,
      venue = EXCLUDED.venue;
    """
    payload = []
    for r in rows:
        payload.append({
            "id": r["gameId"],
            "sport": sport,
            "date_utc": _to_ts(r.get("date")),
            "status": r.get("status") or "STATUS_SCHEDULED",
            "home_team": r["homeTeam"],
            "away_team": r["awayTeam"],
            "venue": r.get("venue"),
        })
    await exec_many(sql, payload)

async def insert_projections(rows: Iterable[Dict[str, Any]], sport: str, scope: str):
    sql = """
    INSERT INTO projections (game_id, sport, scope, proj_total, proj_spread_home, win_prob_home, confidence)
    VALUES (:game_id, :sport, :scope, :proj_total, :proj_spread_home, :win_prob_home, :confidence);
    """
    payload = []
    for r in rows:
        m = r["model"]
        payload.append({
            "game_id": r["gameId"],
            "sport": sport,
            "scope": scope,
            "proj_total": m.get("projTotal"),
            "proj_spread_home": m.get("projSpreadHome"),
            "win_prob_home": m.get("winProbHome"),
            "confidence": m.get("confidence"),
        })
    await exec_many(sql, payload)

async def insert_markets_edges(rows: Iterable[Dict[str, Any]], sport: str, scope: str):
    sql_m = """
    INSERT INTO markets (game_id, sport, scope, book, market_total, market_spread_home)
    VALUES (:game_id, :sport, :scope, :book, :market_total, :market_spread_home);
    """
    sql_e = """
    INSERT INTO edges (game_id, sport, scope, edge_total, edge_spread_home)
    VALUES (:game_id, :sport, :scope, :edge_total, :edge_spread_home);
    """
    m_payload, e_payload = [], []
    for r in rows:
        mk = r.get("market") or {}
        ed = r.get("edge") or {}
        m_payload.append({
            "game_id": r["gameId"],
            "sport": sport,
            "scope": scope,
            "book": mk.get("book"),
            "market_total": mk.get("total"),
            "market_spread_home": mk.get("spreadHome"),
        })
        e_payload.append({
            "game_id": r["gameId"],
            "sport": sport,
            "scope": scope,
            "edge_total": ed.get("total"),
            "edge_spread_home": ed.get("spreadHome"),
        })
    if m_payload:
        await exec_many(sql_m, m_payload)
    if e_payload:
        await exec_many(sql_e, e_payload)

async def ensure_schema():
    # run schema once at startup
    await exec_sql(open("app/core/schema.sql", "r", encoding="utf-8").read())
