from pydantic import BaseModel
from typing import List, Optional


class EconEvent(BaseModel):
    time:     str
    timezone: str           # e.g. "America/New_York", "UTC", "Europe/London"
    currency: str
    impact:   str           # "low" | "medium" | "high" | "unknown"
    event:    str
    actual:   Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None


class ForexFactoryEvent(EconEvent):
    pass


class MyFxBookEvent(EconEvent):
    date: str


class ForexFactoryResponse(BaseModel):
    date:      str
    timezone:  str                  # timezone that covers ALL events in this response
    currencies: List[str]
    events:    List[ForexFactoryEvent]
    source:    str  = "forexfactory"
    cached:    bool


class MyFxBookResponse(BaseModel):
    start_date: str
    end_date:   str
    timezone:   str                 # timezone that covers ALL events in this response
    events:     List[MyFxBookEvent]
    source:     str  = "myfxbook"
    cached:     bool = False


class ErrorResponse(BaseModel):
    error:   str
    details: str