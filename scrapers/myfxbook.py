"""
MyFxBook scraper — reads the active timezone from the page's own UI, then
attaches it to every event so callers never have to guess.

Timezone strategy
-----------------
MFB renders a timezone dropdown in the calendar header:

    <select id="calendarTimezoneSelect">
      <option value="America/New_York" selected>Eastern Time (GMT-4:00)</option>
      ...
    </select>

We read BOTH the `value` attribute (IANA tz name, most useful) AND the visible
label (human-readable, good as a fallback display string).

This is done once per scrape, before any row parsing, so every
MyFxBookEvent gets the timezone that was actually shown on the page.
"""

import asyncio
import logging
import random
import re
from datetime import datetime, timedelta
from typing import List, Tuple

from playwright.async_api import Page, TimeoutError as PWTimeout

from models import MyFxBookEvent

logger = logging.getLogger(__name__)

MAX_RETRIES  = 2
MFB_URL      = "https://www.myfxbook.com/forex-economic-calendar"
API_ENDPOINT = "https://www.myfxbook.com/calendarEmailMinAlert.xml"

FALLBACK_TZ  = "UTC"   # only used if the page gives us absolutely nothing


# ── Public entry point ────────────────────────────────────────────────────────

async def scrape_myfxbook(
    page: Page,
    currencies: List[str],
    start_date: str,
    end_date:   str,
) -> Tuple[List[MyFxBookEvent], str]:
    """
    Returns (events, timezone_string).
    timezone_string is whatever the MFB page's dropdown says — not a guess.
    """
    cur_set = {c.upper() for c in currencies} if currencies else set()

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end   = datetime.strptime(end_date,   "%Y-%m-%d")

    logger.info(
        "MFB scrape | currencies=%s | %s → %s",
        sorted(cur_set), start_date, end_date,
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await _scrape(page, cur_set, start, end)
        except PWTimeout as exc:
            logger.warning("MFB timeout attempt %d/%d: %s", attempt, MAX_RETRIES, exc)
            if attempt == MAX_RETRIES:
                raise
            await asyncio.sleep(2)
        except Exception as exc:
            logger.error("MFB error attempt %d/%d: %s", attempt, MAX_RETRIES, exc, exc_info=True)
            if attempt == MAX_RETRIES:
                raise


# ── Core ──────────────────────────────────────────────────────────────────────

async def _scrape(
    page: Page,
    cur_set: set,
    start: datetime,
    end:   datetime,
) -> Tuple[List[MyFxBookEvent], str]:

    # ── Step 1: Read the active timezone from the page before touching rows ──
    timezone = await _get_page_timezone(page)
    logger.info("MFB timezone from page: %r", timezone)

    # ── Step 2: Get CSRF token ───────────────────────────────────────────────
    csrf = await _get_csrf(page)
    if not csrf:
        # Reload once to re-establish session
        await page.goto(MFB_URL, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_selector("#economicCalendarTable", timeout=30_000)
        csrf = await _get_csrf(page)

    if not csrf:
        raise RuntimeError("Could not obtain CSRF token from MyFxBook")

    # ── Step 3: Fetch and parse one day at a time ────────────────────────────
    all_events: List[MyFxBookEvent] = []
    current = start

    while current <= end:
        day_str = current.strftime("%Y-%m-%d")
        html = await _fetch_day(page, csrf, day_str, day_str)

        if html:
            events = await _parse_html_fragment(
                page, html, cur_set, current, current, timezone
            )
            all_events.extend(events)
            logger.debug("MFB %s: %d events (tz=%s)", day_str, len(events), timezone)
        else:
            logger.warning("MFB %s: empty response", day_str)

        current += timedelta(days=1)

    logger.info("MFB total events: %d (tz=%s)", len(all_events), timezone)
    return all_events, timezone


# ── Timezone extraction ───────────────────────────────────────────────────────

async def _get_page_timezone(page: Page) -> str:
    """
    Read the timezone the MFB page is actually using from its own DOM.

    MFB renders one of these patterns (inspect the live page to confirm):
      <select id="calendarTimezoneSelect">
        <option value="America/New_York" selected>Eastern Time (GMT-4:00)</option>
      </select>

    We return the `value` (IANA name) when present; otherwise the visible text.
    If nothing is found we return FALLBACK_TZ so callers still get a valid string.
    """
    result = await page.evaluate("""
        () => {
            // Pattern 1 — <select id="calendarTimezoneSelect"> (most common)
            const sel = document.querySelector('#calendarTimezoneSelect');
            if (sel) {
                const opt = sel.options[sel.selectedIndex];
                if (opt) {
                    return {
                        iana:  opt.value || null,     // "America/New_York"
                        label: opt.textContent.trim() // "Eastern Time (GMT-4:00)"
                    };
                }
            }

            // Pattern 2 — any <select> whose id/class mentions "timezone"
            const anySelect = document.querySelector(
                'select[id*="imezone"], select[class*="imezone"]'
            );
            if (anySelect) {
                const opt = anySelect.options[anySelect.selectedIndex];
                if (opt) return { iana: opt.value || null, label: opt.textContent.trim() };
            }

            // Pattern 3 — plain text label (non-interactive display)
            const label = document.querySelector(
                '.calendar-timezone, .calendarTimezone, [class*="timezone"]'
            );
            if (label) return { iana: null, label: label.textContent.trim() };

            return { iana: null, label: null };
        }
    """)

    iana  = (result or {}).get("iana")  or ""
    label = (result or {}).get("label") or ""

    # Prefer IANA name (e.g. "America/New_York") — most precise
    if iana and "/" in iana:
        return iana

    # Otherwise return the display label ("Eastern Time (GMT-4:00)") — still useful
    if label:
        return label

    logger.warning("MFB: could not detect timezone from DOM — defaulting to %s", FALLBACK_TZ)
    return FALLBACK_TZ


# ── API call ──────────────────────────────────────────────────────────────────

async def _fetch_day(
    page: Page,
    csrf: str,
    start: str,
    end:   str,
) -> str | None:
    url = (
        f"{API_ENDPOINT}"
        f"?min=&start={start}&end={end}"
        f"&show=show&type=cal&bubble=false&calPeriod=-1&tabType=0"
    )
    post_body = f"_csrf={csrf}&z={random.random()}"

    logger.debug("POST %s  body=%s", url, post_body)

    result = await page.evaluate(
        """
        async ([url, body]) => {
            try {
                const resp = await fetch(url, {
                    method:      'POST',
                    credentials: 'include',
                    headers: {
                        'Content-Type':     'application/x-www-form-urlencoded; charset=UTF-8',
                        'X-Requested-With': 'XMLHttpRequest',
                        'Accept':           '*/*',
                    },
                    body: body,
                });
                if (!resp.ok) return { error: resp.status, body: '' };
                const text = await resp.text();
                return { error: null, body: text };
            } catch (e) {
                return { error: String(e), body: '' };
            }
        }
        """,
        [url, post_body]
    )

    if result.get("error"):
        logger.error("API call failed: %s", result["error"])
        return None

    xml_body = result["body"]
    logger.debug("API returned %d chars", len(xml_body))

    match = re.search(r"<!\[CDATA\[(.*?)]]>", xml_body, re.DOTALL)
    if match:
        return match.group(1)

    if "economicCalendarRow" in xml_body:
        return xml_body

    logger.warning("No CDATA block found in response. Response head: %s", xml_body[:300])
    return None


# ── HTML parser ───────────────────────────────────────────────────────────────

async def _parse_html_fragment(
    page: Page,
    html: str,
    cur_set: set,
    start: datetime,
    end: datetime,
    timezone: str,         # ← passed in from caller; never guessed here
) -> List[MyFxBookEvent]:
    """
    Inject the API-returned HTML into a hidden <div> on the live page,
    then query its rows with Playwright's normal DOM API.
    """
    escaped = html.replace("\\", "\\\\").replace("`", "\\`")
    await page.evaluate(
        f"""
        () => {{
            let c = document.getElementById('_mfb_tmp_parse');
            if (!c) {{
                c = document.createElement('div');
                c.id = '_mfb_tmp_parse';
                c.style.display = 'none';
                document.body.appendChild(c);
            }}
            c.innerHTML = `{escaped}`;
        }}
        """
    )

    rows = await page.query_selector_all("#_mfb_tmp_parse tr.economicCalendarRow")
    logger.debug("Parsing %d rows from fragment", len(rows))

    events: List[MyFxBookEvent] = []
    stats = dict(accepted=0, skip_date=0, skip_currency=0,
                 skip_parse=0, skip_title=0, skip_tds=0)

    for idx, row in enumerate(rows):
        row_id = await row.get_attribute("data-row-id") or f"idx{idx}"

        # ── date ─────────────────────────────────────────────────────────────
        date_div = await row.query_selector("td .calendarDateTd")
        if not date_div:
            stats["skip_parse"] += 1
            continue

        raw = await date_div.get_attribute("data-calendarDateTd") or ""
        if not raw:
            stats["skip_parse"] += 1
            continue

        try:
            row_dt   = datetime.strptime(raw[:10], "%Y-%m-%d")
            row_date = row_dt.strftime("%Y-%m-%d")
            display  = (await date_div.inner_text()).strip()
            # "Mar 02, 03:00"  →  "03:00"
            time_str = display.split(",")[-1].strip() if "," in display else ""
        except ValueError:
            stats["skip_parse"] += 1
            continue

        if not (start <= row_dt <= end):
            stats["skip_date"] += 1
            continue

        # ── columns ───────────────────────────────────────────────────────────
        tds = await row.query_selector_all("td.calendarToggleCell")
        if len(tds) < 6:
            stats["skip_tds"] += 1
            continue

        currency = (await tds[3].inner_text()).strip().upper()
        if cur_set and currency not in cur_set:
            stats["skip_currency"] += 1
            continue

        title = (await tds[4].inner_text()).strip()
        if not title:
            stats["skip_title"] += 1
            continue

        impact   = _parse_impact(await tds[5].inner_html())
        previous = await _cell_text(row, f"td[data-previous='{row_id}'] span")
        forecast = await _cell_text(row, f"td[data-concensus='{row_id}']")
        actual   = await _cell_text(row, f"td[data-actual='{row_id}'] span")

        events.append(MyFxBookEvent(
            date     = row_date,
            time     = time_str,
            timezone = timezone,          # ← site-reported, not guessed
            currency = currency,
            impact   = impact,
            event    = title,
            actual   = actual   or None,
            forecast = forecast or None,
            previous = previous or None,
        ))
        stats["accepted"] += 1

    logger.info("Fragment parse | %s", stats)

    await page.evaluate("""
        () => {
            const c = document.getElementById('_mfb_tmp_parse');
            if (c) c.innerHTML = '';
        }
    """)

    return events


# ── CSRF extraction ───────────────────────────────────────────────────────────

async def _get_csrf(page: Page) -> str | None:
    token = await page.evaluate("""
        () => {
            const match = document.cookie.match(/(?:^|;\\s*)(?:XSRF-TOKEN|_csrf)=([^;]+)/);
            if (match) return decodeURIComponent(match[1]);

            const inputs = [...document.querySelectorAll('input[name="_csrf"]')];
            if (inputs.length) return inputs[0].value;

            const meta = document.querySelector('meta[name="_csrf"]');
            if (meta) return meta.getAttribute('content');

            return null;
        }
    """)

    if token:
        return token

    cookies = await page.context.cookies("https://www.myfxbook.com")
    for c in cookies:
        if c["name"].upper() in ("XSRF-TOKEN", "_CSRF", "CSRF-TOKEN"):
            logger.debug("CSRF from cookie jar: %s=%s", c["name"], c["value"][:12])
            return c["value"]

    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_impact(html: str) -> str:
    h = html.lower()
    if "impact_high"   in h: return "high"
    if "impact_medium" in h: return "medium"
    if "impact_low"    in h: return "low"
    return "unknown"


async def _cell_text(row, selector: str) -> str:
    el = await row.query_selector(selector)
    return (await el.inner_text()).strip() if el else ""