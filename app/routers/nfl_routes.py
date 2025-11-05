@router.get("/week")
async def nfl_week_slate(
    season: int,
    week: int,
    include_markets: bool = False,
):
    """
    Returns {season, week, start, end, rows:[...]} built from the computed week window.
    Uses the same row shape as /slate_this_week.
    """
    start, end = week_window(season, week)
    try:
        games = await get_games_for_range(start, end)
    except Exception as e:
        logger.exception("nfl week(%s,%s) fetch failed: %s", season, week, e)
        games = []

    logger.info("NFL week slate: season=%s week=%s include_markets=%s", season, week, include_markets)
    logger.info("NFL games fetched: %d", len(games))

    markets = {}
    if include_markets:
        try:
            markets = await asyncio.wait_for(get_nfl_fg_lines(), timeout=8.0)
            logger.info("NFL markets loaded: %d", len(markets))
        except Exception as e:
            logger.exception("nfl odds fetch failed or timed out: %s", e)
            markets = {}

    def _norm(s: str) -> str:
        return "".join(ch for ch in (s or "").lower() if ch.isalnum())

    rows = []
    for ev in games:
        lite = extract_game_lite(ev)
        m = project_nfl_fg(lite["homeTeam"], lite["awayTeam"])

        token = f"{_norm(lite['awayTeam'])}|{_norm(lite['homeTeam'])}"
        mk = markets.get(token, {}) if include_markets else {}
        mt = mk.get("marketTotal")
        ms = mk.get("marketSpreadHome")

        edge_total = round(m["projTotal"] - mt, 2) if isinstance(mt, (int, float)) else None
        edge_spread = round(m["projSpreadHome"] - ms, 2) if isinstance(ms, (int, float)) else None

        rows.append({
            **lite,
            "model": {"scope": "FG", **m},
            "market": {"total": mt, "spreadHome": ms, "book": mk.get("book")},
            "edge": {"total": edge_total, "spreadHome": edge_spread},
        })

    return {"season": season, "week": week, "start": start, "end": end, "rows": rows}
