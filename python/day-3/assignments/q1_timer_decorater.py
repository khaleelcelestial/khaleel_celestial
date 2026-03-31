import time
import functools

def timer(func):
    @functools.wraps(func)                          # preserves __name__, __doc__
    def wrapper(*args, **kwargs):                   # works with ANY arguments
        start = time.perf_counter()                 # start time
        result = func(*args, **kwargs)              # run original function
        end = time.perf_counter()                   # end time
        duration = end - start                      # calculate difference
        print(f"[timer] {func.__name__} executed in {duration:.4f}s")  # exact format
        return result                               # return original result
    return wrapper


@timer
def compute_squares(n):
    """Computes sum of squares from 1 to n."""
    return sum(i * i for i in range(1, n + 1))


result = compute_squares(1_000_000)
print(f"Result: {result}")
print(f"Function name: {compute_squares.__name__}")
print(f"Docstring: {compute_squares.__doc__}")