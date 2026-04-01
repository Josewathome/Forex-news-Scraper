# scrapers/brokerguide.py

import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Tuple

from playwright.async_api import Page, TimeoutError as PWTimeout

from models import BrokerSpreadEntry, BrokerSymbolSpread

logger = logging.getLogger(__name__)

BG_URL      = "https://www.forexfactory.com/brokers/kenya"
MAX_RETRIES = 2

# Confirmed by debug_brokerguide.py section 3
_READY_SELECTOR = "tbody.broker-guide__rows tr"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_symbol(symbol: str) -> str:
    """'EUR/USD' → 'eurusd',  'EURUSD' → 'eurusd'"""
    return symbol.replace("/", "").replace("-", "").strip().lower()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Public entry point ────────────────────────────────────────────────────────

async def scrape_broker_spreads(
    page: Page,
    brokers: List[str],
    symbols: List[str],
) -> Tuple[List[BrokerSpreadEntry], str]:
    col_keys     = [_normalise_symbol(s) for s in symbols]
    broker_names = [b.strip() for b in brokers]

    logger.info("BG scrape | brokers=%s  col_keys=%s", broker_names, col_keys)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await _load_and_parse(page, broker_names, symbols, col_keys)
        except PWTimeout as exc:
            logger.warning("BG timeout attempt %d/%d: %s", attempt, MAX_RETRIES, exc)
            if attempt == MAX_RETRIES:
                raise
            await asyncio.sleep(3)
        except Exception as exc:
            logger.error(
                "BG error attempt %d/%d: %s", attempt, MAX_RETRIES, exc, exc_info=True
            )
            if attempt == MAX_RETRIES:
                raise


async def scrape_all_brokers(
    page: Page,
    symbols: List[str],
) -> Tuple[List[BrokerSpreadEntry], str]:
    col_keys = [_normalise_symbol(s) for s in symbols]
    logger.info("BG scrape_all_brokers | col_keys=%s", col_keys)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            await _navigate_and_wait(page)
            scraped_at = _now_iso()

            broker_names: List[str] = await page.evaluate(
                """
                () => [...document.querySelectorAll(
                    'tbody.broker-guide__rows tr td.broker-guide__field--broker strong'
                )].map(s => s.textContent.trim()).filter(Boolean)
                """
            )

            if not broker_names:
                raise RuntimeError("scrape_all_brokers: no broker names found on page")

            logger.info("BG /all: discovered %d brokers", len(broker_names))
            return await _extract(page, broker_names, symbols, col_keys, scraped_at)

        except PWTimeout as exc:
            logger.warning("BG /all timeout attempt %d/%d: %s", attempt, MAX_RETRIES, exc)
            if attempt == MAX_RETRIES:
                raise
            await asyncio.sleep(3)
        except Exception as exc:
            logger.error("BG /all error attempt %d/%d: %s", attempt, MAX_RETRIES, exc, exc_info=True)
            if attempt == MAX_RETRIES:
                raise


# ── Core ──────────────────────────────────────────────────────────────────────

async def _navigate_and_wait(page: Page) -> None:
    """
    Navigate to the broker guide page and wait until the JS-rendered broker
    rows are visible.

    WHY THIS APPROACH:
    - The page uses domcontentloaded quickly, but broker rows are populated
      by a subsequent XHR/fetch data call (JS-rendered).
    - We first wait for networkidle (catches the data API call completing),
      but with a short timeout because ForexFactory ads fire forever.
    - Then we wait for the actual rows with a generous timeout.
    """
    logger.info("BG: navigating to %s …", BG_URL)
    await page.goto(BG_URL, wait_until="domcontentloaded", timeout=60_000)

    # Wait for network to go quiet — this is when the broker data API call
    # has completed and the JS has had a chance to render the rows.
    # Short timeout (12s) so that persistent ad requests don't block us.
    try:
        await page.wait_for_load_state("networkidle", timeout=12_000)
        logger.debug("BG: networkidle reached")
    except PWTimeout:
        # Ads still firing — that's normal on this site. Proceed anyway.
        logger.debug("BG: networkidle timed out (ads firing) — proceeding")

    # Now wait for the actual broker rows with a full timeout
    await page.wait_for_selector(_READY_SELECTOR, timeout=60_000)
    logger.debug("BG: broker rows visible")


async def _load_and_parse(
    page: Page,
    broker_names: List[str],
    symbols: List[str],
    col_keys: List[str],
) -> Tuple[List[BrokerSpreadEntry], str]:
    await _navigate_and_wait(page)
    scraped_at = _now_iso()
    return await _extract(page, broker_names, symbols, col_keys, scraped_at)


async def _extract(
    page: Page,
    broker_names: List[str],
    symbols: List[str],
    col_keys: List[str],
    scraped_at: str,
) -> Tuple[List[BrokerSpreadEntry], str]:
    """Run the JS extraction against the already-loaded page."""

    result = await page.evaluate(
        """
        (args) => {
            const { brokerNames, colKeys, symbols } = args;

            // All broker <tr>s confirmed in debug section 3:
            // tbody.broker-guide__rows holds rows across 3 tbodies (6, 4, 13 rows)
            const allRows = [
                ...document.querySelectorAll('tbody.broker-guide__rows tr')
            ];

            if (allRows.length === 0) {
                return {
                    error: 'No rows found in tbody.broker-guide__rows',
                    entries: []
                };
            }

            // Build name → row map.
            // Confirmed by debug section 6: td.broker-guide__field--broker strong
            const rowByName = new Map();
            for (const tr of allRows) {
                const strong = tr.querySelector(
                    'td.broker-guide__field--broker strong'
                );
                if (!strong) continue;
                const name = strong.textContent.trim();
                if (name) rowByName.set(name.toLowerCase(), { name, tr });
            }

            if (rowByName.size === 0) {
                return {
                    error: 'Broker names not found — td.broker-guide__field--broker strong returned nothing',
                    entries: []
                };
            }

            // Extract spread from a data-column cell.
            // Confirmed classes: .broker-guide__spread, --good, --bad (debug section 2)
            function getCellData(tr, colKey) {
                const cell = tr.querySelector(`td[data-column="${colKey}"]`);
                if (!cell) return { spread: null, commission: null, quality: null };

                const spreadDiv = cell.querySelector('.broker-guide__spread');
                if (!spreadDiv) return { spread: null, commission: null, quality: null };

                const spanEl = spreadDiv.querySelector(':scope > span');
                const spread = spanEl ? spanEl.textContent.trim() || null : null;

                const commEl = spreadDiv.querySelector('.commission, [class*="commission"]');
                const commission = commEl ? (commEl.textContent.trim() || null) : null;

                let quality = null;
                if (spreadDiv.classList.contains('broker-guide__spread--good')) quality = 'good';
                if (spreadDiv.classList.contains('broker-guide__spread--bad'))  quality = 'bad';

                return { spread, commission, quality };
            }

            // Match each requested broker by case-insensitive substring
            const entries = [];
            for (const targetName of brokerNames) {
                const lower = targetName.toLowerCase();
                let match = null;
                for (const [key, val] of rowByName) {
                    if (key.includes(lower)) { match = val; break; }
                }

                if (!match) {
                    entries.push({
                        broker: targetName,
                        found:  false,
                        symbol_data: colKeys.map((_, i) => ({
                            symbol: symbols[i],
                            spread: null, commission: null, quality: null,
                        })),
                    });
                    continue;
                }

                const symbol_data = colKeys.map((colKey, i) => ({
                    symbol: symbols[i],
                    ...getCellData(match.tr, colKey),
                }));

                entries.push({ broker: match.name, found: true, symbol_data });
            }

            return { error: null, entries };
        }
        """,
        {"brokerNames": broker_names, "colKeys": col_keys, "symbols": symbols},
    )

    if result.get("error"):
        raise RuntimeError(f"BG JS extraction failed: {result['error']}")

    raw_entries = result.get("entries", [])
    logger.info(
        "BG: %d broker(s) extracted | symbols=%s | at %s",
        len(raw_entries), symbols, scraped_at,
    )

    entries: List[BrokerSpreadEntry] = []
    for raw in raw_entries:
        if not raw.get("found"):
            logger.warning(
                "BG: broker %r not found on page — all spreads will be null",
                raw["broker"],
            )
        symbol_spreads = [
            BrokerSymbolSpread(
                symbol     = sd["symbol"],
                spread     = sd.get("spread"),
                commission = sd.get("commission"),
                quality    = sd.get("quality"),
            )
            for sd in raw.get("symbol_data", [])
        ]
        entries.append(
            BrokerSpreadEntry(
                broker     = raw["broker"],
                symbols    = symbol_spreads,
                scraped_at = scraped_at,
            )
        )

    return entries, scraped_at