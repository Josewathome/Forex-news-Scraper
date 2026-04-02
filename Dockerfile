# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — Base (Playwright ready)
# ─────────────────────────────────────────────────────────────────────────────
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — Dependencies (cached layer)
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS deps

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — Runtime (clean + stable)
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS runtime

WORKDIR /app

# Copy installed packages
COPY --from=deps /usr/local /usr/local

# Copy application source
COPY . .

# Create persistent directories; these will be overridden by Docker volumes
RUN mkdir -p /app/data /app/logs

# Non-root user for security
RUN useradd --create-home --shell /bin/bash scraper \
 && chown -R scraper:scraper /app

USER scraper

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

EXPOSE 8000

# Single worker to keep the in-memory rate limiter consistent.
# Use Redis-backed rate limiting before scaling to multiple workers.
CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--log-level", "info"]