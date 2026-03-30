import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from browser_manager import BrowserManager
from cache_manager import CacheManager
from models import MyFxBookResponse, MyFxBookEvent
from scrapers.myfxbook import scrape_myfxbook

router = APIRouter()
logger = logging.getLogger(__name__)

MFB_CACHE_TTL = 24 * 60 * 60   # 24 hours


def _bm(request: Request)    -> BrowserManager: return request.app.state.browser_manager
def _cache(request: Request) -> CacheManager:   return request.app.state.cache


def _mfb_key(currency: str, start: str, end: str) -> str:
    return f"mfb:{currency.upper()}:{start}:{end}"

def _mfb_tz_key(start: str, end: str) -> str:
    """Separate cache key for the timezone MFB reported for a date range."""
    return f"mfb:tz:{start}:{end}"


@router.get("/events", response_model=MyFxBookResponse)
async def mfb_events(
    request:    Request,
    currency:   List[str] = Query(...),
    start_date: str        = Query(...),
    end_date:   str        = Query(...),
    bm:    BrowserManager = Depends(_bm),
    cache: CacheManager   = Depends(_cache),
):
    requested = sorted(c.upper() for c in currency)

    # ── 1. Check cache per currency ──────────────────────────────────────────
    cached_events: List[dict] = []
    missing_currs: List[str]  = []

    for cur in requested:
        hit = cache.get(_mfb_key(cur, start_date, end_date), MFB_CACHE_TTL)
        if hit is not None:
            cached_events.extend(hit)
            logger.debug("MFB cache HIT  | %s %s→%s", cur, start_date, end_date)
        else:
            missing_currs.append(cur)
            logger.debug("MFB cache MISS | %s %s→%s", cur, start_date, end_date)

    # ── 2. All cached ────────────────────────────────────────────────────────
    if not missing_currs:
        cached_tz = cache.get(_mfb_tz_key(start_date, end_date), MFB_CACHE_TTL) or "UTC"
        return MyFxBookResponse(
            start_date = start_date,
            end_date   = end_date,
            timezone   = cached_tz,
            events     = [MyFxBookEvent(**e) for e in cached_events],
            source     = "myfxbook",
            cached     = True,
        )

    # ── 3. Scrape missing currencies ─────────────────────────────────────────
    async with bm.mfb_lock:
        still_missing = [
            cur for cur in missing_currs
            if cache.get(_mfb_key(cur, start_date, end_date), MFB_CACHE_TTL) is None
        ]

        scraped_tz = "UTC"

        if still_missing:
            try:
                # scrape_myfxbook now returns (events, timezone)
                all_events, scraped_tz = await scrape_myfxbook(
                    bm.mfb_page, [], start_date, end_date
                )
            except Exception as exc:
                logger.error("MFB scrape failed: %s", exc)
                raise HTTPException(
                    status_code=500,
                    detail={"error": "SCRAPING_FAILED", "details": str(exc)},
                )

            # Cache the timezone
            cache.set(_mfb_tz_key(start_date, end_date), scraped_tz)

            # Cache events by currency
            by_currency: dict[str, List[dict]] = {}
            for ev in all_events:
                by_currency.setdefault(ev.currency, []).append(ev.model_dump())

            for cur, evs in by_currency.items():
                cache.set(_mfb_key(cur, start_date, end_date), evs)
                logger.debug("MFB cache SET  | %s (%d events)", cur, len(evs))

            for cur in still_missing:
                if cur not in by_currency:
                    cache.set(_mfb_key(cur, start_date, end_date), [])

    # ── 4. Assemble response ─────────────────────────────────────────────────
    final_events: List[MyFxBookEvent] = []
    for cur in requested:
        evs = cache.get(_mfb_key(cur, start_date, end_date), MFB_CACHE_TTL) or []
        final_events.extend(MyFxBookEvent(**e) for e in evs)

    timezone = cache.get(_mfb_tz_key(start_date, end_date), MFB_CACHE_TTL) or scraped_tz

    return MyFxBookResponse(
        start_date = start_date,
        end_date   = end_date,
        timezone   = timezone,
        events     = final_events,
        source     = "myfxbook",
        cached     = not bool(still_missing),
    )