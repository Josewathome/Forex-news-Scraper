import asyncio
import logging
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

logger = logging.getLogger(__name__)

FF_URL  = "https://www.forexfactory.com/calendar"
MFB_URL = "https://www.myfxbook.com/forex-economic-calendar"
BG_URL  = "https://www.forexfactory.com/brokers/kenya"

STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver',  { get: () => undefined });
    Object.defineProperty(navigator, 'plugins',    { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages',  { get: () => ['en-US', 'en'] });
    Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 1 });
    window.chrome = { runtime: {} };
"""

# Slightly different UA for the BG context so it looks like a different visitor
BG_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)


class BrowserManager:
    """
    Owns the single Chromium instance with TWO separate browser contexts:

      _ctx     — shared context for ff_page (ForexFactory calendar) and
                 mfb_page (MyFxBook calendar)

      _bg_ctx  — isolated context exclusively for bg_page (ForexFactory
                 broker guide).  Kept separate because ff_page and bg_page
                 both hit forexfactory.com — sharing a context causes FF to
                 detect multiple tabs from the same session and block the
                 broker data XHR from completing.
    """

    def __init__(self) -> None:
        self._playwright: Playwright      | None = None
        self._browser:    Browser         | None = None
        self._ctx:        BrowserContext  | None = None   # FF + MFB
        self._bg_ctx:     BrowserContext  | None = None   # BG only

        self.ff_page:  Page | None = None
        self.mfb_page: Page | None = None
        self.bg_page:  Page | None = None

        self.ff_lock  = asyncio.Lock()
        self.mfb_lock = asyncio.Lock()
        self.bg_lock  = asyncio.Lock()

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

        # ── Shared context: FF calendar + MFB calendar ────────────────────────
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
        await self._ctx.add_init_script(STEALTH_SCRIPT)

        # ── Isolated context: BG broker guide only ────────────────────────────
        # Different UA + fresh cookie jar = looks like a separate visitor to FF.
        self._bg_ctx = await self._browser.new_context(
            user_agent=BG_USER_AGENT,
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
        await self._bg_ctx.add_init_script(STEALTH_SCRIPT)

        # ── Create pages ──────────────────────────────────────────────────────
        self.ff_page  = await self._ctx.new_page()
        self.mfb_page = await self._ctx.new_page()
        self.bg_page  = await self._bg_ctx.new_page()   # isolated context

        await self._navigate_ff()
        await self._navigate_mfb()
        # bg_page left blank — scraper calls page.goto() on every live request
        logger.info("FF and MFB pages ready. BG page will load on first request.")

    async def stop(self) -> None:
        logger.info("Shutting down browser …")
        for ctx in (self._ctx, self._bg_ctx):
            try:
                if ctx:
                    await ctx.close()
            except Exception as exc:
                logger.warning("Context close error: %s", exc)
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            logger.warning("Shutdown error: %s", exc)

    # ------------------------------------------------------------------ #
    #  Public reload methods                                               #
    # ------------------------------------------------------------------ #

    async def reload_ff(self) -> None:
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
        try:
            await self._wait_for_mfb_ready(self.mfb_page)
        except Exception as exc:
            logger.warning("MFB page check failed (%s), recreating …", exc)
            await self._recover_mfb()

    async def reload_bg(self) -> None:
        """bg_page health-check — just ensures the page object is alive."""
        try:
            if self.bg_page is None or self.bg_page.is_closed():
                logger.warning("BG page object gone, recreating …")
                self.bg_page = await self._bg_ctx.new_page()
        except Exception as exc:
            logger.warning("BG page check failed (%s), recreating …", exc)
            try:
                self.bg_page = await self._bg_ctx.new_page()
            except Exception as inner:
                logger.error("BG page recreation failed: %s", inner)

    # ------------------------------------------------------------------ #
    #  Internal navigation                                                 #
    # ------------------------------------------------------------------ #

    async def _navigate_ff(self) -> None:
        await self.ff_page.goto(FF_URL, wait_until="networkidle", timeout=60_000)
        await self._wait_for_ff_ready(self.ff_page)
        logger.debug("ForexFactory calendar page ready.")

    async def _navigate_mfb(self) -> None:
        await self.mfb_page.goto(MFB_URL, wait_until="domcontentloaded", timeout=60_000)
        await self._wait_for_mfb_ready(self.mfb_page)
        logger.debug("MyFxBook calendar page ready.")

    # ------------------------------------------------------------------ #
    #  Wait-for-ready selectors                                            #
    # ------------------------------------------------------------------ #

    async def _wait_for_ff_ready(self, page: Page) -> None:
        await page.wait_for_selector("table.calendar__table", timeout=30_000)
        logger.debug("FF ready — table.calendar__table found")

    async def _wait_for_mfb_ready(self, page: Page) -> None:
        await page.wait_for_selector("#economicCalendarTable", timeout=30_000)
        logger.debug("MFB calendar ready — #economicCalendarTable found")

    # ------------------------------------------------------------------ #
    #  Recovery helpers                                                    #
    # ------------------------------------------------------------------ #

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

    async def _recover_bg(self) -> None:
        try:
            await self.bg_page.close()
        except Exception:
            pass
        # Always recreate from the isolated context
        self.bg_page = await self._bg_ctx.new_page()
        logger.debug("BG page recreated in isolated context — scraper loads on next request.")