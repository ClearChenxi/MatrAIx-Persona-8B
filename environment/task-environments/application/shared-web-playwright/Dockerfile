FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app

# Browsers are already in the base image. Any python the agent launches
# must see that install — do not make the task instruction mention paths.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

# OpenHands install tries apt + pip at trial start. Nested Docker DNS is
# unreliable, so bake the SDK here. --system-site-packages keeps the image
# Playwright visible to the venv; also install onto /usr/bin/python because
# OpenHands' terminal tool uses that interpreter, not the venv.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3.12-venv \
    && python3.12 -m venv --system-site-packages /opt/openhands-sdk-venv \
    && /opt/openhands-sdk-venv/bin/pip install --upgrade pip \
    && /opt/openhands-sdk-venv/bin/pip install openhands-sdk openhands-tools fastapi \
    && for py in python python3 /usr/bin/python /usr/bin/python3; do \
         if command -v "$py" >/dev/null 2>&1; then \
           "$py" -m pip install --disable-pip-version-check playwright==1.49.1 \
             || "$py" -m pip install --disable-pip-version-check --break-system-packages playwright==1.49.1 \
             || true; \
         fi; \
       done \
    && python -c "from playwright.sync_api import sync_playwright" \
    && python3 -c "from playwright.sync_api import sync_playwright" \
    && /usr/bin/python -c "from playwright.sync_api import sync_playwright" \
    && /opt/openhands-sdk-venv/bin/python -c "from playwright.sync_api import sync_playwright" \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app/input /app/output
