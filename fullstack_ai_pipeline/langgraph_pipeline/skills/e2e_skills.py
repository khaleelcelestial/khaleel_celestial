"""
E2E skills - boots the full stack for real (docker compose up), waits for it
to actually become reachable, does a couple of real HTTP checks against the
live backend/frontend, then tears it back down. This is "does the whole
system actually work together when running", distinct from
quality_skills.run_tests_skill's static (never-executed) checks - the
diagram's "E2E_UT: stack starts clean?" / "E2E_VAL: real user flows pass?"
step.

Deliberately NOT an LLM agent - bringing a stack up/down and polling a port
is a deterministic, mechanical process with an unambiguous pass/fail signal
(the same reasoning as run_tests_skill vs. an LLM code reviewer: don't spend
a model call on something a few lines of subprocess/urllib can answer for
free, precisely, every time).
"""

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


def run_e2e_skill(project_dir, host_ports: dict, has_backend: bool, has_frontend: bool,
                  boot_timeout_s: int = 60) -> dict:
    """
    Brings the stack up for real via `docker compose up --build -d`, polls
    the allocated backend/frontend host ports until they respond (or
    boot_timeout_s elapses), then tears everything back down unconditionally
    (this is a temporary verification boot, not the real deployment - that's
    capabilities/cicd.py's job, later in the pipeline).

    Returns {"passed": bool, "checks": [{"name", "ok", "detail"}], "log": str}.
    """
    result = {"passed": False, "checks": [], "log": ""}

    try:
        up = subprocess.run(
            ["docker", "compose", "up", "--build", "-d"],
            cwd=str(project_dir), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        result["checks"].append({"name": "docker compose up", "ok": False, "detail": str(e)[:300]})
        return result

    result["log"] += f"$ docker compose up --build -d\n{up.stdout}\n{up.stderr}\n"
    if up.returncode != 0:
        result["checks"].append({"name": "docker compose up", "ok": False, "detail": up.stderr[-500:]})
        _teardown(project_dir, result)
        return result
    result["checks"].append({"name": "docker compose up", "ok": True, "detail": "containers started"})

    backend_ok = not has_backend
    frontend_ok = not has_frontend
    backend_detail = frontend_detail = ""
    deadline = time.time() + boot_timeout_s
    while time.time() < deadline and not (backend_ok and frontend_ok):
        if has_backend and not backend_ok:
            backend_ok, backend_detail = http_reachable(f"http://localhost:{host_ports['backend']}/")
        if has_frontend and not frontend_ok:
            frontend_ok, frontend_detail = http_reachable(f"http://localhost:{host_ports['frontend']}/")
        if not (backend_ok and frontend_ok):
            time.sleep(2)

    if has_backend:
        result["checks"].append({
            "name": "backend reachable", "ok": backend_ok,
            "detail": f"http://localhost:{host_ports['backend']}/ - {backend_detail}",
        })
    if has_frontend:
        result["checks"].append({
            "name": "frontend reachable", "ok": frontend_ok,
            "detail": f"http://localhost:{host_ports['frontend']}/ - {frontend_detail}",
        })

    result["passed"] = backend_ok and frontend_ok
    _teardown(project_dir, result)
    return result


def _teardown(project_dir, result: dict) -> None:
    """Always tear down the temporary verification boot, pass or fail - this isn't the real deployment."""
    try:
        down = subprocess.run(
            ["docker", "compose", "down"], cwd=str(project_dir), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        result["log"] += f"$ docker compose down\n{down.stdout}\n{down.stderr}\n"
    except (subprocess.TimeoutExpired, OSError) as e:
        result["log"] += f"$ docker compose down - FAILED: {e}\n"
