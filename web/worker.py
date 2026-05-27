"""
Infinite pi worker.

Computes pi to an ever-growing target on a 1-2-5 ladder (1M, 2M, 5M, 10M, …),
forever. For every milestone it:

  * streams live progress into  status.json  (atomic writes) so the web UI can
    show exactly "where we currently stand",
  * verifies the result against an auto-downloaded reference (see
    download_reference.py),
  * publishes the latest verified digits as  pi_latest.txt  (the dataset the
    site explores / visualises).

Everything is driven by env vars (see DEPLOY.md). Designed to run as its own
container/process next to the web server, sharing one data volume.
"""
import os
import sys
import json
import time
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
import download_reference as refmod

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent

DATA_DIR = Path(os.environ.get("DATA_DIR", HERE / "_data"))
STATUS_FILE = Path(os.environ.get("STATUS_FILE", DATA_DIR / "status.json"))
DATASET_FILE = Path(os.environ.get("DATASET_FILE", DATA_DIR / "pi_latest.txt"))
REFERENCE_FILE = Path(os.environ.get("REFERENCE_FILE", DATA_DIR / "reference" / "pi_ref.txt"))
PI_SCRIPT = Path(os.environ.get("PI_SCRIPT", PROJECT / "engine" / "pi.py"))

START_DIGITS = int(os.environ.get("START_DIGITS", "1000000"))
MAX_DIGITS = int(os.environ.get("MAX_DIGITS", "0"))          # 0 = unbounded
WORKERS = int(os.environ.get("WORKERS", "0"))               # 0 = auto
CHUNKS_PER_WORKER = int(os.environ.get("CHUNKS_PER_WORKER", "4"))
VERIFY = os.environ.get("VERIFY", "1") not in ("0", "false", "False", "")
REST_SECONDS = float(os.environ.get("REST_SECONDS", "3"))

# rough serial baseline (s) for a "vs naive" estimate shown in the UI
_BASE_REF = (1e7, 6.374)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ladder_after(value: int) -> int:
    """Next target on a 1-2-5 ×10^k ladder strictly greater than `value`."""
    mant = [1, 2, 5]
    k = 6
    while True:
        for m in mant:
            t = m * (10 ** k)
            if t > value:
                return t
        k += 1


def estimate_serial(digits: int, turbo_seconds: float) -> float:
    # speedup grows mildly with size (measured 2.28x@1e7 .. 2.87x@1e8)
    import math
    x = max(7.0, min(8.0, math.log10(max(digits, 1))))
    return turbo_seconds * (2.28 + (2.87 - 2.28) * (x - 7.0))


class Status:
    def __init__(self):
        self.d = {
            "state": "starting",
            "current_target": None,
            "phase": None,
            "phase_label": None,
            "chunks_done": 0,
            "chunks_total": 0,
            "phase_times": {},
            "started_at": None,
            "elapsed": 0.0,
            "highest_verified": 0,
            "reference_digits": refmod.available_digits(REFERENCE_FILE),
            "dataset_file": str(DATASET_FILE) if DATASET_FILE.exists() else None,
            "dataset_digits": 0,
            "last_record": None,
            "history": [],
            "updated_at": now_iso(),
            "verify_enabled": VERIFY,
        }
        if DATASET_FILE.exists():
            self.d["dataset_digits"] = max(0, DATASET_FILE.stat().st_size - 2)
        # Recover after a restart/crash: carry over what was already achieved so
        # the worker continues from the last finished milestone instead of zero.
        if STATUS_FILE.exists():
            try:
                old = json.loads(STATUS_FILE.read_text())
                for k in ("highest_verified", "history", "last_record"):
                    if old.get(k):
                        self.d[k] = old[k]
            except (OSError, ValueError):
                pass

    def set(self, **kw):
        self.d.update(kw)
        self.flush()

    def flush(self):
        self.d["updated_at"] = now_iso()
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d))
        tmp.replace(STATUS_FILE)


PHASE_LABEL = {"bs": "Binary Splitting", "merge": "Merge", "sqrt": "Wurzel",
               "div": "Division", "str": "Konvertierung"}


def compute(target: int, st: Status, out_path: Path) -> dict:
    """Run pi.py --progress-json and stream progress into status. Returns 'done' event."""
    cmd = [sys.executable, str(PI_SCRIPT), "-d", str(target),
           "-o", str(out_path), "-c", str(CHUNKS_PER_WORKER), "--progress-json"]
    if WORKERS > 0:
        cmd += ["-w", str(WORKERS)]

    t0 = time.time()
    st.set(state="computing", current_target=target, phase=None, phase_times={},
           chunks_done=0, chunks_total=0, started_at=now_iso(), elapsed=0.0)

    done_ev = None
    proc = subprocess.Popen(cmd, cwd=str(PROJECT), stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1)
    last_flush = 0.0
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        et = ev.get("event")
        if et == "start":
            st.d["chunks_total"] = ev.get("chunks", 0)
        elif et == "chunk":
            st.d["chunks_done"] = ev.get("done", 0)
            st.d["chunks_total"] = ev.get("total", st.d["chunks_total"])
            st.d["phase"] = "bs"; st.d["phase_label"] = PHASE_LABEL["bs"]
        elif et == "phase":
            name = ev.get("name")
            st.d["phase_times"][name] = ev.get("seconds")
            st.d["phase"] = name
            st.d["phase_label"] = PHASE_LABEL.get(name, name)
        elif et == "done":
            done_ev = ev
        elif et == "error":
            raise RuntimeError(ev.get("message", "engine error"))
        st.d["elapsed"] = round(time.time() - t0, 2)
        now = time.time()
        if now - last_flush > 0.25:
            st.flush(); last_flush = now
    proc.wait()
    if proc.returncode != 0 and done_ev is None:
        raise RuntimeError(f"engine exited {proc.returncode}")
    st.set(elapsed=round(time.time() - t0, 2))
    return done_ev or {}


def verify(computed_path: Path, target: int, st: Status) -> dict:
    """Verify computed digits against the (auto-downloaded) reference."""
    result = {"verified": None, "matched": 0, "compared": 0, "source": "none"}
    if not VERIFY:
        return result
    st.set(state="verifying")
    try:
        avail = refmod.ensure_reference(target, REFERENCE_FILE)
    except Exception as e:
        result["source"] = f"reference unavailable ({e})"
        return result
    st.d["reference_digits"] = avail
    if avail <= 0:
        result["source"] = "no reference"
        return result

    limit = min(target, avail)
    matched = _compare_prefix(computed_path, REFERENCE_FILE, limit)
    result.update(verified=(matched >= limit), matched=matched,
                  compared=limit, source="reference")
    if target > avail:
        result["note"] = f"reference covers first {avail} digits"
    return result


def _frac_offset(path: Path) -> int:
    head = path.read_bytes()[:2] if path.stat().st_size >= 2 else b""
    return 2 if head[:2] == b"3." else 1


def _compare_prefix(a: Path, b: Path, limit: int) -> int:
    """Stream-compare the first `limit` fractional digits; return matched count."""
    matched = 0
    BLK = 4 * 1024 * 1024
    with a.open("rb") as fa, b.open("rb") as fb:
        fa.seek(_frac_offset(a)); fb.seek(_frac_offset(b))
        while matched < limit:
            need = min(BLK, limit - matched)
            ba, bb = fa.read(need), fb.read(need)
            if not ba or not bb:
                break
            n = min(len(ba), len(bb))
            i = 0
            while i < n and ba[i] == bb[i]:
                i += 1
            matched += i
            if i < n:
                break
    return matched


def publish_dataset(out_path: Path):
    """Atomically make the freshly computed result the public dataset."""
    DATASET_FILE.parent.mkdir(parents=True, exist_ok=True)
    os.replace(out_path, DATASET_FILE)


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "output").mkdir(parents=True, exist_ok=True)
    st = Status()
    st.set(state="starting")

    target = max(START_DIGITS, st.d["highest_verified"] and ladder_after(st.d["highest_verified"]) or START_DIGITS)

    while True:
        if MAX_DIGITS and target > MAX_DIGITS:
            st.set(state="finished")
            break
        out_path = DATA_DIR / "output" / f"pi_{target}.txt"
        try:
            done = compute(target, st, out_path)
            ver = verify(out_path, target, st)
            total_s = done.get("total_seconds", st.d["elapsed"])
            publish_dataset(out_path)

            record = {
                "digits": target,
                "seconds": total_s,
                "serial_estimate": round(estimate_serial(target, total_s), 1),
                "verified": ver["verified"],
                "matched": ver["matched"],
                "source": ver["source"],
                "finished_at": now_iso(),
                "preview": done.get("preview", "")[:1002],
            }
            st.d["last_record"] = record
            st.d["history"] = (st.d["history"] + [{
                "digits": target, "seconds": total_s, "verified": ver["verified"],
                "finished_at": record["finished_at"],
            }])[-30:]
            if ver["verified"] is not False:
                st.d["highest_verified"] = target
            st.d["dataset_file"] = str(DATASET_FILE)
            st.d["dataset_digits"] = max(0, DATASET_FILE.stat().st_size - 2)
            st.set(state="idle")

            target = ladder_after(target)
            time.sleep(REST_SECONDS)
        except Exception as e:
            st.set(state="error", error=str(e))
            time.sleep(10)
            # retry the same target


if __name__ == "__main__":
    main()
