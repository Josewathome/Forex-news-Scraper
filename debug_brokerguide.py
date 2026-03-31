"""
debug_brokerguide.py
──────────────────────────────────────────────────────────────────────────────
Run this standalone to inspect EXACTLY what the MFxBook broker-spreads page
renders in headless Chromium — no assumptions, just raw truth.

Usage:
    python debug_brokerguide.py

Output sections:
  [1] Page title & final URL
  [2] All unique class names that contain "broker" or "slide" or "guide"
  [3] All <tbody> elements — id, class, row count
  [4] First broker row — full raw HTML (so you see every attribute)
  [5] All td[data-column] values found anywhere on the page
  [6] Broker name extraction attempts — every strategy tried
  [7] Spread cell extraction for row 0 — all child elements
  [8] Full clone table HTML (first 8000 chars)
  [9] Full original/scrollable table HTML (first 8000 chars)
"""

import asyncio
import json
from playwright.async_api import async_playwright

BG_URL = "https://www.forexfactory.com/brokers/kenya"

STEALTH = """
    Object.defineProperty(navigator, 'webdriver',  { get: () => undefined });
    Object.defineProperty(navigator, 'plugins',    { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages',  { get: () => ['en-US', 'en'] });
    window.chrome = { runtime: {} };
"""

DIVIDER = "\n" + "═" * 80 + "\n"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        await ctx.add_init_script(STEALTH)
        page = await ctx.new_page()

        print("\n⏳  Navigating to broker spreads page …")
        await page.goto(BG_URL, wait_until="domcontentloaded", timeout=60_000)

        # Give JS-rendered content a moment to settle
        await page.wait_for_timeout(3_000)

        # ── Run the full DOM audit in one evaluate() call ─────────────────────
        data = await page.evaluate("""
        () => {
            const out = {};

            // ── [1] Page meta ─────────────────────────────────────────────────
            out.title    = document.title;
            out.finalUrl = window.location.href;

            // ── [2] All unique class tokens containing 'broker','slide','guide'
            const keywords = ['broker', 'slide', 'guide'];
            const classSet = new Set();
            document.querySelectorAll('*').forEach(el => {
                el.classList.forEach(c => {
                    if (keywords.some(k => c.toLowerCase().includes(k)))
                        classSet.add(c);
                });
            });
            out.relevantClasses = [...classSet].sort();

            // ── [3] Every <tbody> — id, class, row count ──────────────────────
            out.tbodies = [...document.querySelectorAll('tbody')].map(tb => ({
                id:       tb.id || null,
                classes:  tb.className,
                rowCount: tb.querySelectorAll('tr').length,
            }));

            // ── [4] First <tr> in any tbody — raw outerHTML (trimmed) ─────────
            const firstRow = document.querySelector('tbody tr');
            out.firstRowHTML = firstRow
                ? firstRow.outerHTML.slice(0, 3000)
                : 'NO ROW FOUND';

            // ── [5] All td[data-column] values on the page ────────────────────
            out.dataColumns = [
                ...new Set(
                    [...document.querySelectorAll('td[data-column]')]
                        .map(td => td.getAttribute('data-column'))
                )
            ].sort();

            // ── [6] Broker name extraction — every strategy ───────────────────
            const nameStrategies = {
                'strong inside broker td (clone)':
                    [...document.querySelectorAll(
                        '.slidetable__clone td.broker-guide__field--broker strong'
                    )].map(e => e.textContent.trim()),

                'any strong inside broker__field--broker':
                    [...document.querySelectorAll(
                        'td.broker-guide__field--broker strong'
                    )].map(e => e.textContent.trim()),

                'img alt inside broker td':
                    [...document.querySelectorAll(
                        'td.broker-guide__field--broker img'
                    )].map(e => e.alt),

                'any td with class broker__field--broker (text)':
                    [...document.querySelectorAll(
                        'td.broker-guide__field--broker'
                    )].slice(0,10).map(e => e.textContent.trim().slice(0, 80)),

                'tr data-broker attribute':
                    [...document.querySelectorAll('tr[data-broker]')]
                        .map(e => e.dataset.broker),
            };
            out.nameStrategies = nameStrategies;

            // ── [7] First eurusd cell — every child element ───────────────────
            const euCell = document.querySelector('td[data-column="eurusd"]');
            out.eurusdCellHTML = euCell
                ? euCell.outerHTML.slice(0, 2000)
                : 'NO eurusd cell found';

            // All class names on children of that cell
            out.eurusdChildClasses = euCell
                ? [...euCell.querySelectorAll('*')].map(e => ({
                    tag:     e.tagName,
                    classes: e.className,
                    text:    e.textContent.trim().slice(0, 60),
                  }))
                : [];

            // ── [8] Clone table HTML (first 6000 chars) ───────────────────────
            const clone = document.querySelector('.slidetable__clone table');
            out.cloneTableHTML = clone
                ? clone.outerHTML.slice(0, 6000)
                : 'NO .slidetable__clone table found';

            // ── [9] Scrollable table HTML (first 6000 chars) ──────────────────
            const orig = document.querySelector(
                '.slidetable__original .slidetable__overflow table'
            );
            out.originalTableHTML = orig
                ? orig.outerHTML.slice(0, 6000)
                : 'NO slidetable__original table found';

            // ── [10] All tables on the page — tag, id, class, row count ───────
            out.allTables = [...document.querySelectorAll('table')].map(t => ({
                id:       t.id || null,
                classes:  t.className,
                rows:     t.querySelectorAll('tr').length,
                parentId: t.parentElement ? t.parentElement.id || null : null,
                parentCls:t.parentElement ? t.parentElement.className.slice(0,80) : null,
            }));

            // ── [11] First 10 rows of the ORIGINAL table — just the text ──────
            const origRows = [
                ...document.querySelectorAll(
                    '.slidetable__original tbody tr'
                )
            ].slice(0, 10);
            out.originalRowSample = origRows.map((tr, i) => {
                const cells = [...tr.querySelectorAll('td[data-column]')];
                return {
                    rowIndex: i,
                    columns: cells.map(td => ({
                        col:        td.getAttribute('data-column'),
                        classes:    td.className,
                        text:       td.textContent.trim().slice(0, 100),
                    }))
                };
            });

            return out;
        }
        """)

        # ── Print everything ──────────────────────────────────────────────────

        print(DIVIDER)
        print("【1】 PAGE META")
        print(f"  Title : {data['title']}")
        print(f"  URL   : {data['finalUrl']}")

        print(DIVIDER)
        print("【2】 CLASS NAMES CONTAINING 'broker' / 'slide' / 'guide'")
        for c in data["relevantClasses"]:
            print(f"  .{c}")

        print(DIVIDER)
        print("【3】 ALL <tbody> ELEMENTS")
        for i, tb in enumerate(data["tbodies"]):
            print(f"  [{i}] id={tb['id']!r:20s}  class={tb['classes']!r:60s}  rows={tb['rowCount']}")

        print(DIVIDER)
        print("【4】 FIRST <tr> RAW HTML (first 3000 chars)")
        print(data["firstRowHTML"])

        print(DIVIDER)
        print("【5】 ALL td[data-column] VALUES")
        for c in data["dataColumns"]:
            print(f"  data-column={c!r}")

        print(DIVIDER)
        print("【6】 BROKER NAME EXTRACTION — ALL STRATEGIES")
        for strategy, names in data["nameStrategies"].items():
            print(f"\n  Strategy: {strategy}")
            if names:
                for n in names:
                    print(f"    → {n!r}")
            else:
                print("    ✗  NOTHING FOUND")

        print(DIVIDER)
        print("【7】 FIRST eurusd CELL — CHILDREN")
        print("  Raw HTML:")
        print(data["eurusdCellHTML"])
        print("\n  Child elements:")
        for child in data["eurusdChildClasses"]:
            print(f"    <{child['tag'].lower()} class={child['classes']!r}>  text={child['text']!r}")

        print(DIVIDER)
        print("【8】 CLONE TABLE HTML (first 6000 chars)")
        print(data["cloneTableHTML"])

        print(DIVIDER)
        print("【9】 ORIGINAL SCROLLABLE TABLE HTML (first 6000 chars)")
        print(data["originalTableHTML"])

        print(DIVIDER)
        print("【10】 ALL TABLES ON PAGE")
        for i, t in enumerate(data["allTables"]):
            print(f"  [{i}] id={t['id']!r:15s}  rows={t['rows']:3d}  "
                  f"class={t['classes']!r:50s}  "
                  f"parent_id={t['parentId']!r}  parent_cls={t['parentCls']!r}")

        print(DIVIDER)
        print("【11】 FIRST 10 ROWS — ORIGINAL TABLE (data-column cells only)")
        for row in data["originalRowSample"]:
            print(f"\n  Row {row['rowIndex']}:")
            if row["columns"]:
                for cell in row["columns"]:
                    print(f"    col={cell['col']!r:20s}  "
                          f"classes={cell['classes']!r:60s}  "
                          f"text={cell['text']!r}")
            else:
                print("    (no td[data-column] cells in this row)")

        print(DIVIDER)
        print("✅  Debug complete.\n")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())