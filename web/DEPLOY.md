# pi — Deployment

A web app plus a **never-ending worker** that keeps computing more digits of π,
verifies each milestone against an auto-downloaded reference, and shows live
where it stands. Visitors can also benchmark their own machine (in their browser)
and land on a leaderboard.

## Quick start (Docker)

```bash
cd web
docker compose up -d --build
docker compose logs -f worker        # watch it crunch  →  http://<server>:8000
```

Two containers share one volume (`pidata` → `/data`):

| Service | Role |
|---------|------|
| `web`    | serves the site + API (`/api/status`, `/api/digits`, `/api/search`, `/api/source`, `/api/leaderboard`, `/api/benchmark`) |
| `worker` | computes π forever, writes `/data/status.json`, publishes `/data/pi_latest.txt`, caches the reference in `/data/reference/` |

The leaderboard lives in `/data/leaderboard.json`.

## Worker settings (env in `docker-compose.yml`)

| Var | Default | Meaning |
|-----|---------|---------|
| `START_DIGITS` | `1000000` | first milestone (then 1-2-5 ladder: 1M, 2M, 5M, 10M, …) |
| `MAX_DIGITS` | `0` | `0` = run forever; else stop after this |
| `WORKERS` | `0` | `0` = all cores; set e.g. `16` to pin |
| `CHUNKS_PER_WORKER` | `4` | parallel load-balancing granularity |
| `VERIFY` | `1` | verify each milestone against the reference |
| `BENCH_DIGITS` | `100000` | digits every browser computes for the benchmark |
| `REPO_URL` | github… | link used by the site's GitHub buttons |
| `PI_REF_SEGMENT_TEMPLATE` | – | URL template to verify **beyond 1e9** (see below) |

## Verification reference

- **≤ 1e9 digits:** the worker downloads the MIT `pi-billion.txt` once (md5-checked)
  into the volume and verifies every milestone against it.
- **> 1e9 digits:** set `PI_REF_SEGMENT_TEMPLATE`, e.g.
  `https://your-host/pi_{start}_{end}.txt`; segments are appended to grow the
  reference. Without it, the first 1e9 digits are verified and the rest reported
  as "reference covers first 1e9".

> Heads up: the very first milestone triggers a one-time ~1 GB reference download.

## Without Docker

```bash
pip install -r requirements.txt          # needs system libgmp/mpfr/mpc-dev
DATA_DIR=/srv/pi python3 worker.py &
DATA_DIR=/srv/pi HOST=0.0.0.0 python3 server.py
```

## Performance on a tower / VM (Proxmox)

π is **CPU + RAM + storage** bound — a GPU does nothing here.

- **CPU type `host`** (exposes AVX2 etc.), give the VM all real cores.
- **RAM fixed, ballooning OFF.** 1e9 digits ≈ 1 GB output + 1 GB reference + working memory.
- **Storage:** NVMe PCIe passthrough, or virtio-scsi-single + iothread.
- **NUMA on** for multi-node; consider CPU pinning + hugepages.
- **BIOS:** Maximum Performance / Turbo on — avoid throttling under sustained load.

Set `WORKERS` to taste (`nproc` shows cores); the sweet spot is usually the
physical-core count, not every hyperthread.
