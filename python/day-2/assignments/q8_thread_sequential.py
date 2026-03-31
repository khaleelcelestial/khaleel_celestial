#Q8. Thread vs Sequential — IO Simulation 
import threading
import time
from concurrent.futures import ThreadPoolExecutor


# ─── FETCH FUNCTION ───────────────────────────────────
def fetch_data(source, delay):
    print(f"  [{source}] started")
    time.sleep(delay)                    # simulates API call
    print(f"  [{source}] finished")


# ─── SEQUENTIAL ───────────────────────────────────────
def run_sequential(sources):
    print("\n─── SEQUENTIAL ───")
    start = time.time()

    for source, delay in sources:        # one by one, waits each time
        fetch_data(source, delay)

    total = time.time() - start
    print(f"Sequential time: {total:.1f}s")


# ─── THREADED ─────────────────────────────────────────
def run_threaded(sources):
    print("\n─── THREADED ───")
    start = time.time()

    threads = []
    for source, delay in sources:
        t = threading.Thread(
            target=fetch_data,
            args=(source, delay)         # pass source and delay
        )
        threads.append(t)
        t.start()                        # start immediately

    for t in threads:
        t.join()                         # wait for all to finish

    total = time.time() - start
    print(f"Threaded time: {total:.1f}s")


# ─── THREAD POOL (ALTERNATIVE) ────────────────────────
def run_threadpool(sources):
    print("\n─── THREAD POOL ───")
    start = time.time()

    with ThreadPoolExecutor(max_workers=5) as executor:
        for source, delay in sources:
            executor.submit(fetch_data, source, delay)  # submit each task

    total = time.time() - start
    print(f"Thread pool time: {total:.1f}s")


# ─── RUNNING THE CODE ─────────────────────────────────
sources = [
    ("users",     2),
    ("orders",    3),
    ("products",  1),
    ("reviews",   2),
    ("inventory", 1)
]

run_sequential(sources)
run_threaded(sources)
run_threadpool(sources)