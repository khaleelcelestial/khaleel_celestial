"""
E2E skills - boots the full stack for real (docker compose up) and keeps it
running across E2E_RUN -> E2E_UT -> E2E_VAL (unlike every other stage's
self-contained boot-check-teardown-in-one-call validators), since E2E_UT's
"can it start" and E2E_VAL's "does it work correctly" are different
questions that both need to examine the SAME live instance, not two
separate boots. teardown_stack() is called on any failure (nothing more to
check) and unconditionally at the end of E2E_VAL (pass or fail) - see
capabilities/e2e.py for exactly where each is called.

Deliberately NOT an LLM agent for any of this - bringing a stack up/down,
polling a port, and reading `docker compose ps`/`logs` output is a
deterministic, mechanical process with an unambiguous pass/fail signal.
"""

import json
import subprocess
import time
import urllib.request
import urllib.error


def http_reachable(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """
    True if the server answers at all - even a 4xx counts as "reachable"
    (the process is up and responding), only a connection failure or a 5xx
    means the boot itself is broken.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status < 500, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return e.code < 500, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)[:150]


def boot_stack(project_dir, host_ports: dict, has_backend: bool, has_frontend: bool,
               boot_timeout_s: int = 60) -> dict:
    """
    Brings the stack up for real via `docker compose up --build -d` and
    polls the allocated backend/frontend host ports until they respond (or
    boot_timeout_s elapses). Does NOT tear down - see module docstring.

    Returns {"up_ok", "up_detail", "backend_ok", "frontend_ok",
    "backend_detail", "frontend_detail", "log"}.
    """
    result = {
        "up_ok": False, "up_detail": "",
        "backend_ok": not has_backend, "frontend_ok": not has_frontend,
        "backend_detail": "", "frontend_detail": "", "log": "",
    }

    try:
        up = subprocess.run(
            ["docker", "compose", "up", "--build", "-d"],
            cwd=str(project_dir), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        result["up_detail"] = str(e)[:300]
        return result

    result["log"] += f"$ docker compose up --build -d\n{up.stdout}\n{up.stderr}\n"
    if up.returncode != 0:
        result["up_detail"] = up.stderr[-500:]
        return result
    result["up_ok"] = True

    deadline = time.time() + boot_timeout_s
    while time.time() < deadline and not (result["backend_ok"] and result["frontend_ok"]):
        if has_backend and not result["backend_ok"]:
            result["backend_ok"], result["backend_detail"] = http_reachable(f"http://localhost:{host_ports['backend']}/")
        if has_frontend and not result["frontend_ok"]:
            result["frontend_ok"], result["frontend_detail"] = http_reachable(f"http://localhost:{host_ports['frontend']}/")
        if not (result["backend_ok"] and result["frontend_ok"]):
            time.sleep(2)

    return result


def container_states(project_dir) -> list[dict]:
    """
    Real `docker compose ps` output - per-container running-state, exit
    codes, and health status. Closes a gap a plain HTTP reachability poll
    can miss: a container that starts, crashes, and Compose is mid-restart
    at the exact moment the HTTP poll happens to catch a response from a
    PREVIOUS, now-dead process instance (or a sibling service that's fine
    while this one is crash-looping).
    """
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"], cwd=str(project_dir),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []

    states = []
    for line in (result.stdout or "").strip().splitlines():
        try:
            states.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return states


def container_logs_tail(project_dir, service: str, lines: int = 100) -> str:
    """Real container logs for one compose service - used to surface the
    actual fatal exception/traceback behind a crashed/exited container,
    not just "it's down"."""
    try:
        result = subprocess.run(
            ["docker", "compose", "logs", "--tail", str(lines), service], cwd=str(project_dir),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        return (result.stdout or "") + (result.stderr or "")
    except (subprocess.TimeoutExpired, OSError):
        return ""


def teardown_stack(project_dir) -> str:
    """Tears the E2E verification boot down - this isn't the real
    deployment (capabilities/cicd.py's job, later in the pipeline)."""
    try:
        down = subprocess.run(
            ["docker", "compose", "down"], cwd=str(project_dir), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        return f"$ docker compose down\n{down.stdout}\n{down.stderr}\n"
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"$ docker compose down - FAILED: {e}\n"
