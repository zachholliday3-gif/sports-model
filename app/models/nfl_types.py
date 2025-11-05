# app/models/nfl_types.py
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
    scope: Literal["FG"]
    projTotal: float
    projSpreadHome: float
    winProbHome: float
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
