# pi

A server that computes the digits of **π** forever, verifies every milestone
against a reference it fetches itself, and shows live where it currently stands —
plus a browser benchmark with a leaderboard so visitors can race their machines.

- **engine/pi.py** — the parallel Chudnovsky + binary-splitting engine (gmpy2 + GMP).
- **web/server.py** — FastAPI: status, digit slices, search, source, leaderboard.
- **web/worker.py** — the never-ending worker; computes ever-larger targets,
  verifies each, writes `status.json`, publishes the latest digits.
- **web/download_reference.py** — auto-downloads / caches the verification reference.
- **web/static/** — the frontend (Three.js, no build step).

## Run it

```bash
cd web
docker compose up -d --build
docker compose logs -f worker      # → http://localhost:8000
```

Without Docker:

```bash
pip install -r web/requirements.txt          # needs system libgmp/mpfr/mpc-dev
DATA_DIR=./data python3 web/worker.py &       # the endless computation
DATA_DIR=./data HOST=0.0.0.0 python3 web/server.py
```

See **web/DEPLOY.md** for server deployment and performance notes.

## The benchmark

Visitors compute a fixed number of π digits **in their own browser** (BigInt,
client-side — nothing runs on the server) and submit their time to
`/api/benchmark`. The leaderboard ranks all machines.
