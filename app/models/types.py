from typing import TypedDict, Literal, Optional

class GameLite(TypedDict):
    gameId: str
    date: str
    status: str
    homeTeam: str
    awayTeam: str

class Projection(TypedDict):
    gameId: str
    scope: Literal["1H","FG"]
    projTotal: float
    projSpreadHome: float
    confidence: float

class MatchupDetail(TypedDict):
    gameId: str
    date: str
    status: str
    homeTeam: str
    awayTeam: str
    venue: Optional[str]
    notes: Optional[str]
    model: Projection
