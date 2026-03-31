#Q10. Multiprocessing — CPU-bound Speedup 
import time
import multiprocessing


# ─── CPU BOUND FUNCTION ───────────────────────────────
def compute_squares(n):
    result = sum(i * i for i in range(1, n + 1))  # heavy computation
    print(f"  compute_squares({n:,}) = {result}")
    return result


# ─── SEQUENTIAL ───────────────────────────────────────
def run_sequential(values):
    print("\n─── SEQUENTIAL ───")
    start   = time.time()
    results = []

    for n in values:
        result = compute_squares(n)    # one by one, blocks each time
        results.append(result)

    total = time.time() - start
    print(f"Sequential time: {total:.2f}s")
    return results


# ─── MULTIPROCESSING ──────────────────────────────────
def run_multiprocessing(values):
    print("\n─── MULTIPROCESSING ───")
    start = time.time()

    with multiprocessing.Pool() as pool:      # auto detects CPU cores
        results = pool.map(compute_squares, values)  # splits across cores

    total = time.time() - start
    print(f"Multiprocessing time: {total:.2f}s")
    return results


# ─── COMPARE RESULTS ──────────────────────────────────
def compare(seq_results, mp_results):
    print("\n─── VERIFICATION ───")
    all_match = seq_results == mp_results
    print(f"Results match: {all_match} ✅" if all_match else "Results mismatch ❌")


# ─── ENTRY POINT ──────────────────────────────────────
if __name__ == "__main__":              # required for multiprocessing!
    values = [10_000_000, 20_000_000, 15_000_000, 25_000_000]

    seq_results = run_sequential(values)
    mp_results  = run_multiprocessing(values)

    compare(seq_results, mp_results)