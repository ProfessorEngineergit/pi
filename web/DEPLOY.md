# pi — Deployment

A lightweight web app plus a streaming worker that computes π digit by digit,
verifies each 1000-digit block against pi.delivery, and loops forever. Pure
Python, almost no RAM, made for a home server / Raspberry Pi behind Cloudflare.

## Quick start (Docker)

```bash
cd web
docker compose up -d --build
docker compose logs -f worker        # → http://<server>:8000
```

Two containers share one volume (`pidata` → `/data`):

| Service | Role |
|---------|------|
| `web`    | serves the site + API (`/api/status`, `/api/digits`, `/api/search`, `/api/source`, `/api/leaderboard`, `/api/benchmark`) |
| `worker` | the spigot streamer: writes `/data/status.json` + the current round to `/data/pi_current.txt` |

## Settings (env in `docker-compose.yml`)

| Var | Default | Meaning |
|-----|---------|---------|
| `RESET_LIMIT` | `100000` | digits per round, then back to 0 (keep small on a Pi) |
| `BLOCK` | `1000` | verify chunk size (pi.delivery returns at most 1000) |
| `VERIFY` | `1` | check each block against pi.delivery |
| `BENCH_DIGITS` | `15000` | digits every browser computes for the benchmark |
| `REPO_URL` | github… | link used by the site's GitHub buttons |

## How verification works

Every finished 1000-digit block is sent to `https://api.pi.delivery/v1/pi`
and compared. If it matches, the status says so; if not, it flags a mismatch.
No huge reference files are stored on disk.

## Cloudflare tunnel

Run `cloudflared` next to it and point a tunnel at `http://localhost:8000`:

```bash
cloudflared tunnel --url http://localhost:8000
```

…or wire it into your named tunnel's ingress so your domain reaches port 8000.

## Memory / Raspberry Pi

The spigot keeps almost nothing in memory and resets each round, so RAM use
stays tiny regardless of how long it runs. Lower `RESET_LIMIT` (e.g. `50000`) if
you want even smaller rounds. The image is pure Python, so it builds fast on ARM.

## Recovery

On restart the worker resumes the current round from where `pi_current.txt`
left off and keeps the round counter, so a reboot or crash doesn't lose its place.
