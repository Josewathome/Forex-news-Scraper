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


# ── Broker spread models ──────────────────────────────────────────────────────

class BrokerSymbolSpread(BaseModel):
    """Live spread data for one broker × one symbol at time of request."""
    symbol:     str             # e.g. "EUR/USD"
    spread:     Optional[str]   # e.g. "0.4"  (pips, as shown on site)
    commission: Optional[str]   # e.g. "+$3"  (per lot, empty = no commission)
    quality:    Optional[str]   # "good" | "bad" | None  (MFB relative ranking)


class BrokerSpreadEntry(BaseModel):
    """All requested symbols for one named broker."""
    broker:    str                      # e.g. "HFM"
    symbols:   List[BrokerSymbolSpread]
    scraped_at: str                     # ISO timestamp of when data was fetched


class BrokerSpreadsResponse(BaseModel):
    brokers:   List[BrokerSpreadEntry]
    symbols:   List[str]                # normalised list of symbols requested
    source:    str  = "myfxbook-broker-spreads"
    cached:    bool = False