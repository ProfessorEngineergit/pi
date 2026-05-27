"""
The endless pi streamer (spigot).

Generates the digits of pi one at a time with a streaming spigot algorithm
(Gibbons), which needs almost no memory because it throws old digits away as it
goes. Perfect for a small home server / Raspberry Pi.

What it does, on a loop, forever:
  * stream digits, collecting them into blocks of exactly 1000,
  * check every finished block live against the official pi.delivery API,
  * after a configurable limit (e.g. 100k or 1M digits) it stops, jumps back to
    digit 0 and starts over, counting the round (iteration),
  * write a small current-run file + status.json so the website can show, in
    real time, exactly which digit it is on and which round it is in.

No giant files are kept on disk; only the current run (at most RESET_LIMIT
digits) lives in a small file so the site can show and explore it.
"""
import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone

import requests

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent

DATA_DIR = Path(os.environ.get("DATA_DIR", HERE / "_data"))
STATUS_FILE = Path(os.environ.get("STATUS_FILE", DATA_DIR / "status.json"))
DATASET_FILE = Path(os.environ.get("DATASET_FILE", DATA_DIR / "pi_current.txt"))

RESET_LIMIT = int(os.environ.get("RESET_LIMIT", "20000000000"))  # ~20 GB of digits per round
BLOCK = int(os.environ.get("BLOCK", "1000"))                 # verify chunk size
VERIFY = os.environ.get("VERIFY", "1") not in ("0", "false", "False", "")
PI_DELIVERY = os.environ.get("PI_DELIVERY", "https://api.pi.delivery/v1/pi")
RECENT_TAIL = 90


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pi_spigot():
    """Gibbons' unbounded streaming spigot. Yields 3, 1, 4, 1, 5, 9, ... ."""
    q, r, t, k, n, l = 1, 0, 1, 1, 3, 3
    while True:
        if 4 * q + r - t < n * t:
            yield n
            nr = 10 * (r - n * t)
            n = ((10 * (3 * q + r)) // t) - 10 * n
            q *= 10
            r = nr
        else:
            nr = (2 * q + r) * l
            nn = (q * (7 * k + 2) + r * l) // (t * l)
            q *= k
            t *= l
            l += 2
            k += 1
            n = nn
            r = nr


def verify_block(start: int, digits: str):
    """Compare a 1000-digit block against pi.delivery. Returns True/False/None."""
    try:
        resp = requests.get(PI_DELIVERY,
                            params={"start": start, "numberOfDigits": len(digits)},
                            timeout=15)
        resp.raise_for_status()
        ref = resp.json().get("content", "")
        return ref == digits
    except requests.RequestException:
        return None


class Status:
    def __init__(self):
        self.d = {
            "mode": "spigot",
            "state": "starting",
            "iteration": 1,
            "current_digit": 0,
            "reset_limit": RESET_LIMIT,
            "blocks_verified": 0,
            "verify_ok": None,
            "verify_enabled": VERIFY,
            "status_text": "startet…",
            "rate_dps": 0.0,
            "recent": "",
            "dataset_digits": 0,
            "started_at": now_iso(),
            "updated_at": now_iso(),
        }
        if STATUS_FILE.exists():
            try:
                old = json.loads(STATUS_FILE.read_text())
                self.d["iteration"] = old.get("iteration", 1)
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


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    st = Status()

    # ---- crash recovery: resume the current round where the file left off ----
    resume_from = 0
    if DATASET_FILE.exists():
        size = DATASET_FILE.stat().st_size
        resume_from = max(0, size - 2)          # minus the leading "3."
        resume_from = min(resume_from, RESET_LIMIT)

    gen = pi_spigot()
    next(gen)                                   # consume the integer part "3"
    fh = open(DATASET_FILE, "r+" if resume_from else "w")
    if not resume_from:
        fh.write("3.")
        fh.flush()
    else:
        fh.seek(2 + resume_from)
        fh.truncate()
        for _ in range(resume_from):            # fast-forward the spigot
            next(gen)

    pos = resume_from
    block = []
    buf = []
    recent = ""
    last_flush = 0.0
    t0 = time.time()
    last_rate_t, last_rate_pos = t0, pos
    st.set(state="streaming", current_digit=pos,
           status_text="berechnet Ziffern…", started_at=now_iso())

    while True:
        d = next(gen)
        ch = str(d)
        buf.append(ch)
        block.append(ch)
        pos += 1
        recent = (recent + ch)[-RECENT_TAIL:]

        if len(block) >= BLOCK:
            fh.write("".join(buf)); fh.flush(); buf = []
            # pi.delivery counts the leading "3" as index 0, our fractional
            # digit p sits at their start = p + 1.
            start = (pos - BLOCK) + 1
            ok = verify_block(start, "".join(block)) if VERIFY else None
            block = []
            if ok is True:
                st.d["blocks_verified"] += 1
                st.d["verify_ok"] = True
                st.d["status_text"] = "berechnet und via pi.delivery verifiziert"
            elif ok is False:
                st.d["verify_ok"] = False
                st.d["status_text"] = "ACHTUNG: Block stimmt nicht mit der Referenz überein"
            else:
                st.d["verify_ok"] = None
                st.d["status_text"] = "berechnet (Verifikation gerade nicht erreichbar)"

        now = time.time()
        if now - last_flush > 0.3:
            if buf:
                fh.write("".join(buf)); fh.flush(); buf = []
            dt = now - last_rate_t
            if dt >= 1.0:
                st.d["rate_dps"] = round((pos - last_rate_pos) / dt)
                last_rate_t, last_rate_pos = now, pos
            st.set(state="streaming", current_digit=pos,
                   dataset_digits=pos, recent=recent)
            last_flush = now

        if pos >= RESET_LIMIT:
            if buf:
                fh.write("".join(buf)); fh.flush(); buf = []
            st.set(state="resetting",
                   status_text=f"Runde {st.d['iteration']} fertig bei {pos} Stellen, fange wieder bei 0 an",
                   current_digit=pos, dataset_digits=pos)
            time.sleep(1.0)
            # back to digit 0, next round
            st.d["iteration"] += 1
            gen = pi_spigot(); next(gen)
            fh.seek(0); fh.truncate(); fh.write("3."); fh.flush()
            pos = 0; block = []; buf = []; recent = ""
            t0 = time.time(); last_rate_t, last_rate_pos = t0, 0
            st.set(state="streaming", current_digit=0, dataset_digits=0,
                   blocks_verified=0, recent="", started_at=now_iso(),
                   status_text="neue Runde gestartet")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
