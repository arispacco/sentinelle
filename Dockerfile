FROM python:3.12-slim

WORKDIR /app

# System dependencies for Python build & Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    libglib2.0-0 libnss3 libnspr4 libdbus-1-3 libatk1.0-0 \
    libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
    libasound2 libxshmfence1 libx11-xcb1 libxcb1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install chromium

# Copy application source
COPY . .

# Ensure data directory exists
RUN mkdir -p /app/data

EXPOSE 8000

# sh -c : la forme exec n'expand pas $PORT. Repli 8000 en local, PORT injecté par Render.
CMD ["sh", "-c", "exec gunicorn web.server:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8000} --workers 1 --timeout 120 --access-logfile -"]
