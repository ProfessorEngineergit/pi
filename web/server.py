"""
pi — web backend (FastAPI).

Serves the frontend plus a small API:

    GET  /api/status        where the never-ending computation currently stands
    GET  /api/meta          this server's machine + build info
    GET  /api/digits        a slice of pi's digits (seek-based)
    GET  /api/search        find a digit sequence in pi (mmap)
    GET  /api/source        the engine's source code
    GET  /api/leaderboard   the benchmark ranking of all machines
    POST /api/benchmark     submit a visitor's benchmark result

Run:
    python3 server.py                      # http://127.0.0.1:8000
    HOST=0.0.0.0 PORT=8000 python3 server.py
"""
import os
import json
import mmap
import time
import threading
import subprocess
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent          # .../pi/web
PROJECT = BASE.parent                            # .../pi
STATIC = BASE / "static"
SOURCE_FILE = BASE / "worker.py"                 # the live streamer shown in the UI

# Shared data (worker + web). Configurable for container/volume deployment.
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE / "_data"))
STATUS_FILE = Path(os.environ.get("STATUS_FILE", DATA_DIR / "status.json"))
DATASET_FILE = Path(os.environ.get("DATASET_FILE", DATA_DIR / "pi_current.txt"))
LEADERBOARD_FILE = Path(os.environ.get("LEADERBOARD_FILE", DATA_DIR / "leaderboard.json"))
LOCAL_FALLBACK = PROJECT / "pi_100m.txt"         # optional local dataset for dev

REPO_URL = os.environ.get("REPO_URL", "https://github.com/ProfessorEngineergit/pi")
# Digits every browser computes for the benchmark, so results are comparable.
BENCH_DIGITS = int(os.environ.get("BENCH_DIGITS", "100000"))

_lock = threading.Lock()


def dataset_path():
    """The pi file the site shows: the worker's latest, else local, else reference."""
    for p in (DATASET_FILE, LOCAL_FALLBACK):
        if p.exists():
            return p
    return None


def frac_offset(path: Path) -> int:
    """Byte offset where fractional digits begin ('3.' -> 2, '3' -> 1)."""
    try:
        with open(path, "rb") as f:
            head = f.read(2)
        return 2 if head[:2] == b"3." else 1
    except OSError:
        return 1


def _detect_cpu():
    # x86 exposes "model name"; ARM (Raspberry Pi) has no such line, so fall back
    # to the board "Model" / "Hardware" fields instead.
    try:
        fields = {}
        with open("/proc/cpuinfo") as f:
            for line in f:
                if ":" not in line:
                    continue
                key, val = line.split(":", 1)
                fields.setdefault(key.strip().lower(), val.strip())
        for key in ("model name", "model", "hardware", "processor"):
            val = fields.get(key)
            if val and not val.isdigit():
                return val
    except OSError:
        pass
    try:
        out = subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            stderr=subprocess.DEVNULL).decode().strip()
        if out:
            return out
    except Exception:
        pass
    return "Unbekannte CPU"


CPU_BRAND = _detect_cpu()
LOGICAL_CORES = os.cpu_count() or 1
CORES = int(os.environ.get("WORKERS", "0") or 0) or LOGICAL_CORES


def total_digits() -> int:
    p = dataset_path()
    return max(0, p.stat().st_size - frac_offset(p)) if p else 0


app = FastAPI(title="pi", docs_url="/api/docs")


@app.get("/api/status")
def status():
    if STATUS_FILE.exists():
        try:
            return JSONResponse(json.loads(STATUS_FILE.read_text()))
        except (OSError, ValueError):
            pass
    return {"state": "offline", "mode": "spigot", "iteration": 0,
            "current_digit": 0, "reset_limit": 0, "blocks_verified": 0,
            "status_text": "Streamer gerade offline", "recent": "",
            "dataset_digits": total_digits()}


@app.get("/api/meta")
def meta():
    ds = dataset_path()
    return {
        "repo": REPO_URL,
        "total_digits": total_digits(),
        "pi_file": ds.name if ds else None,
        "has_pi_file": ds is not None,
        "cpu": CPU_BRAND,
        "cores": CORES,
        "logical_cores": LOGICAL_CORES,
        "bench_digits": BENCH_DIGITS,
        "pi_delivery": "https://pi.delivery",
    }


@app.get("/api/digits")
def digits(start: int = Query(0, ge=0), count: int = Query(500, ge=1, le=50000)):
    p = dataset_path()
    td = total_digits()
    if not p or td == 0:
        return JSONResponse({"error": "no dataset available yet"}, status_code=404)
    if start >= td:
        return {"start": start, "count": 0, "digits": "", "total": td}
    count = min(count, td - start)
    off = frac_offset(p)
    with open(p, "rb") as f:
        f.seek(off + start)
        data = f.read(count)
    return {"start": start, "count": len(data),
            "digits": data.decode("ascii"), "total": td}


@app.get("/api/search")
def search(q: str = Query(..., min_length=1, max_length=64)):
    if not q.isdigit():
        return JSONResponse({"error": "query must be digits only"}, status_code=400)
    p = dataset_path()
    if not p:
        return JSONResponse({"error": "no dataset available yet"}, status_code=404)
    off = frac_offset(p)
    needle = q.encode("ascii")
    with open(p, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            idx = mm.find(needle, off)
            context = ""
            if idx != -1:
                cs = max(off, idx - 12)
                ce = min(len(mm), idx + len(needle) + 12)
                context = mm[cs:ce].decode("ascii")
        finally:
            mm.close()
    if idx == -1:
        return {"found": False, "query": q, "total": total_digits()}
    return {"found": True, "query": q, "position": idx - off,
            "context": context, "total": total_digits()}


@app.get("/api/source")
def source():
    if not SOURCE_FILE.exists():
        return JSONResponse({"error": "source not found"}, status_code=404)
    return PlainTextResponse(SOURCE_FILE.read_text(), media_type="text/plain")


# ───────────────────────── benchmark leaderboard ─────────────────────────
class Specs(BaseModel):
    cores: int | None = None
    memory: float | None = None
    gpu: str | None = None
    platform: str | None = None
    browser: str | None = None


class BenchSubmit(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    seconds: float = Field(gt=0, lt=100000)
    digits: int = Field(gt=0, lt=10_000_000)
    cid: str = Field(default="", max_length=64)   # per-browser id, not the name
    specs: Specs = Specs()


def _clean_name(s: str) -> str:
    s = "".join(c for c in s.strip() if c.isalnum() or c in " _-.äöüÄÖÜß")
    return (s[:24] or "anonym").strip()


def _short_gpu(s):
    if not s:
        return None
    s = str(s)[:80]
    # trim verbose "ANGLE (Vendor, Renderer Direct3D...)" to the renderer
    if "ANGLE" in s and "," in s:
        parts = s.split(",")
        if len(parts) >= 2:
            s = parts[1].strip().split(" (")[0]
    return s[:48]


def _load_board():
    if LEADERBOARD_FILE.exists():
        try:
            return json.loads(LEADERBOARD_FILE.read_text())
        except (OSError, ValueError):
            pass
    return []


def _save_board(entries):
    LEADERBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEADERBOARD_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries))
    tmp.replace(LEADERBOARD_FILE)


def _ranked(entries):
    # Only the standard workload is comparable. Keep the best (fastest) entry per
    # DEVICE (cid), not per name, so two people with the same name don't overwrite
    # each other and nobody can clobber someone else's result by reusing a name.
    std = [e for e in entries if e.get("digits") == BENCH_DIGITS]
    best = {}
    for i, e in enumerate(std):
        key = e.get("cid") or f"_anon{i}"        # entries without a cid stay distinct
        if key not in best or e["seconds"] < best[key]["seconds"]:
            best[key] = e
    return sorted(best.values(), key=lambda e: e["seconds"])


@app.get("/api/leaderboard")
def leaderboard(limit: int = Query(100, ge=1, le=500)):
    ranked = _ranked(_load_board())
    for i, e in enumerate(ranked):
        e["rank"] = i + 1
    return {"bench_digits": BENCH_DIGITS, "count": len(ranked),
            "entries": ranked[:limit]}


@app.post("/api/benchmark")
def benchmark(sub: BenchSubmit):
    entry = {
        "username": _clean_name(sub.username),
        "cid": sub.cid[:64],
        "seconds": round(sub.seconds, 3),
        "digits": sub.digits,
        "score": round(sub.digits / sub.seconds),
        "specs": {
            "cores": sub.specs.cores,
            "memory": sub.specs.memory,
            "gpu": _short_gpu(sub.specs.gpu),
            "platform": (sub.specs.platform or "")[:40] or None,
            "browser": (sub.specs.browser or "")[:40] or None,
        },
        "ts": int(time.time()),
    }
    with _lock:
        entries = _load_board()
        entries.append(entry)
        # keep the file bounded: latest 2000 raw submissions
        if len(entries) > 2000:
            entries = entries[-2000:]
        _save_board(entries)
        ranked = _ranked(entries)
    rank = next((i + 1 for i, e in enumerate(ranked)
                 if (e.get("cid") or "") == entry["cid"]
                 and e["seconds"] == entry["seconds"]), None)
    return {"ok": True, "you": entry, "rank": rank, "total": len(ranked),
            "bench_digits": BENCH_DIGITS}


# Static frontend (mounted last so /api/* takes precedence).
app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print(f"\n  pi  ->  http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="info")
