import asyncio
import logging
import logging.config
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from browser_manager import BrowserManager
from cache_manager import CacheManager
from routes import forexfactory as ff_routes
from routes import myfxbook    as mfb_routes
from routes import brokerguide as bg_routes

# ---- Logging ----------------------------------------------------------------

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "format": '{"ts":"%(asctime)s","lvl":"%(levelname)s","mod":"%(name)s","msg":"%(message)s"}'
        }
    },
    "handlers": {
        "console": {
            "class":     "logging.StreamHandler",
            "formatter": "json",
        }
    },
    "root": {"level": "INFO", "handlers": ["console"]},
}
logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)


# ---- App lifecycle ----------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---------- startup ----------
    cache = CacheManager()
    bm    = BrowserManager()

    app.state.cache           = cache
    app.state.browser_manager = bm

    await bm.start()
    logger.info("Browser manager ready (FF + MFB calendar + MFB broker guide).")

    # Background cache-purge task (runs every 5 min)
    async def _purge_loop():
        while True:
            await asyncio.sleep(300)
            n = cache.purge_expired(30 * 60)
            if n:
                logger.info("Purged %d stale cache entries.", n)

    task = asyncio.create_task(_purge_loop())

    yield  # ---------- app running ----------

    task.cancel()
    await bm.stop()
    logger.info("Shutdown complete.")


# ---- FastAPI app ------------------------------------------------------------

app = FastAPI(
    title       = "Economic Calendar & Broker Spreads Scraper",
    description = (
        "Persistent-session Playwright scraper for:\n"
        "• ForexFactory economic calendar\n"
        "• MyFxBook economic calendar\n"
        "• MyFxBook live broker spreads"
    ),
    version     = "2.0.0",
    lifespan    = lifespan,
)

app.include_router(ff_routes.router,  prefix="/forexfactory",   tags=["ForexFactory"])
app.include_router(mfb_routes.router, prefix="/myfxbook",        tags=["MyFxBook Calendar"])
app.include_router(bg_routes.router,  prefix="/broker-spreads",  tags=["Broker Spreads"])


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---- Global error handler ---------------------------------------------------

@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "INTERNAL_ERROR", "details": str(exc)},
    )


# ---- Entry point ------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host    = "0.0.0.0",
        port    = 8000,
        reload  = False,   # Never reload in prod — would destroy browser state
        workers = 1,       # Single process: browser lives in one process
    )