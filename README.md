# Forex News Scraper (forex-api)

A FastAPI service that scrapes the ForexFactory and MyFxBook economic calendars and live broker spreads with Playwright, caches the results, and serves them over an API-key-protected, rate-limited REST API. My MT5 trading bots query it before opening trades around high-impact news.

## Features

- **Economic calendars:** ForexFactory and MyFxBook events filtered by currency and date, with impact, actual, forecast and previous values.
- **Broker spreads:** live spreads by broker and symbol.
- **API keys:** created from a dashboard, stored encrypted (via my [`securepipe`](https://pypi.org/project/securepipe/) library, with a Fernet fallback), each with its own per-minute rate limit.
- **Dashboard:** JWT login with login rate limiting, request stats, and key management.
- **Persistent browser:** one Chromium instance reused across requests, with recovery when a page crashes.
- **Caching:** in-memory TTL cache (broker spreads: 5 minutes) and SQLite request logs with retention cleanup.

## Endpoints

All data endpoints need an API key in the `X-API-Key` header.

| Method | Path | Query parameters |
|---|---|---|
| GET | `/forexfactory/events` | `currency` (repeatable), `date` |
| GET | `/myfxbook/events` | `currency` (repeatable), `start_date`, `end_date` |
| GET | `/broker-spreads/live` | `broker` (repeatable), `symbol` (repeatable) |
| GET | `/broker-spreads/all` | `symbol` (repeatable) |
| GET | `/health` | — |

Dashboard routes: `/` (dashboard), `/api-docs`, `POST /auth/login`, and `/api/dashboard/stats` and `/api/dashboard/keys` (Bearer token from login). Example responses are in [`API_DOC.md`](API_DOC.md).

```bash
curl -H "X-API-Key: <your key>" \
  "http://localhost:8087/myfxbook/events?currency=USD&start_date=2026-03-10&end_date=2026-03-11"
```

## Configuration

| Variable | Purpose |
|---|---|
| `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD` | Dashboard login |
| `JWT_SECRET` | Signs dashboard login tokens |
| `ENCRYPTION_KEY` | Encrypts stored API keys |
| `JWT_EXPIRY_HOURS` | Dashboard token lifetime (default 24) |
| `LOG_RETENTION_DAYS` | Request-log retention (default 3) |
| `LOGIN_RATE_LIMIT`, `LOGIN_RATE_WINDOW` | Login attempt limit |
| `DATA_DIR`, `LOGS_DIR` | SQLite and log locations |

Generate `JWT_SECRET` and `ENCRYPTION_KEY` with `python generate_secrets.py`. Never use the placeholder defaults in `docker-compose.yml` outside local testing.

## Running

### Docker

```bash
docker compose up -d --build
# API and dashboard: http://localhost:8087
```

### Local (tested on Ubuntu 24.04 / WSL, Python 3.12)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt PyJWT   # PyJWT is needed by main.py but missing from requirements.txt
playwright install --with-deps chromium
python main.py                          # serves on http://localhost:8000
```

If Chromium fails to start on Ubuntu 24.04, install its system libraries (`libnss3`, `libasound2t64`, `libgbm1`, `libgtk-3-0t64` and others); `playwright install --with-deps` does this for you.

## Deployment

`.github/workflows/deploy.yml` runs on a self-hosted runner: it syncs the repo to the server and rebuilds the container with `docker compose`.

## Limitations

- No automated tests.
- Scraping depends on each site's page structure and may break when it changes; check each site's terms of use before relying on it.
- The trading bots expect a shared Docker network named `forex-shared`, which this compose file does not create.

## Author

Joseph Wathome
