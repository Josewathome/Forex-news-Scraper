import asyncio
import logging
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

logger = logging.getLogger(__name__)

FF_URL  = "https://www.forexfactory.com/calendar"
MFB_URL = "https://www.myfxbook.com/forex-economic-calendar"

# Injected into every page before any script runs.
# Removes the most common bot-detection fingerprints.
STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver',  { get: () => undefined });
    Object.defineProperty(navigator, 'plugins',    { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages',  { get: () => ['en-US', 'en'] });
    Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 1 });
    window.chrome = { runtime: {} };
"""


class BrowserManager:
    """
    Owns the single Chromium instance and two persistent pages.
    FF uses page.evaluate fetch() — no DOM scraping, just browser cookies.
    MFB uses page.evaluate fetch() + DOM injection for parsing.
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None     = None
        self._browser:    Browser   | None      = None
        self._ctx:        BrowserContext | None = None

        self.ff_page:  Page | None = None
        self.mfb_page: Page | None = None

        self.ff_lock  = asyncio.Lock()
        self.mfb_lock = asyncio.Lock()

    async def start(self) -> None:
        logger.info("Launching Chromium …")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        self._ctx = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            java_script_enabled=True,
            ignore_https_errors=True,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        await self._ctx.add_init_script(STEALTH_SCRIPT)

        # Both FF and MFB use Playwright — FF reads embedded JS state,
        # MFB uses fetch() API with browser session cookies
        self.ff_page  = await self._ctx.new_page()
        self.mfb_page = await self._ctx.new_page()

        await self._navigate_ff()
        await self._navigate_mfb()
        logger.info("Both pages loaded and ready.")

    async def stop(self) -> None:
        logger.info("Shutting down browser …")
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            logger.warning("Shutdown error: %s", exc)

    async def reload_ff(self) -> None:
        """
        Called every cache-miss (every 30 min at most).
        Navigate the FF page fresh so window.calendarComponentStates[1]
        contains up-to-date events for the scraper to read.
        """
        try:
            logger.info("FF: reloading page for fresh data …")
            await self.ff_page.goto(FF_URL, wait_until="domcontentloaded", timeout=60_000)
            logger.debug("FF: page reloaded successfully")
        except Exception as exc:
            logger.warning("FF reload failed (%s), recovering …", exc)
            await self._recover_ff()

    async def reload_mfb(self) -> None:
        try:
            await self._wait_for_mfb_ready(self.mfb_page)
        except Exception as exc:
            logger.warning("MFB page check failed (%s), recovering …", exc)
            await self._recover_mfb()

    async def _navigate_ff(self) -> None:
        await self.ff_page.goto(FF_URL, wait_until="networkidle", timeout=60_000)
        await self.ff_page.wait_for_selector("table.calendar__table", timeout=30_000)
        logger.debug("FF page ready.")

    async def _navigate_mfb(self) -> None:
        await self.mfb_page.goto(MFB_URL, wait_until="domcontentloaded", timeout=60_000)
        await self._wait_for_mfb_ready(self.mfb_page)
        logger.debug("MyFxBook page ready.")

    async def _wait_for_mfb_ready(self, page: Page) -> None:
        await page.wait_for_selector("#economicCalendarTable", timeout=30_000)
        logger.debug("MFB ready — #economicCalendarTable found")

    async def _recover_ff(self) -> None:
        try:
            await self.ff_page.close()
        except Exception:
            pass
        self.ff_page = await self._ctx.new_page()
        await self._navigate_ff()

    async def _recover_mfb(self) -> None:
        try:
            await self.mfb_page.close()
        except Exception:
            pass
        self.mfb_page = await self._ctx.new_page()
        await self._navigate_mfb()
    """
    Owns the single Chromium instance and ONE persistent page — MyFxBook only.
    ForexFactory now uses plain HTTP (httpx) — no browser needed.
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None     = None
        self._browser:    Browser   | None      = None
        self._ctx:        BrowserContext | None = None

        self.ff_page:  None = None          # unused — FF is pure HTTP now
        self.mfb_page: Page | None = None

        self.ff_lock  = asyncio.Lock()      # still used by the route layer
        self.mfb_lock = asyncio.Lock()

    async def start(self) -> None:
        logger.info("Launching Chromium (MFB only) …")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        self._ctx = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            java_script_enabled=True,
            ignore_https_errors=True,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        await self._ctx.add_init_script(STEALTH_SCRIPT)

        self.mfb_page = await self._ctx.new_page()
        await self._navigate_mfb()
        logger.info("MFB page ready.")

    async def stop(self) -> None:
        logger.info("Shutting down browser …")
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            logger.warning("Shutdown error: %s", exc)

    async def reload_ff(self) -> None:
        pass   # FF uses httpx — nothing to do

    async def reload_mfb(self) -> None:
        try:
            await self._wait_for_mfb_ready(self.mfb_page)
        except Exception as exc:
            logger.warning("MFB page check failed (%s), recovering …", exc)
            await self._recover_mfb()

    async def _navigate_mfb(self) -> None:
        await self.mfb_page.goto(MFB_URL, wait_until="domcontentloaded", timeout=60_000)
        await self._wait_for_mfb_ready(self.mfb_page)
        logger.debug("MyFxBook page ready.")

    async def _wait_for_mfb_ready(self, page: Page) -> None:
        await page.wait_for_selector("#economicCalendarTable", timeout=30_000)
        logger.debug("MFB ready — #economicCalendarTable found")

    async def _recover_mfb(self) -> None:
        try:
            await self.mfb_page.close()
        except Exception:
            pass
        self.mfb_page = await self._ctx.new_page()
        await self._navigate_mfb()
    def __init__(self) -> None:
        self._playwright: Playwright | None     = None
        self._browser:    Browser   | None      = None
        self._ctx:        BrowserContext | None = None

        self.ff_page:  Page | None = None
        self.mfb_page: Page | None = None

        self.ff_lock  = asyncio.Lock()
        self.mfb_lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        logger.info("Launching Chromium …")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--disable-extensions",
                "--disable-gpu",
                "--window-size=1280,900",
            ],
        )
        self._ctx = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            java_script_enabled=True,
            ignore_https_errors=True,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,image/webp,*/*;q=0.8"
                ),
            },
        )

        # Apply stealth before any page loads
        await self._ctx.add_init_script(STEALTH_SCRIPT)

        self.ff_page  = await self._ctx.new_page()
        self.mfb_page = await self._ctx.new_page()

        await self._navigate_ff()
        await self._navigate_mfb()
        logger.info("Both pages loaded and ready.")

    async def stop(self) -> None:
        logger.info("Shutting down browser …")
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            logger.warning("Shutdown error: %s", exc)

    # ------------------------------------------------------------------ #
    #  Public reload methods (called by scrapers)                         #
    # ------------------------------------------------------------------ #

    async def reload_ff(self) -> None:
        """
        FF now uses an API call — no page reload needed.
        The persistent page only exists to hold session cookies.
        Only re-navigate if the page has crashed (URL gone blank).
        """
        try:
            current_url = self.ff_page.url
            if not current_url or "forexfactory" not in current_url:
                logger.warning("FF page URL lost (%s), re-navigating …", current_url)
                await self._recover_ff()
            else:
                logger.debug("FF session page healthy at %s", current_url)
        except Exception as exc:
            logger.warning("FF page check failed (%s), recovering …", exc)
            await self._recover_ff()

    async def reload_mfb(self) -> None:
        # MFB scraper navigates fresh itself via the API — this is a no-op.
        # Kept for interface consistency; recovery still works if page crashes.
        try:
            await self._wait_for_mfb_ready(self.mfb_page)
        except Exception as exc:
            logger.warning("MFB page check failed (%s), recreating …", exc)
            await self._recover_mfb()

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    async def _navigate_ff(self) -> None:
        await self.ff_page.goto(FF_URL, wait_until="networkidle", timeout=60_000)
        await self._wait_for_ff_ready(self.ff_page)
        logger.debug("ForexFactory page ready.")

    async def _navigate_mfb(self) -> None:
        # FIX: MFB ads fire forever — networkidle never resolves, use domcontentloaded
        await self.mfb_page.goto(MFB_URL, wait_until="domcontentloaded", timeout=60_000)
        await self._wait_for_mfb_ready(self.mfb_page)
        logger.debug("MyFxBook page ready.")

    async def _wait_for_ff_ready(self, page: Page) -> None:
        # Confirmed by debug_ff.py: table id is absent, class is 'calendar__table'
        # Row type confirmed: tr.calendar__row--day-breaker holds date cells
        await page.wait_for_selector("table.calendar__table", timeout=30_000)
        logger.debug("FF ready — table.calendar__table found")

    async def _wait_for_mfb_ready(self, page: Page) -> None:
        # Confirmed by debug_mfb.py: table id='economicCalendarTable'
        await page.wait_for_selector("#economicCalendarTable", timeout=30_000)
        logger.debug("MFB ready — #economicCalendarTable found")

    async def _recover_ff(self) -> None:
        try:
            await self.ff_page.close()
        except Exception:
            pass
        self.ff_page = await self._ctx.new_page()
        await self._navigate_ff()

    async def _recover_mfb(self) -> None:
        try:
            await self.mfb_page.close()
        except Exception:
            pass
        self.mfb_page = await self._ctx.new_page()
        await self._navigate_mfb()