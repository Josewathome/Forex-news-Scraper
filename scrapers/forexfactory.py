"""
ForexFactory scraper — reads window.calendarComponentStates[1] from the page.

Timezone strategy
-----------------
FF embeds the active timezone in the same JS state object as the events:

    window.calendarComponentStates[1].timezone
        → e.g. "America/New_York"  (IANA name)

    window.calendarComponentStates[1].timezoneOffsetStr
        → e.g. "GMT-4:00"          (display label)

We read the IANA name first (most reliable), fall back to the offset string,
and finally fall back to a DOM element FF renders in the calendar header
(.calendar__timezone-select option:checked).  We NEVER hardcode "ET".
"""

import asyncio
import logging
from datetime import datetime
from typing import List, Tuple

from playwright.async_api import Page, TimeoutError as PWTimeout

from models import ForexFactoryEvent

logger = logging.getLogger(__name__)

FF_URL      = "https://www.forexfactory.com/calendar"
MAX_RETRIES = 2

FALLBACK_TZ = "UTC"          # only used if the page gives us absolutely nothing


# ── Public entry point ────────────────────────────────────────────────────────

async def scrape_forexfactory(
    page: Page,
    currencies: List[str],
    date: str,
) -> Tuple[List[ForexFactoryEvent], str]:
    """
    Returns (events, timezone_string).
    timezone_string is whatever the FF page says — not a guess.
    """
    target_date = datetime.strptime(date, "%Y-%m-%d")
    cur_set     = {c.upper() for c in currencies}

    logger.info("FF scrape | currencies=%s | date=%s", sorted(cur_set), date)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await _load_and_parse(page, cur_set, target_date)
        except PWTimeout as exc:
            logger.warning("FF timeout attempt %d/%d: %s", attempt, MAX_RETRIES, exc)
            if attempt == MAX_RETRIES:
                raise
            await asyncio.sleep(2)
        except Exception as exc:
            logger.error("FF error attempt %d/%d: %s", attempt, MAX_RETRIES, exc, exc_info=True)
            if attempt == MAX_RETRIES:
                raise


# ── Core ──────────────────────────────────────────────────────────────────────

async def _load_and_parse(
    page: Page,
    cur_set: set,
    target_date: datetime,
) -> Tuple[List[ForexFactoryEvent], str]:

    result = await page.evaluate("""
        () => {
            try {
                const state = window.calendarComponentStates &&
                              window.calendarComponentStates[1];
                if (!state || !state.days)
                    return { error: 'no state', days: null, tz: null, tzLabel: null };

                // ── Timezone: read directly from the state object ──────────
                // FF stores the IANA tz name and a human-readable offset string
                const tz      = state.timezone          || null;   // "America/New_York"
                const tzLabel = state.timezoneOffsetStr || null;   // "GMT-4:00"

                // ── DOM fallback: the timezone <select> the user sees ──────
                // FF renders something like:
                //   <select class="calendar__timezone-select">
                //     <option selected>New York</option>
                //   </select>
                // or a plain label element — try both patterns
                let domTz = null;
                const selectOpt = document.querySelector(
                    '.calendar__timezone-select option:checked, ' +
                    '.calendar__timezone-select option[selected]'
                );
                if (selectOpt) domTz = selectOpt.textContent.trim();

                if (!domTz) {
                    const label = document.querySelector(
                        '.calendar__timezones, .calendar__timezone'
                    );
                    if (label) domTz = label.textContent.trim();
                }

                return { error: null, days: state.days, tz, tzLabel, domTz };
            } catch(e) {
                return { error: String(e), days: null, tz: null, tzLabel: null, domTz: null };
            }
        }
    """)

    if result.get("error") or not result.get("days"):
        logger.warning("FF: calendarComponentStates not in memory (%s), reloading …",
                       result.get("error"))
        await page.goto(FF_URL, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(1_000)
        result = await page.evaluate("""
            () => {
                const state = window.calendarComponentStates &&
                              window.calendarComponentStates[1];
                if (!state || !state.days)
                    return { error: 'still no state', days: null, tz: null, tzLabel: null, domTz: null };

                const tz      = state.timezone          || null;
                const tzLabel = state.timezoneOffsetStr || null;

                let domTz = null;
                const selectOpt = document.querySelector(
                    '.calendar__timezone-select option:checked, ' +
                    '.calendar__timezone-select option[selected]'
                );
                if (selectOpt) domTz = selectOpt.textContent.trim();

                if (!domTz) {
                    const label = document.querySelector(
                        '.calendar__timezones, .calendar__timezone'
                    );
                    if (label) domTz = label.textContent.trim();
                }

                return { error: null, days: state.days, tz, tzLabel, domTz };
            }
        """)

    if result.get("error") or not result.get("days"):
        raise RuntimeError(
            f"calendarComponentStates[1].days not available: {result.get('error')}"
        )

    # ── Pick the best timezone string the site actually gave us ───────────────
    # Priority: IANA name > offset string > DOM label > fallback
    timezone = (
        result.get("tz")       or   # "America/New_York"  ← most useful
        result.get("tzLabel")  or   # "GMT-4:00"
        result.get("domTz")    or   # "New York" (what the user sees)
        FALLBACK_TZ
    )

    logger.info(
        "FF timezone | iana=%r  offsetLabel=%r  dom=%r  → using %r",
        result.get("tz"), result.get("tzLabel"), result.get("domTz"), timezone,
    )

    days = result["days"]
    logger.info("FF: extracted %d day buckets from page JS state", len(days))

    events = _parse(days, cur_set, target_date, timezone)
    return events, timezone


# ── Parser ─────────────────────────────────────────────────────────────────────

def _parse(
    days: list,
    cur_set: set,
    target_date: datetime,
    timezone: str,
) -> List[ForexFactoryEvent]:
    target_str = target_date.strftime("%b %-d, %Y")
    events: List[ForexFactoryEvent] = []

    for day in days:
        for ev in day.get("events", []):
            if ev.get("date") != target_str:
                continue

            currency = (ev.get("currency") or "").strip().upper()
            if not currency or currency == "ALL":
                continue
            if cur_set and currency not in cur_set:
                continue

            events.append(ForexFactoryEvent(
                time     = ev.get("timeLabel") or "",
                timezone = timezone,              # ← site-reported, not guessed
                currency = currency,
                impact   = ev.get("impactName") or "unknown",
                event    = ev.get("name")        or "",
                actual   = ev.get("actual")   or None,
                forecast = ev.get("forecast") or None,
                previous = ev.get("previous") or None,
            ))

    logger.info("FF: %d events matched for %s %s", len(events), cur_set, target_str)
    return events