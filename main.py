import asyncio
import logging
import logging.config
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

load_dotenv()

from browser_manager import BrowserManager
from cache_manager   import CacheManager
from routes import forexfactory as ff_routes
from routes import myfxbook     as mfb_routes
from routes import brokerguide  as bg_routes

# New unified DB layer (replaces log_manager.py)
from db_manager   import DatabaseManager
from auth_manager import APIKeyManager, RateLimiter

# ── Configuration ─────────────────────────────────────────────────────────── #

DATA_DIR            = Path(os.getenv("DATA_DIR", "data"))
LOGS_DIR            = Path(os.getenv("LOGS_DIR", "logs"))   # still used for app.log
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH             = DATA_DIR / "forex_api.db"

DASHBOARD_USERNAME  = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD  = os.getenv("DASHBOARD_PASSWORD", "changeme")
JWT_SECRET          = os.getenv("JWT_SECRET",          "CHANGE_THIS_JWT_SECRET")
ENCRYPTION_KEY      = os.getenv("ENCRYPTION_KEY",      "CHANGE_THIS_32_CHAR_ENCRYPTION_KEY!")
JWT_ALGORITHM       = "HS256"
JWT_EXPIRY_HOURS    = int(os.getenv("JWT_EXPIRY_HOURS",   "24"))
LOG_RETENTION_DAYS  = int(os.getenv("LOG_RETENTION_DAYS", "3"))

# Max login attempts per IP before a 429 is returned.
# 5 attempts / 60 seconds is enough for a legitimate user who mistyped
# their password; it makes brute-forcing impractical.
LOGIN_RATE_LIMIT    = int(os.getenv("LOGIN_RATE_LIMIT",    "5"))
LOGIN_RATE_WINDOW   = int(os.getenv("LOGIN_RATE_WINDOW",  "60"))  # seconds

# ── Logging  (identical to original) ─────────────────────────────────────── #

_LOG_DIR = str(LOGS_DIR)
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "format": (
                '{"ts":"%(asctime)s","lvl":"%(levelname)s",'
                '"mod":"%(name)s","msg":"%(message)s"}'
            )
        }
    },
    "handlers": {
        "console": {
            "class":     "logging.StreamHandler",
            "formatter": "json",
        },
        "file": {
            "class":       "logging.handlers.TimedRotatingFileHandler",
            "formatter":   "json",
            "filename":    f"{_LOG_DIR}/app.log",
            "when":        "midnight",
            "interval":    1,
            "backupCount": 3,
            "encoding":    "utf-8",
        },
    },
    "root": {
        "level":    "INFO",
        "handlers": ["console", "file"],
    },
}
logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)

# ── JWT helpers ───────────────────────────────────────────────────────────── #

try:
    import jwt
except ImportError:
    raise RuntimeError("PyJWT is required: pip install PyJWT")


def _create_jwt(username: str) -> str:
    return jwt.encode(
        {
            "sub": username,
            "iat": datetime.utcnow(),
            "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS),
        },
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


def _verify_jwt(token: str) -> Optional[str]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM]).get("sub")
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


# ── Module-level singletons ───────────────────────────────────────────────── #
# db.connect() is called in lifespan; by the time any request hits these
# they are fully initialised.

db           = DatabaseManager(DB_PATH)
key_manager  = APIKeyManager(db, ENCRYPTION_KEY)
rate_limiter = RateLimiter()

_PUBLIC_PREFIXES  = ("/auth/", "/dashboard", "/api/dashboard", "/health", "/docs", "/openapi")
_TRACKED_PREFIXES = ("/myfxbook", "/forexfactory", "/broker-spreads")


# ── Lifespan  (identical pattern to original) ─────────────────────────────── #

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── startup ──────────────────────────────────────────────────────────── #
    cache = CacheManager()
    bm    = BrowserManager()

    app.state.cache           = cache           # same as original
    app.state.browser_manager = bm              # same as original

    await bm.start()
    logger.info("Browser manager ready.")

    # Connect SQLite and run initial log cleanup
    await db.connect()
    cleaned = await db.cleanup_old_logs(LOG_RETENTION_DAYS)
    if cleaned:
        logger.info("Startup: removed %d stale log rows.", cleaned)

    # Background task 1: cache purge every 5 min (identical to original)
    async def _cache_purge():
        while True:
            await asyncio.sleep(300)
            n = cache.purge_expired(30 * 60)
            if n:
                logger.info("Purged %d stale cache entries.", n)

    # Background task 2: log cleanup once every 24 h
    async def _log_cleanup():
        while True:
            await asyncio.sleep(24 * 3600)
            await db.cleanup_old_logs(LOG_RETENTION_DAYS)

    t_cache   = asyncio.create_task(_cache_purge())
    t_cleanup = asyncio.create_task(_log_cleanup())

    yield  # ── app running ─────────────────────────────────────────────────── #

    t_cache.cancel()
    t_cleanup.cancel()
    await bm.stop()
    await db.close()
    logger.info("Shutdown complete.")


# ── FastAPI app ───────────────────────────────────────────────────────────── #

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


# ── Middleware: API-key auth + rate limiting + logging ────────────────────── #

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    path = request.url.path

    if any(path.startswith(p) for p in _PUBLIC_PREFIXES) or path == "/":
        return await call_next(request)

    raw_key = (
        request.headers.get("X-API-Key")
        or request.query_params.get("api_key")
    )
    if not raw_key:
        return JSONResponse(
            {"error": "Missing API key", "hint": "Pass via X-API-Key header."},
            status_code=401,
        )

    # verify_key is now async
    key_info = await key_manager.verify_key(raw_key)
    if key_info is None:
        # Auth rejection — endpoint never executed, not a scraper failure
        return JSONResponse({"error": "Invalid API key"}, status_code=401)

    if not rate_limiter.is_allowed(key_info["name"], key_info["rate_limit"]):
        # Rate-limit rejection — endpoint never executed, not a scraper failure
        return JSONResponse(
            {
                "error":          "Rate limit exceeded",
                "limit":          key_info["rate_limit"],
                "window_seconds": 60,
                "key_name":       key_info["name"],
            },
            status_code=429,
            headers={
                "X-RateLimit-Limit":     str(key_info["rate_limit"]),
                "X-RateLimit-Remaining": "0",
                "Retry-After":           "60",
            },
        )

    t0       = time.perf_counter()
    response = await call_next(request)
    elapsed  = (time.perf_counter() - t0) * 1000

    if any(path.startswith(p) for p in _TRACKED_PREFIXES):
        await db.log_request(
            path, request.method, response.status_code,
            key_info["name"], elapsed,
        )

    remaining = rate_limiter.get_remaining(key_info["name"], key_info["rate_limit"])
    response.headers["X-RateLimit-Limit"]     = str(key_info["rate_limit"])
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-Key-Name"]            = key_info["name"]
    return response


# ── Health  (identical to original) ──────────────────────────────────────── #

@app.get("/health")
async def health():
    return {
        "status":   "ok",
        "version":  "1.0.0",
        "services": {"forexfactory": "ok", "myfxbook": "ok"},
    }


# ── Global error handler  (identical to original) ─────────────────────────── #

@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "INTERNAL_ERROR", "details": str(exc)},
    )


# ── Auth ──────────────────────────────────────────────────────────────────── #

def _client_ip(request: Request) -> str:
    """
    Return the real client IP, respecting a reverse-proxy X-Forwarded-For header.

    If the app sits behind nginx / a load balancer, the outermost proxy appends
    the real client IP as the first value in X-Forwarded-For.
    Fall back to request.client.host when the header is absent (direct connection).
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/auth/login", tags=["Dashboard Auth"])
async def login(request: Request, body: LoginRequest):
    ip = _client_ip(request)

    # Rate-limit login attempts by IP to block brute-force attacks.
    # Uses the same RateLimiter as API keys but with a "login:{ip}" key
    # so there is no cross-contamination with API key buckets.
    if not rate_limiter.is_allowed(f"login:{ip}", LOGIN_RATE_LIMIT, LOGIN_RATE_WINDOW):
        logger.warning("Login rate-limit hit from IP %s", ip)
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many login attempts. "
                f"Maximum {LOGIN_RATE_LIMIT} attempts per {LOGIN_RATE_WINDOW} seconds."
            ),
            headers={"Retry-After": str(LOGIN_RATE_WINDOW)},
        )

    if body.username != DASHBOARD_USERNAME or body.password != DASHBOARD_PASSWORD:
        # Log the failure so it is visible in app.log.
        # We do NOT hint whether the username or password was wrong.
        logger.warning("Failed login attempt for user '%s' from IP %s", body.username, ip)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    logger.info("Successful login for user '%s' from IP %s", body.username, ip)
    return {
        "access_token": _create_jwt(body.username),
        "token_type":   "bearer",
        "expires_in":   JWT_EXPIRY_HOURS * 3600,
    }


# ── Dashboard HTML ─────────────────────────────────────────────────────────── #

@app.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"])
async def serve_dashboard():
    p = Path("templates/dashboard.html")
    return HTMLResponse(p.read_text() if p.exists() else "<h1>template missing</h1>")


# ── Dashboard API  (JWT protected) ────────────────────────────────────────── #

_bearer = HTTPBearer()


def _require_auth(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> str:
    user = _verify_jwt(creds.credentials)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired JWT")
    return user


@app.get("/api/dashboard/stats", tags=["Dashboard"])
async def get_stats(_: str = Depends(_require_auth)):
    stats, today, keys = await asyncio.gather(
        db.get_stats(),
        db.get_total_today(),
        key_manager.list_keys(),
    )
    return {
        "stats":      stats,
        "summary":    {"total_today": today, "active_keys": len(keys)},
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }


@app.get("/api/dashboard/keys", tags=["Dashboard"])
async def list_keys(_: str = Depends(_require_auth)):
    return {"keys": await key_manager.list_keys()}


class CreateKeyRequest(BaseModel):
    name:       str
    rate_limit: int = 60


@app.post("/api/dashboard/keys", tags=["Dashboard"])
async def create_key(body: CreateKeyRequest, _: str = Depends(_require_auth)):
    try:
        raw = await key_manager.generate_key(body.name, body.rate_limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "name":       body.name,
        "key":        raw,
        "rate_limit": body.rate_limit,
        "warning":    "Copy this key now – it will NOT be shown again.",
    }


@app.delete("/api/dashboard/keys/{name}", tags=["Dashboard"])
async def delete_key(name: str, _: str = Depends(_require_auth)):
    if await key_manager.delete_key(name):
        return {"message": f"Key '{name}' deleted."}
    raise HTTPException(status_code=404, detail=f"No key named '{name}'.")


# ── Entry point  (identical to original) ──────────────────────────────────── #

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host    = "0.0.0.0",
        port    = 8000,
        reload  = False,
        workers = 1,
    )