# pi — single image used for both the web server and the worker.
# Build context must be the repo root (see docker-compose.yml).
FROM python:3.12-slim

# GMP / MPFR / MPC dev libs so gmpy2 links against fast C arithmetic.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libgmp-dev libmpfr-dev libmpc-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY web/requirements.txt /app/web/requirements.txt
RUN pip install --no-cache-dir -r /app/web/requirements.txt

# Engine + web app. The dataset is produced by the worker at runtime and the
# verification reference is fetched into the volume, so neither is baked in.
COPY engine/ /app/engine/
COPY web/ /app/web/

ENV DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8000 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Default = web server; the worker service overrides this command.
CMD ["python", "/app/web/server.py"]
