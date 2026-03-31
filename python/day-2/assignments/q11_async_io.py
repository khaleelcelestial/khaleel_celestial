import asyncio
import time


# ─── SYNC FETCH ───────────────────────────────────────
def fetch_sync(url, delay):
    start = time.time()
    print(f"  [SYNC]  {url} started  at {start - sync_start:.1f}s")
    time.sleep(delay)                         # blocks everything ❌
    end = time.time()
    print(f"  [SYNC]  {url} finished at {end - sync_start:.1f}s")
    return f"{url} data"


# ─── ASYNC FETCH ──────────────────────────────────────
async def fetch_async(url, delay):
    start = time.time()
    print(f"  [ASYNC] {url} started  at {start - async_start:.1f}s")
    await asyncio.sleep(delay)                # yields control ✅
    end = time.time()
    print(f"  [ASYNC] {url} finished at {end - async_start:.1f}s")
    return f"{url} data"


# ─── SYNC RUNNER ──────────────────────────────────────
def run_sync(urls):
    global sync_start
    print("\n─── SYNC VERSION ───")
    sync_start = time.time()
    results    = []

    for url, delay in urls:
        result = fetch_sync(url, delay)       # waits fully each time
        results.append(result)

    total = time.time() - sync_start
    print(f"\nSync results : {results}")
    print(f"Sync time    : {total:.1f}s")
    return results


# ─── ASYNC RUNNER ─────────────────────────────────────
async def run_async(urls):
    global async_start
    print("\n─── ASYNC VERSION ───")
    async_start = time.time()

    results = await asyncio.gather(           # all run concurrently ✅
        *[fetch_async(url, delay) for url, delay in urls]
    )

    total = time.time() - async_start
    print(f"\nAsync results: {list(results)}")
    print(f"Async time   : {total:.1f}s")
    return results


# ─── MAIN ─────────────────────────────────────────────
async def main():
    urls = [
        ("api/users",    2),
        ("api/orders",   3),
        ("api/products", 1),
        ("api/reviews",  2)
    ]

    run_sync(urls)              # sync first
    await run_async(urls)       # async second


asyncio.run(main())