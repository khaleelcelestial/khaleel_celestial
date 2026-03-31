import random
import functools

def retry(max_attempts):                                    # Layer 1 — receives argument
    def decorator(func):                                    # Layer 2 — receives function
        @functools.wraps(func)
        def wrapper(*args, **kwargs):                       # Layer 3 — wraps function
            last_exception = None

            for attempt in range(1, max_attempts + 1):     # 1 to max_attempts inclusive
                try:
                    result = func(*args, **kwargs)          # try running the function
                    print(f"[retry] Attempt {attempt} succeeded!")
                    return result                           # success — return immediately

                except Exception as e:
                    last_exception = e                      # store for possible re-raise
                    print(f"[retry] Attempt {attempt} failed: {e}")

            # all attempts exhausted — raise with custom message
            raise Exception(f"All {max_attempts} attempts failed") from last_exception

        return wrapper
    return decorator


random.seed(60)                                             # reproducible output

@retry(max_attempts=5)
def fetch_data():
    """Simulates a flaky API call."""
    if random.choice([True, False]):
        raise ConnectionError("Server unreachable")
    return {"status": "ok", "data": [1, 2, 3]}


result = fetch_data()
print(f"Result: {result}")