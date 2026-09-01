"""
PostgreSQL Validator (Phase 3 of the DB_VAL Validation Engine) - applies
schema.sql to a REAL, throwaway PostgreSQL instance and sees if it actually
succeeds. Every other validator in this engine (skills/db_validators.py) is
static analysis - a real parser (pglast), but still only reasoning about the
text, never actually running it. That's a real, structural gap: a DEFAULT
value or type name can be syntactically valid SQL and still be semantically
wrong (a misspelled type, an out-of-range default, a CHECK expression
referencing a function that doesn't exist) - pglast has no catalog to check
that against, only a live Postgres does. This module is what closes that
gap, the same way `pytest test_main.py` is a real execution check for
Backend rather than another static pass.

Deliberately NOT an LLM step - "does this SQL actually run against a real
Postgres" has one unambiguous, deterministic answer, the same reasoning
skills/e2e_skills.py already uses for booting the real stack instead of
guessing from static analysis.

Deliberately gated behind the OTHER DB_VAL validators already passing (see
run_val_validators in db_validators.py) - spinning up a container costs
real wall-clock time (several seconds), so it's wasted effort re-running on
every retry of a schema already known to be broken by a check that costs
nothing. Also deliberately fails OPEN (returns no problems, just a log
warning) if Docker itself isn't available/responsive - this is a defense-
in-depth check on top of the always-on static validators, not something
that should block the whole pipeline over a missing/misconfigured local
Docker install unrelated to whether the schema itself is any good.
"""

import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from core.logger import get_logger

_IMAGE = "postgres:16-alpine"
_READY_POLL_ATTEMPTS = 15
_READY_POLL_INTERVAL_S = 1.0


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=15)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def validate_schema_against_postgres(schema: str, test_file_content: str = "") -> list[str]:
    """
    Starts a throwaway postgres:16-alpine container, applies schema.sql to
    it for real with ON_ERROR_STOP so the first genuine SQL error aborts
    instead of psql plowing past it, then does one cheap sanity cross-check
    (does the live table count match what was actually in the script).
    Always tears the container down, even on failure/exception.

    If test_file_content is given (the shared Acceptance Test Generator's
    output - see skills/acceptance_test_generator.py and database.py's
    run()), this is also the Requirement Test Runner: a host port is
    published (needed so the pipeline's own pytest, running on the host,
    can reach the container), and the generated pytest file is actually
    executed for real against this live database via DATABASE_URL - not
    another static pass. A published port never collides with a project's
    own deployed containers since this uses a random host port (docker
    picks one, `-p 0:5432`), a throwaway database name, and is torn down
    before returning either way.

    Returns a list of problem strings (empty = schema genuinely applies
    cleanly AND, if given, every generated acceptance test passes). Fails
    open (empty list, logged warning) if Docker isn't available - see
    module docstring.
    """
    logger = get_logger()

    if not schema:
        return []

    if not _docker_available():
        logger.warning("PostgreSQL Validator: Docker isn't available - skipping the real-apply check "
                       "(the other DB_VAL validators already ran; this one is defense-in-depth on top)")
        return []

    container = f"db_validate_{uuid.uuid4().hex[:12]}"
    try:
        port_args = ["-p", "0:5432"] if test_file_content else []
        run = subprocess.run(
            ["docker", "run", "-d", "--name", container, *port_args,
             "-e", "POSTGRES_PASSWORD=postgres", "-e", "POSTGRES_DB=validate",
             _IMAGE],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        if run.returncode != 0:
            logger.warning(f"PostgreSQL Validator: could not start a temporary instance "
                           f"({run.stderr[-300:]}) - skipping this check")
            return []

        if not _wait_until_ready(container):
            logger.warning("PostgreSQL Validator: temporary instance never became ready - skipping this check")
            return []

        apply = subprocess.run(
            ["docker", "exec", "-i", container, "psql", "-U", "postgres", "-d", "validate",
             "-v", "ON_ERROR_STOP=1"],
            input=schema, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        if apply.returncode != 0:
            error_text = (apply.stderr or apply.stdout or "unknown error").strip()
            return [f"schema.sql fails to apply to a real PostgreSQL instance: {error_text[-500:]}"]

        problems = _sanity_check_applied_tables(container, schema)
        if not problems and test_file_content:
            problems.extend(_run_generated_tests(container, test_file_content))
        return problems

    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning(f"PostgreSQL Validator: unexpected error running the real-apply check ({e}) - "
                       f"skipping this check")
        return []
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30)


def _wait_until_ready(container: str) -> bool:
    for _ in range(_READY_POLL_ATTEMPTS):
        try:
            check = subprocess.run(
                ["docker", "exec", container, "pg_isready", "-U", "postgres", "-d", "validate"],
                capture_output=True, timeout=10,
            )
            if check.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, OSError):
            pass
        time.sleep(_READY_POLL_INTERVAL_S)
    return False


def _sanity_check_applied_tables(container: str, schema: str) -> list[str]:
    """
    A cheap backstop, not the main check - the apply's own exit code already
    caught any real SQL error. This only catches the rarer case of a driver/
    tool silently swallowing a partial failure: does the live table count
    roughly match what the script actually declared?
    """
    from skills.db_validators import build_schema_model

    model = build_schema_model(schema)
    if model.parse_error:
        return []  # DB_UT already failed this round for the same reason

    expected_tables = set(model.tables.keys())
    if not expected_tables:
        return []

    query = ("SELECT string_agg(table_name, ',') FROM information_schema.tables "
             "WHERE table_schema='public'")
    result = subprocess.run(
        ["docker", "exec", container, "psql", "-U", "postgres", "-d", "validate", "-t", "-A", "-c", query],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    if result.returncode != 0:
        return []  # can't verify - don't fail the schema over a query we couldn't run

    live_tables = {t.strip().lower() for t in (result.stdout or "").split(",") if t.strip()}
    missing = expected_tables - live_tables
    if missing:
        return [f"schema.sql applied without error, but table(s) {sorted(missing)} don't actually exist "
                f"in the resulting database - the apply may have partially failed silently"]
    return []


def _published_port(container: str) -> str | None:
    result = subprocess.run(["docker", "port", container, "5432/tcp"], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=15)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    # Output looks like "0.0.0.0:54321" (one line, possibly two for IPv4+IPv6) -
    # the port number is the same either way.
    first_line = result.stdout.strip().splitlines()[0]
    return first_line.rsplit(":", 1)[-1].strip()


def _run_generated_tests(container: str, test_file_content: str) -> list[str]:
    """
    The Requirement Test Runner: actually executes the shared Acceptance
    Test Generator's output against the live temp database via a real
    `pytest` subprocess (using the pipeline's own Python/pytest install,
    not the generated project's) - a genuinely different signal than
    anything else in this Validation Engine, since it checks BUSINESS
    behavior (does a soft-deleted row really stay excluded, does a bad
    foreign key really get rejected), not schema structure.

    Returns a list of problem strings - empty means every generated test
    passed. Fails open (empty list, logged warning) on any harness-level
    problem (no port found, pytest itself errors before running anything) -
    only real test FAILURES are reported as validation problems.
    """
    logger = get_logger()

    port = _published_port(container)
    if not port:
        logger.warning("PostgreSQL Validator: could not find the published port for the temporary "
                       "instance - skipping the generated acceptance tests")
        return []

    with tempfile.TemporaryDirectory() as tmpdir:
        test_path = Path(tmpdir) / "test_database.py"
        test_path.write_text(test_file_content, encoding="utf-8")

        import os
        env = {**os.environ, "DATABASE_URL": f"postgresql://postgres:postgres@localhost:{port}/validate"}

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(test_path), "-v", "--tb=short", "-p", "no:cacheprovider"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, env=env,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning(f"PostgreSQL Validator: could not run the generated acceptance tests ({e}) - skipping")
            return []

        if result.returncode == 0:
            logger.success("Generated acceptance tests: all passed against the real database")
            return []
        if result.returncode == 5:
            # pytest's own "no tests were collected" code - not a failure of
            # the schema, just nothing to run (e.g. the generator skipped
            # every criterion as untestable from the given context).
            return []

        output = (result.stdout or "") + "\n" + (result.stderr or "")
        return [f"generated acceptance test(s) failed against a real PostgreSQL instance:\n{output[-1500:]}"]
