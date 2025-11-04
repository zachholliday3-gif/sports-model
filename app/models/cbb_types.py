# app/models/cbb_types.py
from typing_extensions import TypedDict, Literal
from typing import Optional

class GameLite(TypedDict):
    gameId: str
    date: str | None
    status: str
    homeTeam: str
    awayTeam: str

class Projection(TypedDict):
    gameId: str
    scope: Literal["1H", "FG"]
    projTotal: float
    projSpreadHome: float
    confidence: float

class MatchupDetail(TypedDict):
    gameId: str
    date: str | None
    status: str
    homeTeam: str
    awayTeam: str
    venue: Optional[str]
    notes: Optional[str]
    model: Projection
