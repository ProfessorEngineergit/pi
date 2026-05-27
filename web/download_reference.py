"""
Automatic pi reference manager.

Downloads / caches a reference of pi's decimal expansion so the worker can
verify every milestone it computes.

  * up to 1,000,000,000 digits: the MIT "pi-billion.txt" file (md5-checked),
    cached in a volume so it is fetched only once.
  * beyond 1,000,000,000 digits: optional segmented fetching driven by the
    env var  PI_REF_SEGMENT_TEMPLATE  (e.g.
    "https://host/pi_{start}_{end}.txt"); segments are appended to grow the
    reference. If no template is configured we cap at 1e9 and the worker
    verifies the overlapping prefix + falls back to an internal consistency
    check for the digits beyond.

The reference file on disk is stored WITHOUT a decimal point: a leading "3"
followed by the fractional digits (this is the MIT format). Helpers below
always reason in "fractional digits available".

CLI:
    python download_reference.py --digits 1000000 --out /data/reference/pi_ref.txt
"""
import os
import sys
import argparse
import hashlib
from pathlib import Path

import requests

MIT_PI_BILLION_URL = "https://stuff.mit.edu/afs/sipb/contrib/pi/pi-billion.txt"
MIT_PI_BILLION_MD5_URL = "https://stuff.mit.edu/afs/sipb/contrib/pi/pi-billion.md5"
MIT_LIMIT = 1_000_000_000

SEGMENT_TEMPLATE = os.environ.get("PI_REF_SEGMENT_TEMPLATE", "").strip()


def log(msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def available_digits(path: Path) -> int:
    """Fractional digits available in a reference file (leading '3' excluded)."""
    if not path.exists():
        return 0
    size = path.stat().st_size
    # tolerate an optional "." after the 3
    head = path.read_bytes()[:2] if size >= 2 else b""
    offset = 2 if head[:2] == b"3." else 1
    return max(0, size - offset)


def _download(url: str, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with tmp.open("wb") as f:
            for chunk in r.iter_content(chunk_size=4 * 1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if total:
                    log(f"\r  {target.name}: {done*100/total:6.2f}%")
                else:
                    log(f"\r  {target.name}: {done/1048576:,.1f} MB")
    tmp.replace(target)


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_reference(min_digits: int, out: Path, check_md5: bool = True) -> int:
    """
    Make sure `out` holds at least `min_digits` reference fractional digits.
    Returns the number of fractional digits actually available.
    """
    out = Path(out)
    have = available_digits(out)
    if have >= min(min_digits, MIT_LIMIT):
        # base reference already sufficient (segment growth handled below)
        if min_digits <= have:
            return have

    if have < min(min_digits, MIT_LIMIT):
        log("Fetching MIT pi-billion reference (one-time, ~1 GB)…")
        _download(MIT_PI_BILLION_URL, out)
        if check_md5:
            try:
                md5_path = out.with_suffix(".md5")
                _download(MIT_PI_BILLION_MD5_URL, md5_path)
                expected = md5_path.read_text(errors="ignore").split()[0].strip().lower()
                actual = _md5(out).lower()
                if expected and expected != actual:
                    out.unlink(missing_ok=True)
                    raise SystemExit(f"MD5 mismatch (expected {expected}, got {actual})")
                log(f"  md5 OK: {actual}")
            except requests.RequestException:
                log("  (md5 file unavailable, skipping checksum)")
        have = available_digits(out)

    if min_digits > MIT_LIMIT:
        have = _grow_segments(out, min_digits, have)

    return have


def _grow_segments(out: Path, min_digits: int, have: int) -> int:
    """Append reference segments beyond 1e9 if a segment template is configured."""
    if not SEGMENT_TEMPLATE:
        log(f"  >1e9 requested but PI_REF_SEGMENT_TEMPLATE not set; "
            f"reference capped at {have} digits (prefix verification only).")
        return have
    step = int(os.environ.get("PI_REF_SEGMENT_SIZE", "100000000"))
    with out.open("ab") as f:
        while have < min_digits:
            start, end = have + 1, have + step
            url = SEGMENT_TEMPLATE.format(start=start, end=end)
            log(f"  fetching segment {start}-{end} from {url}")
            tmp = out.with_suffix(f".seg")
            try:
                _download(url, tmp)
            except requests.RequestException as e:
                log(f"  segment fetch failed ({e}); stopping at {have} digits")
                break
            data = tmp.read_bytes().strip()
            f.write(data)
            tmp.unlink(missing_ok=True)
            have += len(data)
    return available_digits(out)


def main():
    ap = argparse.ArgumentParser(description="Download/cache a pi reference for verification.")
    ap.add_argument("--digits", type=int, required=True)
    ap.add_argument("--out", default="/data/reference/pi_ref.txt")
    ap.add_argument("--no-md5", action="store_true")
    args = ap.parse_args()
    have = ensure_reference(args.digits, Path(args.out), check_md5=not args.no_md5)
    print(have)


if __name__ == "__main__":
    main()
