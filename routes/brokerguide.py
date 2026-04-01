# routes/brokerguide.py
#
# CHANGES FROM ORIGINAL
# ───────────────────────────────────────────────────────────────────────────────
# • source field corrected: "myfxbook-broker-spreads" → "forexfactory-broker-spreads"
#   (data comes from forexfactory.com/brokers/kenya, not myfxbook)
# • Added /all endpoint — returns every broker on the page without filtering
# • HTTPException detail normalised to always include an "error" key
# ───────────────────────────────────────────────────────────────────────────────

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from browser_manager import BrowserManager
from cache_manager import CacheManager
from models import BrokerSpreadsResponse, BrokerSpreadEntry
from scrapers.brokerguide import scrape_broker_spreads, scrape_all_brokers

router = APIRouter()
logger = logging.getLogger(__name__)

# Spreads are live — short TTL so data stays fresh
BG_CACHE_TTL = 5 * 60  # 5 minutes

# ── Dependency helpers ────────────────────────────────────────────────────────

def _bm(request: Request) -> BrowserManager:
    return request.app.state.browser_manager


def _cache(request: Request) -> CacheManager:
    return request.app.state.cache


def _bg_key(*parts) -> str:
    from cache_manager import CacheManager as CM
    return CM.make_key(*parts)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/live", response_model=BrokerSpreadsResponse)
async def broker_spreads_live(
    request: Request,
    broker:  List[str] = Query(..., description="Broker name(s) — e.g. HFM, IC Markets"),
    symbol:  List[str] = Query(..., description="Symbol(s) — e.g. EUR/USD, USDJPY"),
    bm:    BrowserManager = Depends(_bm),
    cache: CacheManager   = Depends(_cache),
):
    """
    Fetch live spread data for the requested broker(s) and symbol(s).

    Broker names are matched case-insensitively by substring against the names
    on the ForexFactory Kenya broker guide page — so "HFM", "hfm", and
    "HF Markets" all resolve to the same row.

    Symbols accept slash or no-slash: "EUR/USD" and "EURUSD" both resolve to
    the `eurusd` data-column.
    """
    brokers_req = [b.strip() for b in broker if b.strip()]
    symbols_req = [s.strip() for s in symbol if s.strip()]

    if not brokers_req:
        raise HTTPException(
            status_code=422,
            detail={"error": "MISSING_PARAM", "message": "At least one broker= is required."},
        )
    if not symbols_req:
        raise HTTPException(
            status_code=422,
            detail={"error": "MISSING_PARAM", "message": "At least one symbol= is required."},
        )

    cache_key = _bg_key(
        sorted(b.upper() for b in brokers_req),
        sorted(s.upper() for s in symbols_req),
    )

    # ── Cache check ───────────────────────────────────────────────────────────
    hit = cache.get(cache_key, BG_CACHE_TTL)
    if hit is not None:
        logger.debug("BG cache HIT | brokers=%s symbols=%s", brokers_req, symbols_req)
        return BrokerSpreadsResponse(
            brokers = [BrokerSpreadEntry(**e) for e in hit["entries"]],
            symbols = hit["symbols"],
            source  = "forexfactory-broker-spreads",
            cached  = True,
        )

    logger.debug("BG cache MISS | brokers=%s symbols=%s", brokers_req, symbols_req)

    # ── Scrape (serialised via lock so we don't double-navigate the page) ─────
    async with bm.bg_lock:
        # Double-check after acquiring lock
        hit = cache.get(cache_key, BG_CACHE_TTL)
        if hit is not None:
            return BrokerSpreadsResponse(
                brokers = [BrokerSpreadEntry(**e) for e in hit["entries"]],
                symbols = hit["symbols"],
                source  = "forexfactory-broker-spreads",
                cached  = True,
            )

        try:
            entries, scraped_at = await scrape_broker_spreads(
                bm.bg_page, brokers_req, symbols_req
            )
        except Exception as exc:
            logger.error("BG scrape failed: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"error": "SCRAPING_FAILED", "message": str(exc)},
            )

    # ── Store and return ──────────────────────────────────────────────────────
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
        source  = "forexfactory-broker-spreads",
        cached  = False,
    )


@router.get("/all", response_model=BrokerSpreadsResponse)
async def broker_spreads_all(
    request: Request,
    symbol:  List[str] = Query(..., description="Symbol(s) — e.g. EUR/USD, USDJPY"),
    bm:    BrowserManager = Depends(_bm),
    cache: CacheManager   = Depends(_cache),
):
    """
    Return spread data for **every** broker listed on the page for the
    requested symbol(s).  Useful for comparison tables.
    """
    symbols_req = [s.strip() for s in symbol if s.strip()]

    if not symbols_req:
        raise HTTPException(
            status_code=422,
            detail={"error": "MISSING_PARAM", "message": "At least one symbol= is required."},
        )

    cache_key = _bg_key("ALL", sorted(s.upper() for s in symbols_req))

    hit = cache.get(cache_key, BG_CACHE_TTL)
    if hit is not None:
        return BrokerSpreadsResponse(
            brokers = [BrokerSpreadEntry(**e) for e in hit["entries"]],
            symbols = hit["symbols"],
            source  = "forexfactory-broker-spreads",
            cached  = True,
        )

    async with bm.bg_lock:
        hit = cache.get(cache_key, BG_CACHE_TTL)
        if hit is not None:
            return BrokerSpreadsResponse(
                brokers = [BrokerSpreadEntry(**e) for e in hit["entries"]],
                symbols = hit["symbols"],
                source  = "forexfactory-broker-spreads",
                cached  = True,
            )

        try:
            entries, scraped_at = await scrape_all_brokers(bm.bg_page, symbols_req)
        except Exception as exc:
            logger.error("BG /all scrape failed: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"error": "SCRAPING_FAILED", "message": str(exc)},
            )

    payload = {
        "entries": [e.model_dump() for e in entries],
        "symbols": symbols_req,
    }
    cache.set(cache_key, payload)
    logger.info(
        "BG /all scraped %d broker(s) × %d symbol(s) at %s",
        len(entries), len(symbols_req), scraped_at,
    )

    return BrokerSpreadsResponse(
        brokers = entries,
        symbols = symbols_req,
        source  = "forexfactory-broker-spreads",
        cached  = False,
    )