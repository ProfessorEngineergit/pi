# pi — single lightweight image for the web server and the streamer.
# Pure Python (no native deps), so it builds fast even on a Raspberry Pi.
# Build context must be the repo root (see docker-compose.yml).
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY web/requirements.txt /app/web/requirements.txt
RUN pip install --no-cache-dir -r /app/web/requirements.txt

# The web app + the streamer. The current run lives in the data volume at
# runtime; verification happens live against the pi.delivery API.
COPY web/ /app/web/

ENV DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8000 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Default = web server; the worker service overrides this command.
CMD ["python", "/app/web/server.py"]
