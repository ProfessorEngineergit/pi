# pi

A small home server that streams the digits of **π** forever, checks every block
of 1000 digits live against the official pi.delivery database, and shows in real
time where it currently is. Plus a browser benchmark with a leaderboard so
visitors can race their machines.

It is deliberately lightweight (pure Python, almost no RAM), so it runs happily
on a Raspberry Pi behind a Cloudflare tunnel.

- **web/worker.py** — the streamer. A spigot algorithm emits π digit by digit
  and throws old digits away, verifies each 1000-digit block against pi.delivery,
  and after a limit jumps back to digit 0 and counts the round.
- **web/server.py** — FastAPI: live status, digit slices, search, source, leaderboard.
- **web/static/** — the frontend (Three.js, no build step): live ticker,
  explorer, random walk, and the in-browser benchmark.

## Run it

```bash
cd web
docker compose up -d --build
docker compose logs -f worker        # → http://localhost:8000
```

Without Docker:

```bash
pip install -r web/requirements.txt
DATA_DIR=./data python3 web/worker.py &     # the endless streamer
DATA_DIR=./data HOST=0.0.0.0 python3 web/server.py
```

Settings live in `web/docker-compose.yml`: `RESET_LIMIT` (digits per round),
`BLOCK` (verify chunk size), `VERIFY`, `BENCH_DIGITS`, `REPO_URL`.

## The benchmark

Visitors compute a fixed number of π digits **in their own browser** (BigInt,
client-side, nothing on the server), the time is measured to the millisecond,
and the result goes on the leaderboard. Each device keeps its own best result,
so identical names never overwrite each other.
