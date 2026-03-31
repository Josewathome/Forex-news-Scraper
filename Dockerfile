# ─────────────────────────────────────────────
# Stage 1 — Python deps (cached layer)
# ─────────────────────────────────────────────
FROM python:3.12-slim AS deps

WORKDIR /app

# System libs needed to compile some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt


# ─────────────────────────────────────────────
# Stage 2 — Playwright + Chromium install
# ─────────────────────────────────────────────
FROM deps AS playwright

# Force HTTPS (fix network blocks)
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list

RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxcb1 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    fonts-liberation \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Install only Chromium (skip Firefox/WebKit to keep image small)
RUN playwright install chromium


# ─────────────────────────────────────────────
# Stage 3 — Final runtime image
# ─────────────────────────────────────────────
FROM playwright AS runtime

WORKDIR /app

# Copy application source
COPY . .

# Non-root user for security
RUN useradd --create-home --shell /bin/bash scraper \
 && chown -R scraper:scraper /app

# Playwright stores browser binaries here — make sure our user can reach them
ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

USER scraper

EXPOSE 8000

# Uvicorn: single worker (browser lives in one process — never use multiple workers)
CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--log-level", "info", \
     "--no-access-log"]