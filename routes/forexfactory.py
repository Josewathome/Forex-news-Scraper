import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from browser_manager import BrowserManager
from cache_manager import CacheManager
from models import ForexFactoryResponse, ForexFactoryEvent
from scrapers.forexfactory import scrape_forexfactory

router = APIRouter()
logger = logging.getLogger(__name__)

FF_CACHE_TTL = 30 * 60   # 30 minutes


def _bm(request: Request)    -> BrowserManager: return request.app.state.browser_manager
def _cache(request: Request) -> CacheManager:   return request.app.state.cache


def _ff_key(currency: str, date: str) -> str:
    return f"ff:{currency.upper()}:{date}"

def _ff_tz_key(date: str) -> str:
    """Separate cache key for the timezone reported by FF for a given date."""
    return f"ff:tz:{date}"


@router.get("/events", response_model=ForexFactoryResponse)
async def ff_events(
    request:  Request,
    currency: List[str] = Query(...),
    date:     str        = Query(...),
    bm:    BrowserManager = Depends(_bm),
    cache: CacheManager   = Depends(_cache),
):
    requested = sorted(c.upper() for c in currency)

    # ── 1. Check cache per currency ──────────────────────────────────────────
    cached_events:  List[dict] = []
    missing_currs:  List[str]  = []

    for cur in requested:
        hit = cache.get(_ff_key(cur, date), FF_CACHE_TTL)
        if hit is not None:
            cached_events.extend(hit)
            logger.debug("FF cache HIT  | %s %s", cur, date)
        else:
            missing_currs.append(cur)
            logger.debug("FF cache MISS | %s %s", cur, date)

    # ── 2. All cached — return immediately ───────────────────────────────────
    if not missing_currs:
        # Timezone was cached alongside events
        cached_tz = cache.get(_ff_tz_key(date), FF_CACHE_TTL) or "UTC"
        return ForexFactoryResponse(
            date       = date,
            timezone   = cached_tz,
            currencies = requested,
            events     = [ForexFactoryEvent(**e) for e in cached_events],
            source     = "forexfactory",
            cached     = True,
        )

    # ── 3. Scrape missing currencies ─────────────────────────────────────────
    async with bm.ff_lock:
        still_missing = [
            cur for cur in missing_currs
            if cache.get(_ff_key(cur, date), FF_CACHE_TTL) is None
        ]

        scraped_tz = "UTC"   # will be overwritten by the scraper's real value

        if still_missing:
            try:
                await bm.reload_ff()
                # scrape_forexfactory now returns (events, timezone)
                all_events, scraped_tz = await scrape_forexfactory(
                    bm.ff_page, [], date
                )
            except Exception as exc:
                logger.error("FF scrape failed: %s", exc)
                raise HTTPException(
                    status_code=500,
                    detail={"error": "SCRAPING_FAILED", "details": str(exc)},
                )

            # Cache the timezone so hits don't lose it
            cache.set(_ff_tz_key(date), scraped_tz)

            # Cache events by currency
            by_currency: dict[str, List[dict]] = {}
            for ev in all_events:
                by_currency.setdefault(ev.currency, []).append(ev.model_dump())

            for cur, evs in by_currency.items():
                cache.set(_ff_key(cur, date), evs)
                logger.debug("FF cache SET  | %s %s (%d events)", cur, date, len(evs))

            for cur in still_missing:
                if cur not in by_currency:
                    cache.set(_ff_key(cur, date), [])

    # ── 4. Assemble response ─────────────────────────────────────────────────
    final_events: List[ForexFactoryEvent] = []
    for cur in requested:
        evs = cache.get(_ff_key(cur, date), FF_CACHE_TTL) or []
        final_events.extend(ForexFactoryEvent(**e) for e in evs)

    timezone = cache.get(_ff_tz_key(date), FF_CACHE_TTL) or scraped_tz

    return ForexFactoryResponse(
        date       = date,
        timezone   = timezone,
        currencies = requested,
        events     = final_events,
        source     = "forexfactory",
        cached     = not bool(still_missing),
    )