import sys
import os
import time
import math
import argparse
import subprocess
import concurrent.futures as cf
from concurrent.futures import ProcessPoolExecutor, as_completed

from tqdm import tqdm

# =============================================================================
# pi engine — Chudnovsky series + binary splitting, run in parallel.
#
# The idea in plain terms:
#   * Chudnovsky is the fastest known series for pi (~14 digits per term).
#   * Binary splitting evaluates it as one big balanced tree of fractions
#     instead of summing term by term, which keeps the huge multiplications fast.
#   * We cut that tree into chunks and hand each chunk to its own process, so all
#     CPU cores work at once. Even the merge, the square root and the final
#     base-10 conversion are parallelised.
#
# gmpy2 doesn't release the GIL on big multiplications, so threads wouldn't help
# here — real processes do. Every step is bit-for-bit reproducible.
#
#   python3 pi.py -d 100000000 -o pi.txt -v reference.txt
# =============================================================================

try:
    sys.set_int_max_str_digits(0)
except AttributeError:
    pass

try:
    import gmpy2
    from gmpy2 import mpz, isqrt
    HAS_GMP = True
except ImportError:
    print("=========================================================================")
    print("FATAL: 'gmpy2' not found. The turbo build requires gmpy2 (GMP arm64).")
    print("Install with:  pip install gmpy2")
    print("=========================================================================")
    mpz = int
    isqrt = math.isqrt
    HAS_GMP = False

# Chudnovsky constants
C1 = mpz(10939058860032000)
C2 = mpz(13591409)
C3 = mpz(545140134)

# Native-int copies for the fast leaf math
C1_int = 10939058860032000
C2_int = 13591409
C3_int = 545140134

# Terms per decimal digit (Chudnovsky adds ~14.18 digits per term)
DIGITS_PER_TERM = 14.181647462725477

# Below these, process-pool overhead outweighs the parallelism, so we fall back
# to the serial in-process path.
PARALLEL_MERGE_MIN_DIGITS = 1_000_000
PARALLEL_STR_MIN_DIGITS = 1_000_000


def perf_core_count():
    """Performance-core count on Apple Silicon; logical CPU count elsewhere."""
    try:
        n = int(subprocess.check_output(
            ["sysctl", "-n", "hw.perflevel0.physicalcpu"],
            stderr=subprocess.DEVNULL))
        if n > 0:
            return n
    except Exception:
        pass
    return os.cpu_count() or 1


# -----------------------------------------------------------------------------
# Serial binary splitting for one contiguous chunk [a, b).
# Native ints at the leaves (fast), promoted to mpz for the big combines.
# Identical math to pi_chudnovski.py's bs().
# -----------------------------------------------------------------------------
def bs_serial(a, b):
    diff = b - a
    if diff == 1:
        a2 = a * a
        a3 = a2 * a
        P = 5 - 46 * a + 108 * a2 - 72 * a3
        Q = C1_int * a3
        T = P * (C2_int + C3_int * a)
        return mpz(P), mpz(Q), mpz(T)
    if diff == 2:
        a2 = a * a
        a3 = a2 * a
        P1 = 5 - 46 * a + 108 * a2 - 72 * a3
        Q1 = C1_int * a3
        T1 = P1 * (C2_int + C3_int * a)

        b1 = a + 1
        b2 = b1 * b1
        b3 = b2 * b1
        P2 = 5 - 46 * b1 + 108 * b2 - 72 * b3
        Q2 = C1_int * b3
        T2 = P2 * (C2_int + C3_int * b1)

        P = P1 * P2
        Q = Q1 * Q2
        T = T1 * Q2 + P1 * T2
        return mpz(P), mpz(Q), mpz(T)

    m = (a + b) // 2
    P1, Q1, T1 = bs_serial(a, m)
    P2, Q2, T2 = bs_serial(m, b)
    return P1 * P2, Q1 * Q2, T1 * Q2 + P1 * T2


def combine(L, R):
    """Associative merge of two adjacent binary-splitting results."""
    P1, Q1, T1 = L
    P2, Q2, T2 = R
    return (P1 * P2, Q1 * Q2, T1 * Q2 + P1 * T2)


def merge_in_order(items):
    """
    Reduce a left-to-right ordered list of (P, Q, T) triples into one.
    Pairwise tree reduction keeps operand sizes balanced (best for FFT mult)
    while preserving index order (the combine is associative, not commutative).
    """
    while len(items) > 1:
        nxt = []
        for i in range(0, len(items) - 1, 2):
            nxt.append(combine(items[i], items[i + 1]))
        if len(items) % 2:
            nxt.append(items[-1])
        items = nxt
    return items[0]


def _mul_worker(args):
    return args[0] * args[1]


def parallel_merge(items, pool):
    """
    Same tree reduction as merge_in_order, but each level dispatches ALL of its
    independent multiplications to the process pool at once. A combine needs four
    products (P1*P2, Q1*Q2, T1*Q2, P1*T2), all independent -- so even the final
    top-of-tree combine keeps 4 cores busy, and lower levels saturate all of them.
    This attacks the giant serial multiplications that otherwise dominate.
    """
    while len(items) > 1:
        pairs_end = len(items) - (len(items) % 2)
        jobs = []
        for i in range(0, pairs_end, 2):
            (P1, Q1, T1), (P2, Q2, T2) = items[i], items[i + 1]
            jobs.append((P1, P2))   # -> P
            jobs.append((Q1, Q2))   # -> Q
            jobs.append((T1, Q2))   # -> T part 1
            jobs.append((P1, T2))   # -> T part 2
        res = list(pool.map(_mul_worker, jobs))
        nxt = []
        for k in range(pairs_end // 2):
            nxt.append((res[4 * k], res[4 * k + 1],
                        res[4 * k + 2] + res[4 * k + 3]))
        if len(items) % 2:
            nxt.append(items[-1])
        items = nxt
    return items[0]


def _divmod_worker(args):
    val, low = args
    return divmod(val, mpz(10) ** low)


def _leaf_worker(args):
    val, width = args
    return val.digits(10).rjust(width, '0')


def parallel_to_decimal(X, pool, depth):
    """
    Convert a huge integer to its exact decimal string across all cores.

    Repeatedly split each block into a high/low half via divmod by 10^k (the
    splits at one level are independent -> dispatched together), then convert the
    2^depth leaf blocks to fixed-width zero-padded strings in parallel and join.
    num_digits gives the exact length so every block's width is exact.
    """
    nd = gmpy2.num_digits(X, 10)
    leaves = [(X, nd)]
    for _ in range(depth):
        jobs, meta = [], []
        for (val, w) in leaves:
            low = w // 2
            jobs.append((val, low))
            meta.append((w, low))
        outs = list(pool.map(_divmod_worker, jobs))
        nxt = []
        for (hi, lo), (w, low) in zip(outs, meta):
            nxt.append((hi, w - low))
            nxt.append((lo, low))
        leaves = nxt
    return "".join(pool.map(_leaf_worker, leaves))


def make_ranges(lo, hi, n_chunks):
    """Split [lo, hi) into n_chunks contiguous index ranges."""
    total = hi - lo
    n_chunks = max(1, min(n_chunks, total))
    bounds = [lo + (total * i) // n_chunks for i in range(n_chunks + 1)]
    return [(bounds[i], bounds[i + 1]) for i in range(n_chunks)
            if bounds[i + 1] > bounds[i]]


# Top-level worker entry points (must be picklable for the 'spawn' start method).
def _chunk_worker(rng):
    return bs_serial(rng[0], rng[1])


def _sqrt_worker(two_d):
    # C = 426880 * sqrt(10005) * 10^D  ->  isqrt(10005 * 10^(2D)) * 426880
    return mpz(426880) * isqrt(mpz(10005) * (mpz(10) ** two_d))


def calc_pi(digits, workers, chunks_per_worker, progress=None):
    """
    Compute pi to `digits` digits. If `progress` is given it is called with small
    dicts describing each phase (used by the web UI for live SSE updates); when it
    is None we fall back to the human-friendly prints + tqdm bar.
    """
    def emit(**ev):
        if progress is not None:
            progress(ev)

    stats = {}
    D_calc = digits + 10
    N = int(D_calc / DIGITS_PER_TERM) + 1

    n_chunks = max(workers, workers * chunks_per_worker)
    ranges = make_ranges(1, N + 1, n_chunks)
    quiet = progress is not None

    if not quiet:
        print(f"Target: {digits} digits | Guard: 10 | Series terms: {N}")
        print(f"Performance cores: {workers} | Chunks: {len(ranges)} "
              f"(~{chunks_per_worker}x oversubscribe for load balancing)")
    emit(event="start", digits=digits, terms=N,
         workers=workers, chunks=len(ranges))

    start = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        # Kick off the giant square root as one extra parallel task up front so
        # it overlaps the whole binary-splitting phase on a spare core.
        fut_sqrt = pool.submit(_sqrt_worker, 2 * D_calc)

        # Submit all chunks; collect results back into index order.
        fut_to_idx = {pool.submit(_chunk_worker, r): i
                      for i, r in enumerate(ranges)}
        results = [None] * len(ranges)
        done = 0
        total = len(ranges)
        bar = None if quiet else tqdm(total=total, desc="Binary Splitting",
                                      unit=" chunk")
        for fut in as_completed(fut_to_idx):
            results[fut_to_idx[fut]] = fut.result()
            done += 1
            if bar is not None:
                bar.update(1)
            else:
                emit(event="chunk", done=done, total=total)
        if bar is not None:
            bar.close()
        stats['bs_time'] = time.time() - start
        emit(event="phase", name="bs", seconds=round(stats['bs_time'], 3))

        if not quiet:
            print("Merging chunk results...")
        t = time.time()
        if digits >= PARALLEL_MERGE_MIN_DIGITS and len(results) > 1:
            _, Q, T = parallel_merge(results, pool)
        else:
            _, Q, T = merge_in_order(results)
        stats['merge_time'] = time.time() - t
        emit(event="phase", name="merge", seconds=round(stats['merge_time'], 3))

        if not quiet:
            print("Retrieving overlapped square root...")
        t = time.time()
        C = fut_sqrt.result()
        stats['sqrt_time'] = time.time() - t
        emit(event="phase", name="sqrt", seconds=round(stats['sqrt_time'], 3))

        if not quiet:
            print("Performing final division...")
        t = time.time()
        pi_int = (C * Q) // (C2 * Q + T)
        stats['div_time'] = time.time() - t
        emit(event="phase", name="div", seconds=round(stats['div_time'], 3))

        if not quiet:
            print("Converting to decimal string...")
        t = time.time()
        if digits >= PARALLEL_STR_MIN_DIGITS:
            depth = max(3, workers.bit_length())
            pi_str_full = parallel_to_decimal(pi_int, pool, depth)
        else:
            pi_str_full = pi_int.digits(10) if HAS_GMP else str(pi_int)
        pi_str = pi_str_full[0] + "." + pi_str_full[1:digits + 1]
        stats['str_time'] = time.time() - t
        emit(event="phase", name="str", seconds=round(stats['str_time'], 3))

    return pi_str, stats


def verify_pi(calculated_pi_str, filepath):
    print(f"Verifying against reference file: '{filepath}'...")
    start = time.time()
    calc_clean = calculated_pi_str.replace(".", "").strip()
    try:
        with open(filepath, 'r') as f:
            ref_str = f.read().replace(".", "").replace("\n", "").replace(" ", "").strip()
        compare_len = min(len(calc_clean), len(ref_str))
        if len(ref_str) < len(calc_clean):
            print(f"Warning: reference has fewer digits ({len(ref_str)}) "
                  f"than calculated ({len(calc_clean)}).")
        if calc_clean[:compare_len] == ref_str[:compare_len]:
            print(f"\n[OK] SUCCESS: all {compare_len} digits match.")
        else:
            print("\n[X] FAILURE: mismatch detected.")
            for i in range(compare_len):
                if calc_clean[i] != ref_str[i]:
                    s, e = max(0, i - 5), min(compare_len, i + 5)
                    print(f"First mismatch at index {i} (digit {i + 1} after '3'):")
                    print(f"Calculated : ...{calc_clean[s:e]}...")
                    print(f"Reference  : ...{ref_str[s:e]}...")
                    break
    except FileNotFoundError:
        print(f"\n[!] Error: verification file '{filepath}' not found.")
    return time.time() - start


def main():
    parser = argparse.ArgumentParser(
        description="Turbo Pi (Chudnovsky / parallel Binary Splitting, Apple Silicon)")
    parser.add_argument("-d", "--digits", type=int, default=1000000,
                        help="Number of digits of Pi to calculate.")
    parser.add_argument("-v", "--verify", type=str, default="",
                        help="Optional reference .txt file for validation.")
    parser.add_argument("-o", "--output", type=str, default="pi_output.txt",
                        help="Path to save the digits of Pi.")
    parser.add_argument("-w", "--workers", type=int, default=0,
                        help="Worker processes (default: performance-core count).")
    parser.add_argument("-c", "--chunks-per-worker", type=int, default=4,
                        help="Chunk oversubscription factor for load balancing.")
    parser.add_argument("--progress-json", action="store_true",
                        help="Emit machine-readable JSON progress on stdout "
                             "(used by the web UI for live SSE streaming).")
    args = parser.parse_args()

    if not HAS_GMP:
        print("Refusing to run without gmpy2 (would take orders of magnitude longer).")
        sys.exit(1)

    workers = args.workers if args.workers > 0 else perf_core_count()

    # ---- JSON progress mode for the web server -------------------------------
    if args.progress_json:
        import json

        def emit(ev):
            sys.stdout.write(json.dumps(ev) + "\n")
            sys.stdout.flush()

        total_start = time.time()
        try:
            pi_str, stats = calc_pi(args.digits, workers,
                                    args.chunks_per_worker, progress=emit)
            with open(args.output, "w") as f:
                f.write(pi_str)
            emit({
                "event": "done",
                "digits": args.digits,
                "workers": workers,
                "total_seconds": round(time.time() - total_start, 3),
                "stats": {k: round(v, 3) for k, v in stats.items()},
                "preview": pi_str[:1002],
                "output": os.path.abspath(args.output),
            })
        except Exception as exc:  # surface failures to the UI
            emit({"event": "error", "message": str(exc)})
            sys.exit(1)
        return

    total_start = time.time()
    pi_str, stats = calc_pi(args.digits, workers, args.chunks_per_worker)

    write_start = time.time()
    print(f"Saving output to '{args.output}'...")
    with open(args.output, "w") as f:
        f.write(pi_str)
    stats['write_time'] = time.time() - write_start

    stats['ver_time'] = 0.0
    if args.verify:
        stats['ver_time'] = verify_pi(pi_str, args.verify)

    total_time = time.time() - total_start

    print("\n" + "=" * 44)
    print(f"{'--- TURBO COMPUTATION STATISTICS ---':^44}")
    print("=" * 44)
    print(f"Binary Splitting (parallel) : {stats['bs_time']:>9.3f} s")
    print(f"Merge                       : {stats['merge_time']:>9.3f} s")
    print(f"Root (overlapped)           : {stats['sqrt_time']:>9.3f} s")
    print(f"Final Division              : {stats['div_time']:>9.3f} s")
    print(f"String Conversion           : {stats['str_time']:>9.3f} s")
    print(f"File Save IO                : {stats['write_time']:>9.3f} s")
    if args.verify:
        print(f"File Verification           : {stats['ver_time']:>9.3f} s")
    print("-" * 44)
    print(f"TOTAL WALL TIME             : {total_time:>9.3f} s")
    print("=" * 44 + "\n")


if __name__ == "__main__":
    main()
