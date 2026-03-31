"""
Route: GET /broker-spreads/live

Returns live spread data from https://www.forexfactory.com/brokers/kenya for the
requested brokers and currency symbols at the moment of the call.

Query parameters:
  broker   (repeatable, required) — broker display names, e.g. broker=HFM&broker=Pepperstone
  symbol   (repeatable, required) — forex pairs,         e.g. symbol=EUR/USD&symbol=USD/JPY

Cache TTL: 5 minutes — broker spreads are live/real-time so we keep them
           fresh but avoid hammering the page on repeated identical requests.

Example:
  GET /broker-spreads/live?broker=HFM&symbol=EUR/USD&symbol=USD/JPY
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from browser_manager import BrowserManager
from cache_manager import CacheManager
from models import BrokerSpreadsResponse, BrokerSpreadEntry
from scrapers.brokerguide import scrape_broker_spreads

router = APIRouter()
logger = logging.getLogger(__name__)

# Spreads are live — short TTL so data stays meaningful
BG_CACHE_TTL = 5 * 60   # 5 minutes


def _bm(request: Request)    -> BrowserManager: return request.app.state.browser_manager
def _cache(request: Request) -> CacheManager:   return request.app.state.cache


def _bg_key(brokers: List[str], symbols: List[str]) -> str:
    """Stable cache key from sorted brokers + symbols."""
    from cache_manager import CacheManager as CM
    return CM.make_key(sorted(b.upper() for b in brokers),
                       sorted(s.upper() for s in symbols))


@router.get("/live", response_model=BrokerSpreadsResponse)
async def broker_spreads_live(
    request: Request,
    broker:  List[str] = Query(..., description="Broker name(s), e.g. HFM"),
    symbol:  List[str] = Query(..., description="Symbol(s), e.g. EUR/USD"),
    bm:    BrowserManager = Depends(_bm),
    cache: CacheManager   = Depends(_cache),
):
    """
    Fetch live broker spread data for the requested broker(s) and symbol(s).

    Brokers are matched by case-insensitive substring against the names on the
    MFxBook broker guide page — so "HFM", "hfm", and "HF Markets" all work.

    Symbols accept slash or no-slash: "EUR/USD" and "EURUSD" both resolve to
    the `eurusd` column on the page.
    """
    brokers_req = [b.strip() for b in broker if b.strip()]
    symbols_req = [s.strip() for s in symbol if s.strip()]

    if not brokers_req:
        raise HTTPException(status_code=422, detail="At least one broker= is required.")
    if not symbols_req:
        raise HTTPException(status_code=422, detail="At least one symbol= is required.")

    cache_key = _bg_key(brokers_req, symbols_req)

    # ── Cache check ───────────────────────────────────────────────────────────
    hit = cache.get(cache_key, BG_CACHE_TTL)
    if hit is not None:
        logger.debug("BG cache HIT | brokers=%s symbols=%s", brokers_req, symbols_req)
        return BrokerSpreadsResponse(
            brokers = [BrokerSpreadEntry(**e) for e in hit["entries"]],
            symbols = hit["symbols"],
            source  = "myfxbook-broker-spreads",
            cached  = True,
        )

    logger.debug("BG cache MISS | brokers=%s symbols=%s", brokers_req, symbols_req)

    # ── Scrape ────────────────────────────────────────────────────────────────
    async with bm.bg_lock:
        # Double-check inside lock
        hit = cache.get(cache_key, BG_CACHE_TTL)
        if hit is not None:
            return BrokerSpreadsResponse(
                brokers = [BrokerSpreadEntry(**e) for e in hit["entries"]],
                symbols = hit["symbols"],
                source  = "myfxbook-broker-spreads",
                cached  = True,
            )

        try:
            entries, scraped_at = await scrape_broker_spreads(
                bm.bg_page, brokers_req, symbols_req
            )
        except Exception as exc:
            logger.error("BG scrape failed: %s", exc)
            raise HTTPException(
                status_code=500,
                detail={"error": "SCRAPING_FAILED", "details": str(exc)},
            )

    # ── Cache and respond ─────────────────────────────────────────────────────
    payload = {
        "entries": [e.model_dump() for e in entries],
        "symbols": symbols_req,
    }
    cache.set(cache_key, payload)
    logger.info(
        "BG scraped %d broker(s) × %d symbol(s) at %s",
        len(entries), len(symbols_req), scraped_at,
    )

    return BrokerSpreadsResponse(
        brokers = entries,
        symbols = symbols_req,
        source  = "myfxbook-broker-spreads",
        cached  = False,
    )