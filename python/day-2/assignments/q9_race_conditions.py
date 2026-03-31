#Q9. Race Condition — Shared Counter Fix 
import threading


# ─── VERSION 1: WITHOUT LOCK (RACE CONDITION) ─────────
def increment_unsafe(container):
    for _ in range(1000):
        container[0] += 1              # not thread safe ❌


def run_without_lock():
    print("\n─── WITHOUT LOCK ───")
    container = [0]                    # mutable container (list)
    threads   = []

    for _ in range(10):
        t = threading.Thread(
            target=increment_unsafe,
            args=(container,)
        )
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    print(f"Without lock: {container[0]} (expected 10000)")


# ─── VERSION 2: WITH LOCK (THREAD SAFE) ───────────────
def increment_safe(container, lock):
    for _ in range(1000):
        with lock:                     # only one thread at a time ✅
            container[0] += 1


def run_with_lock():
    print("\n─── WITH LOCK ───")
    container = [0]                    # mutable container (list)
    lock      = threading.Lock()       # one lock shared by all threads
    threads   = []

    for _ in range(10):
        t = threading.Thread(
            target=increment_safe,
            args=(container, lock)     # pass lock to each thread
        )
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    print(f"With lock: {container[0]} (expected 10000)")


# ─── VERSION 3: USING CLASS (CLEANER) ─────────────────
class Counter:
    def __init__(self):
        self.value = 0
        self.lock  = threading.Lock()  # lock lives inside class

    def increment(self):
        with self.lock:                # protected ✅
            self.value += 1

    def get(self):
        return self.value


def run_with_class():
    print("\n─── WITH CLASS ───")
    counter = Counter()
    threads = []

    for _ in range(10):
        t = threading.Thread(
            target=lambda: [counter.increment() for _ in range(1000)]
        )
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    print(f"With class: {counter.get()} (expected 10000)")


# ─── SHOW INCONSISTENCY — RUN 5 TIMES ─────────────────
def show_inconsistency():
    print("\n─── RACE CONDITION (5 runs) ───")
    for run in range(1, 6):
        container = [0]
        threads   = []

        for _ in range(10):
            t = threading.Thread(
                target=increment_unsafe,
                args=(container,)
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        status = "✅" if container[0] == 10000 else "❌"
        print(f"  Run {run}: {container[0]} {status}")


# ─── RUNNING THE CODE ─────────────────────────────────
show_inconsistency()       # proves race condition is real
run_without_lock()         # unsafe version
run_with_lock()            # fixed with lock
run_with_class()           # fixed with class