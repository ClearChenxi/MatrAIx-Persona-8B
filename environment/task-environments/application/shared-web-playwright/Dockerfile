FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app

# OpenHands install tries apt + pip at trial start. Nested Docker DNS is
# unreliable, so bake venv and the SDK here (warmup uses --network=host).
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3.12-venv \
    && python3.12 -m venv /opt/openhands-sdk-venv \
    && /opt/openhands-sdk-venv/bin/pip install --upgrade pip \
    && /opt/openhands-sdk-venv/bin/pip install openhands-sdk openhands-tools fastapi \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app/input /app/output
