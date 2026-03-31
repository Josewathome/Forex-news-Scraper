"""
MyFxBook Broker Guide scraper — https://www.forexfactory.com/brokers/kenya

Confirmed DOM structure (from live page HTML):
──────────────────────────────────────────────
The page uses a split slidetable layout:

  .slidetable__clone   →  fixed left panel (broker names)
    table > tbody.broker-guide__rows > tr
      td.broker-guide__field.broker-guide__field--broker
        strong  ← broker display name, e.g. "HFM", "IC Markets"

  .slidetable__original > .slidetable__overflow  →  scrollable data panel
    table.slidetable__table > tbody.broker-guide__rows > tr
      td[data-column="eurusd"]   ← one <td> per symbol column
        div.broker-guide__spread            (may also carry --good / --bad)
          span                              ← spread value, e.g. "0.0"
          div.commission                    ← commission,  e.g. "+$3" or ""
        div.broker-guide__comment           ← tooltip (ignored)

Clone rows and data rows share the same order — matched by index.

Symbol → data-column mapping  (strip "/" and lowercase):
  EUR/USD → eurusd,  GBP/USD → gbpusd,  USD/JPY → usdjpy, etc.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Tuple

from playwright.async_api import Page, TimeoutError as PWTimeout

from models import BrokerSpreadEntry, BrokerSymbolSpread

logger = logging.getLogger(__name__)

BG_URL          = "https://www.forexfactory.com/brokers/kenya"
MAX_RETRIES     = 2
_READY_SELECTOR = "tbody.broker-guide__rows tr"   # confirmed present in both sub-tables


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
    """
    Returns (entries, scraped_at_iso).

    brokers  — display names, e.g. ["HFM", "IC Markets"]
               case-insensitive substring match against <strong> in clone table.
    symbols  — forex pairs,   e.g. ["EUR/USD", "USD/JPY"]
               slash is optional: "EURUSD" also accepted.
    """
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
            await asyncio.sleep(2)
        except Exception as exc:
            logger.error("BG error attempt %d/%d: %s", attempt, MAX_RETRIES, exc, exc_info=True)
            if attempt == MAX_RETRIES:
                raise


# ── Core ──────────────────────────────────────────────────────────────────────

async def _load_and_parse(
    page: Page,
    broker_names: List[str],
    symbols: List[str],
    col_keys: List[str],
) -> Tuple[List[BrokerSpreadEntry], str]:

    # Always reload — spreads are live real-time data
    logger.info("BG: navigating to live spreads page …")
    await page.goto(BG_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_selector(_READY_SELECTOR, timeout=30_000)

    scraped_at = _now_iso()

    result = await page.evaluate(
        """
        (args) => {
            const { brokerNames, colKeys, symbols } = args;

            // ── 1. Broker names from the CLONE (fixed left) table ─────────────
            // Each <tr> → td.broker-guide__field--broker → <strong> = display name
            const cloneRows = [
                ...document.querySelectorAll(
                    '.slidetable__clone tbody.broker-guide__rows tr'
                )
            ];

            if (cloneRows.length === 0)
                return { error: 'clone rows not found — page layout unexpected', entries: [] };

            const nameByIndex = cloneRows.map(tr => {
                const s = tr.querySelector('td.broker-guide__field--broker strong');
                return s ? s.textContent.trim() : null;
            });

            // ── 2. Data rows from the SCROLLABLE table ────────────────────────
            const dataRows = [
                ...document.querySelectorAll(
                    '.slidetable__original tbody.broker-guide__rows tr'
                )
            ];

            if (dataRows.length === 0)
                return { error: 'data rows not found in slidetable__original', entries: [] };

            // ── 3. Helper: spread data for one td[data-column] ────────────────
            function getCellData(dataRow, colKey) {
                const cell = dataRow.querySelector(`td[data-column="${colKey}"]`);
                if (!cell) return { spread: null, commission: null, quality: null };

                const spreadDiv = cell.querySelector('.broker-guide__spread');
                if (!spreadDiv) return { spread: null, commission: null, quality: null };

                // Spread: first <span> inside the spread div
                const spanEl = spreadDiv.querySelector(':scope > span');
                const spread = spanEl ? spanEl.textContent.trim() : null;

                // Commission: div.commission — may be empty string (no commission)
                const commEl = spreadDiv.querySelector('div.commission');
                const commission = commEl
                    ? (commEl.textContent.trim() || null)
                    : null;

                // Quality modifier class: --good / --bad (relative to other brokers)
                let quality = null;
                if (spreadDiv.classList.contains('broker-guide__spread--good')) quality = 'good';
                if (spreadDiv.classList.contains('broker-guide__spread--bad'))  quality = 'bad';

                return { spread, commission, quality };
            }

            // ── 4. Match each requested broker by substring then extract data ──
            const entries = [];

            for (const targetName of brokerNames) {
                const lower = targetName.toLowerCase();

                let matchedIdx  = -1;
                let matchedName = null;

                for (let i = 0; i < nameByIndex.length; i++) {
                    const n = nameByIndex[i];
                    if (n && n.toLowerCase().includes(lower)) {
                        matchedIdx  = i;
                        matchedName = n;   // exact name as shown on the page
                        break;
                    }
                }

                if (matchedIdx === -1 || matchedIdx >= dataRows.length) {
                    entries.push({
                        broker:      targetName,
                        found:       false,
                        symbol_data: colKeys.map((_, i) => ({
                            symbol: symbols[i], spread: null, commission: null, quality: null,
                        })),
                    });
                    continue;
                }

                const dataRow = dataRows[matchedIdx];
                const symbol_data = colKeys.map((colKey, i) => ({
                    symbol: symbols[i],
                    ...getCellData(dataRow, colKey),
                }));

                entries.push({ broker: matchedName, found: true, symbol_data });
            }

            return { error: null, entries };
        }
        """,
        {"brokerNames": broker_names, "colKeys": col_keys, "symbols": symbols},
    )

    if result.get("error"):
        raise RuntimeError(f"BG JS extraction failed: {result['error']}")

    raw_entries = result.get("entries", [])
    logger.info("BG: %d broker(s) extracted | symbols=%s | at %s",
                len(raw_entries), symbols, scraped_at)

    entries: List[BrokerSpreadEntry] = []
    for raw in raw_entries:
        if not raw.get("found"):
            logger.warning("BG: broker %r not found on page — all spreads will be null",
                           raw["broker"])

        symbol_spreads = [
            BrokerSymbolSpread(
                symbol     = sd["symbol"],
                spread     = sd.get("spread"),
                commission = sd.get("commission"),
                quality    = sd.get("quality"),
            )
            for sd in raw.get("symbol_data", [])
        ]
        entries.append(BrokerSpreadEntry(
            broker     = raw["broker"],
            symbols    = symbol_spreads,
            scraped_at = scraped_at,
        ))

    return entries, scraped_at