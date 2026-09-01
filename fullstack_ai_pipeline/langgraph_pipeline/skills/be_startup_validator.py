"""
Startup Validator (Phase 3 of the BE_VAL Validation Engine) - builds the
REAL backend Docker image (via docker_skills.py's generate_backend_dockerfile,
the exact same deterministic template cicd.py/e2e.py use for the actual
deploy - not a simplified stand-in) and boots it against a real throwaway
PostgreSQL, then polls /openapi.json for a real 200. Closes the same kind of
gap db_postgres_validator.py closes for schema.sql: Phase 1/2's static AST
analysis above can prove the code LOOKS right without ever proving it
actually RUNS - e.g. `Base.metadata.create_all(bind=engine)` at module import
time (confirmed the real pattern this pipeline's backends use) means the
app can't even start without a genuinely reachable database, something no
static check can see.

/openapi.json (not a project-specific /health) is polled because FastAPI
serves it automatically for any app - no BACKEND_SYSTEM_PROMPT change
needed, unlike /health which isn't currently mandated.

Deliberately gated behind Phase 1/2 already passing (see
be_validators.py's run_be_val_validators) - building a real image costs
real wall-clock time (a full pip install), so there's no point paying that
cost to re-confirm code already known to be broken by checks that are free.

Deliberately fails OPEN (returns no problems, just a log warning) if Docker
isn't available/responsive - same reasoning as db_postgres_validator.py:
this is defense-in-depth on top of the always-on static validators, not
something that should block the whole pipeline over a missing/misconfigured
local Docker install unrelated to whether the backend itself is any good.

Phase 4 additionally makes this the Requirement Test Runner for Backend: if
test_file_content is given (the shared Acceptance Test Generator's output -
see capabilities/backend.py's run()), once /openapi.json genuinely responds,
those generated tests are executed for real via a real `pytest` subprocess
against BASE_URL pointing at the live published port - real HTTP calls
against the actually-booted container, not another static pass and not an
in-process TestClient. A failing generated test fails BE_VAL just like any
other problem here, same pattern as db_postgres_validator.py's own
Requirement Test Runner for Database.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from core.logger import get_logger

_POSTGRES_IMAGE = "postgres:16-alpine"
_PG_READY_ATTEMPTS = 15
_PG_READY_INTERVAL_S = 1.0
_APP_POLL_ATTEMPTS = 20
_APP_POLL_INTERVAL_S = 1.5


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=15)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def validate_backend_startup(backend_files: dict, test_file_content: str = "") -> list[str]:
    """
    Writes backend_files plus a real generate_backend_dockerfile() Dockerfile
    to a throwaway build context, starts a throwaway Postgres on a private
    Docker network, builds and boots the backend image against it (a real
    DATABASE_URL, matching this pipeline's actual deploy shape), and polls
    /openapi.json for a real 200. If test_file_content is given (the shared
    Acceptance Test Generator's output), this is also the Requirement Test
    Runner - the generated pytest file is executed for real against the
    live container via real HTTP calls (BASE_URL env var), not another
    static pass. Returns a list of problem strings (empty = the app
    genuinely started, served a real request, AND, if given, every
    generated acceptance test passed). Always tears every
    container/network/image down, even on failure/exception. Fails open if
    Docker isn't available - see module docstring.
    """
    logger = get_logger()

    if not backend_files:
        return []

    if not _docker_available():
        logger.warning("Startup Validator: Docker isn't available - skipping the real-boot check "
                       "(the other BE_VAL validators already ran; this one is defense-in-depth on top)")
        return []

    from skills.docker_skills import generate_backend_dockerfile
    dockerfile, container_port = generate_backend_dockerfile(backend_files)

    run_id = uuid.uuid4().hex[:12]
    network = f"be_validate_net_{run_id}"
    pg_container = f"be_validate_pg_{run_id}"
    app_container = f"be_validate_app_{run_id}"
    image_tag = f"be_validate_img_{run_id}"

    try:
        with tempfile.TemporaryDirectory() as build_dir:
            build_path = Path(build_dir)
            for path, content in backend_files.items():
                file_path = build_path / path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")
            (build_path / "Dockerfile").write_text(dockerfile, encoding="utf-8")

            net_create = subprocess.run(["docker", "network", "create", network],
                                        capture_output=True, text=True, encoding="utf-8",
                                        errors="replace", timeout=30)
            if net_create.returncode != 0:
                logger.warning("Startup Validator: could not create a temporary Docker network - "
                               "skipping this check")
                return []

            pg_run = subprocess.run(
                ["docker", "run", "-d", "--name", pg_container, "--network", network,
                 "-e", "POSTGRES_PASSWORD=postgres", "-e", "POSTGRES_DB=validate", _POSTGRES_IMAGE],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            )
            if pg_run.returncode != 0:
                logger.warning("Startup Validator: could not start a temporary database - skipping this check")
                return []
            if not _wait_until_pg_ready(pg_container):
                logger.warning("Startup Validator: temporary database never became ready - skipping this check")
                return []

            build = subprocess.run(
                ["docker", "build", "-t", image_tag, str(build_path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
            )
            if build.returncode != 0:
                error_text = (build.stderr or build.stdout or "unknown error").strip()
                return [f"backend/ fails to build a real Docker image: {error_text[-1500:]}"]

            db_url = f"postgresql://postgres:postgres@{pg_container}:5432/validate"
            app_run = subprocess.run(
                ["docker", "run", "-d", "--name", app_container, "--network", network,
                 "-p", f"0:{container_port}", "-e", f"DATABASE_URL={db_url}", image_tag],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
            if app_run.returncode != 0:
                error_text = (app_run.stderr or app_run.stdout or "unknown error").strip()
                return [f"backend Docker image built but the container failed to start: {error_text[-1000:]}"]

            port = _published_port(app_container, container_port)
            if not port:
                return ["backend container started but no published port could be determined - "
                        "cannot verify it's actually serving requests"]

            problems = _poll_openapi(app_container, port)
            if not problems and test_file_content:
                problems.extend(_run_generated_tests(port, test_file_content))
            return problems

    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning(f"Startup Validator: unexpected error running the real-boot check ({e}) - "
                       f"skipping this check")
        return []
    finally:
        subprocess.run(["docker", "rm", "-f", app_container], capture_output=True, timeout=30)
        subprocess.run(["docker", "rm", "-f", pg_container], capture_output=True, timeout=30)
        subprocess.run(["docker", "rmi", "-f", image_tag], capture_output=True, timeout=30)
        subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=30)


def _wait_until_pg_ready(container: str) -> bool:
    for _ in range(_PG_READY_ATTEMPTS):
        try:
            check = subprocess.run(
                ["docker", "exec", container, "pg_isready", "-U", "postgres", "-d", "validate"],
                capture_output=True, timeout=10,
            )
            if check.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, OSError):
            pass
        time.sleep(_PG_READY_INTERVAL_S)
    return False


def _published_port(container: str, container_port: int) -> str | None:
    result = subprocess.run(["docker", "port", container, f"{container_port}/tcp"], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=15)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    # Output looks like "0.0.0.0:54321" (one line, possibly two for IPv4+IPv6) -
    # the port number is the same either way.
    first_line = result.stdout.strip().splitlines()[0]
    return first_line.rsplit(":", 1)[-1].strip()


def _poll_openapi(container: str, port: str) -> list[str]:
    """Polls http://127.0.0.1:{port}/openapi.json from the HOST, not inside
    the container - matches how a real client reaches it, and avoids needing
    curl/wget installed inside the (possibly minimal) app image. 127.0.0.1,
    never "localhost" - same reasoning docker_skills.py's own healthcheck
    comments document (IPv6 resolution-order surprises on some hosts)."""
    url = f"http://127.0.0.1:{port}/openapi.json"
    last_error = None
    for _ in range(_APP_POLL_ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    try:
                        json.loads(response.read())
                    except ValueError as e:
                        return [f"backend started and responded on /openapi.json, but the response "
                                f"wasn't valid JSON: {e}"]
                    return []
                last_error = f"HTTP {response.status}"
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_error = e
        time.sleep(_APP_POLL_INTERVAL_S)

    logs = subprocess.run(["docker", "logs", "--tail", "50", container], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=15)
    log_tail = ((logs.stdout or "") + (logs.stderr or "")).strip()
    waited_s = _APP_POLL_ATTEMPTS * _APP_POLL_INTERVAL_S
    return [f"backend container started but never served a real 200 from /openapi.json within "
            f"{waited_s:.0f}s (last error: {last_error}). Container logs (tail):\n{log_tail[-1500:]}"]


def _run_generated_tests(port: str, test_file_content: str) -> list[str]:
    """
    The Requirement Test Runner for Backend: actually executes the shared
    Acceptance Test Generator's output against the live, really-booted
    container via a real `pytest` subprocess (using the pipeline's own
    Python/pytest/requests install, not the generated project's), with
    BASE_URL pointing at the container's published port - real HTTP calls,
    not another static pass and not an in-process TestClient. Mirrors
    db_postgres_validator.py's _run_generated_tests, but for HTTP instead
    of a direct DB connection.

    Returns a list of problem strings - empty means every generated test
    passed. Fails open (empty list, logged warning) on any harness-level
    problem (pytest itself errors before running anything) - only real test
    FAILURES are reported as validation problems.
    """
    logger = get_logger()

    with tempfile.TemporaryDirectory() as tmpdir:
        test_path = Path(tmpdir) / "test_backend.py"
        test_path.write_text(test_file_content, encoding="utf-8")

        env = {**os.environ, "BASE_URL": f"http://127.0.0.1:{port}"}

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(test_path), "-v", "--tb=short", "-p", "no:cacheprovider"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning(f"Startup Validator: could not run the generated acceptance tests ({e}) - skipping")
            return []

        if result.returncode == 0:
            logger.success("Generated backend acceptance tests: all passed against the real container")
            return []
        if result.returncode == 5:
            # pytest's own "no tests were collected" code - not a failure of
            # the backend, just nothing to run (e.g. the generator skipped
            # every criterion as untestable from the given context).
            return []

        output = (result.stdout or "") + "\n" + (result.stderr or "")
        return [f"generated acceptance test(s) failed against the real, really-booted backend "
                f"container:\n{output[-1500:]}"]
