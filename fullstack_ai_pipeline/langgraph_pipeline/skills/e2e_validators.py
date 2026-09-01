"""
System Integration Validation Engine - E2E's own Validator base
class/shape, same architecture as db_validators.py/be_validators.py/
fe_validators.py: one focused class per concern, fully deterministic where
the spec requires it (E2E_UT). E2E_VAL's validators (added in later phases)
may include exactly ONE subjective LLM step (the Holistic Review) - see
that phase's additions - everything else here stays deterministic.

Phase 1 content: E2E_UT's "can the complete stack start successfully"
proof - container running-state, crash-loop detection via restart count,
and fatal-exception scanning in container logs. Reachability itself is
already checked by skills/e2e_skills.py's boot_stack() before this even
runs; these validators look at what boot_stack() can't see from an HTTP
poll alone (a container that's technically "up" between crash-restart
cycles at the exact moment polled).
"""

import json
import os
import re
import subprocess
from pathlib import Path

from core.logger import get_logger

_FATAL_LOG_MARKERS = (
    "Traceback (most recent call last)",
    "FATAL",
    "panic:",
    "Segmentation fault",
    "ModuleNotFoundError",
    "ImportError",
)

# Every finding this module produces gets a "[stage] " prefix so
# capabilities/e2e.py can build real, file-attributed runtime.review_issues
# entries out of a plain list[str] - without this, the Supervisor's own
# stage-matching logic (capabilities/supervisor.py's _stage_named_in_issue,
# which checks `f"{stage}/" in issue["file"]`) has nothing to match against,
# and neither Backend nor Frontend ever learns WHICH of them E2E is asking
# to fix. "db" (the actual compose service name) maps to the "database"
# stage name Supervisor/Database's own capability use everywhere else.
_SERVICE_TO_STAGE = {"backend": "backend", "frontend": "frontend", "db": "database"}


def _tag(stage: str, message: str) -> str:
    return f"[{stage}] {message}"


_TAG_PATTERN = re.compile(r"^\[(\w+)\]\s*(.*)", re.DOTALL)


def parse_tag(problem: str) -> tuple:
    """Splits a '[stage] message' string (every finding in this module is
    tagged this way) back into (stage, message) - used by
    capabilities/e2e.py to build real runtime.review_issues entries so the
    Supervisor's own stage-matching logic has something to match against.
    Falls back to ("system", problem) for anything unexpectedly untagged
    (e.g. a holistic-review finding, which already carries its own real
    file path and doesn't go through this convention at all)."""
    match = _TAG_PATTERN.match(problem)
    return (match.group(1), match.group(2)) if match else ("system", problem)


class Validator:
    name = "base"

    def validate(self, *args, **kwargs) -> list[str]:
        raise NotImplementedError


class ContainerRunningValidator(Validator):
    """Every expected service's container must actually be in the
    'running' state - a container that exited (even with code 0) or was
    never created at all means the stack didn't really start, regardless
    of what an HTTP poll against a DIFFERENT, still-up service might have
    reported."""
    name = "container_running"

    def validate(self, project_dir, expected_services: list[str]) -> list[str]:
        from skills.e2e_skills import container_states, container_logs_tail

        states = {s.get("Service"): s for s in container_states(project_dir)}
        problems = []
        for service in expected_services:
            stage = _SERVICE_TO_STAGE.get(service, service)
            state = states.get(service)
            if state is None:
                problems.append(_tag(stage, f"container for service '{service}' was never created - "
                                            f"check `docker compose up` output for a build/config error"))
                continue
            status = str(state.get("State", "")).lower()
            if status and status != "running":
                log_tail = container_logs_tail(project_dir, service, lines=50)
                problems.append(_tag(stage, f"container '{service}' is not running (state: {status}) - "
                                            f"log tail:\n{log_tail[-800:]}"))
        return problems


class CrashLoopValidator(Validator):
    """A container can be 'running' at the exact instant polled and STILL
    be crash-looping (Compose restarts it faster than a single HTTP poll
    can notice) - a high restart count is the real signal, not raw
    'running' state alone. `docker compose ps --format json` does NOT
    expose a restart count field at all (confirmed by inspecting its real
    output before writing this) - only `docker inspect` does, via
    `.RestartCount` - so this validator makes that separate call per
    container rather than trying to read it from container_states()."""
    name = "crash_loop"

    def validate(self, project_dir, expected_services: list[str]) -> list[str]:
        import subprocess
        from skills.e2e_skills import container_states, container_logs_tail

        states = {s.get("Service"): s for s in container_states(project_dir)}
        problems = []
        for service in expected_services:
            state = states.get(service)
            container_id = state.get("ID") if state else None
            if not container_id:
                continue
            try:
                inspect = subprocess.run(
                    ["docker", "inspect", "--format", "{{.RestartCount}}", container_id],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
                )
            except (subprocess.TimeoutExpired, OSError):
                continue
            if inspect.returncode != 0:
                continue
            try:
                restart_count = int(inspect.stdout.strip())
            except ValueError:
                continue
            if restart_count >= 2:
                log_tail = container_logs_tail(project_dir, service, lines=50)
                stage = _SERVICE_TO_STAGE.get(service, service)
                problems.append(_tag(stage, f"container '{service}' has restarted {restart_count} times - "
                                            f"this looks like a crash loop, not a clean start - "
                                            f"log tail:\n{log_tail[-800:]}"))
        return problems


class FatalLogValidator(Validator):
    """Scans each expected service's recent logs for an unambiguous fatal
    marker (an uncaught Python traceback, a missing-module import error,
    a native crash) - catches a container that's technically 'running'
    (e.g. a supervisor/wrapper process that stays alive) while the actual
    application inside it already crashed at startup."""
    name = "fatal_log"

    def validate(self, project_dir, expected_services: list[str]) -> list[str]:
        from skills.e2e_skills import container_logs_tail

        problems = []
        for service in expected_services:
            log_tail = container_logs_tail(project_dir, service, lines=200)
            for marker in _FATAL_LOG_MARKERS:
                if marker in log_tail:
                    idx = log_tail.find(marker)
                    stage = _SERVICE_TO_STAGE.get(service, service)
                    problems.append(_tag(stage, f"container '{service}' logs contain a fatal marker "
                                                f"('{marker}'): ...{log_tail[max(0, idx - 100):idx + 400]}..."))
                    break  # one finding per service is enough signal - don't pile on
        return problems


def run_e2e_ut_validators(project_dir, e2e_boot: dict) -> list[str]:
    """E2E_UT's engine - flattened into a plain list[str] so
    E2ECapability.check_ut() (and the Supervisor, which only ever sees ONE
    pass/fail from it) needs zero stage-specific knowledge of what's
    inside. e2e_boot is E2E_RUN's boot_stack() result - if the boot itself
    never even succeeded (`up_ok` False) or a service never became
    reachable, that's reported directly without bothering to inspect
    container/log state further (nothing meaningful to inspect if compose
    itself failed to bring anything up)."""
    if not e2e_boot.get("up_ok"):
        return [_tag("system", f"`docker compose up` itself failed: {e2e_boot.get('up_detail', 'unknown error')}")]

    problems = []
    if e2e_boot.get("has_backend") and not e2e_boot.get("backend_ok"):
        problems.append(_tag("backend", f"backend never became reachable within the boot window: "
                                        f"{e2e_boot.get('backend_detail', '')}"))
    if e2e_boot.get("has_frontend") and not e2e_boot.get("frontend_ok"):
        problems.append(_tag("frontend", f"frontend never became reachable within the boot window: "
                                         f"{e2e_boot.get('frontend_detail', '')}"))
    if problems:
        return problems

    expected_services = []
    if e2e_boot.get("has_backend"):
        expected_services.append("backend")
    if e2e_boot.get("has_frontend"):
        expected_services.append("frontend")
    if e2e_boot.get("has_database"):
        expected_services.append("db")

    from core.logger import get_logger
    logger = get_logger()
    for name, result in (
        ("ContainerRunningValidator", ContainerRunningValidator().validate(project_dir, expected_services)),
        ("CrashLoopValidator", CrashLoopValidator().validate(project_dir, expected_services)),
        ("FatalLogValidator", FatalLogValidator().validate(project_dir, expected_services)),
    ):
        problems.extend(result)
        logger.validator_result("testing", "ut", name, "FAIL" if result else "PASS", issue_count=len(result))
    return problems


# ============================================================================
# E2E_VAL (Phase 2) - "does the complete system work correctly together",
# not just "did it start". Runs against the SAME still-live stack E2E_UT
# just proved boots cleanly (see capabilities/e2e.py - the boot is torn
# down only after E2E_VAL finishes, not before).
# ============================================================================

class ApiIntegrationValidator(Validator):
    """Real network-level check across a full page load: any request that
    fails at the connection level, or any real 5xx server error, means
    frontend-backend integration broke somewhere - even if a bare
    reachability GET (E2E_UT's own check) looked fine. Distinct from
    Frontend's own FE_VAL browser check: that one tests the frontend's
    OWN correctness against a live backend; this one is checking the
    INTEGRATION boundary itself, on the final assembled system, as the
    last gate before the whole project is considered done."""
    name = "api_integration"

    def validate(self, frontend_url: str) -> list[str]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return []

        failed_requests, server_errors = [], []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.on("requestfailed", lambda req: failed_requests.append(f"{req.method} {req.url}"))
                page.on("response", lambda res: server_errors.append(f"{res.status} {res.url}")
                        if res.status >= 500 else None)
                page.goto(frontend_url, timeout=15000, wait_until="networkidle")
                browser.close()
        except Exception as e:
            return [_tag("integration", f"could not run the API integration check: {str(e)[:200]}")]

        problems = []
        if failed_requests:
            problems.append(_tag("integration", f"{len(failed_requests)} network request(s) failed to "
                                                 f"complete during page load: {failed_requests[0]}"))
        if server_errors:
            problems.append(_tag("backend", f"{len(server_errors)} request(s) got a real server error "
                                            f"(5xx) during page load: {server_errors[0]}"))
        return problems


class DatabaseIntegrationValidator(Validator):
    """Deliberately scoped narrow: confirms THIS SPECIFIC running
    instance's real database is reachable from outside the container and
    its real schema actually applied - NOT a synthetic CRUD probe.
    Constructing a valid INSERT payload generically and safely across
    arbitrary domains (CRM/ERP/Hospital/Banking/...) without violating
    real constraints isn't reliably possible without either an LLM call
    (which would make this non-deterministic) or full OpenAPI
    request-body-schema-driven payload synthesis (a much larger, separate
    effort). Whether CRUD initiated through the REAL UI actually persists
    is proven by Phase 3's Acceptance Test Runner instead - a real,
    user-flow-driven proof already exercising this exact concern, not a
    synthetic bypass-the-UI one."""
    name = "database_integration"

    def validate(self, project_id: str, host_ports: dict, expected_tables: set) -> list[str]:
        try:
            import psycopg2
        except ImportError:
            return []
        if not expected_tables:
            return []

        from skills.docker_skills import get_postgres_config
        from skills.project_registry import db_name_for

        pg = get_postgres_config()
        db_name = db_name_for(project_id)
        try:
            conn = psycopg2.connect(host="localhost", port=host_ports["db"], user=pg["user"],
                                    password=pg["password"], dbname=db_name, connect_timeout=10)
        except Exception as e:
            return [_tag("database", f"the real database ({db_name}) isn't reachable from outside the "
                                     f"container: {str(e)[:200]}")]

        try:
            with conn.cursor() as cur:
                cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
                live_tables = {row[0].lower() for row in cur.fetchall()}
        except Exception as e:
            return [_tag("database", f"could not query the real database: {str(e)[:200]}")]
        finally:
            conn.close()

        missing = expected_tables - live_tables
        if missing:
            return [_tag("database", f"table(s) {sorted(missing)} are declared in schema.sql but don't "
                                     f"actually exist in the REAL running database for this deployment - "
                                     f"the schema may not have applied cleanly this time")]
        return []


class SecurityValidator(Validator):
    """Conditional and deliberately coarse: cross-references Frontend's
    own guarded-route evidence (the same routes Frontend's FE_VAL already
    auth-bypass-tested AT THE BROWSER level - see
    skills/fe_browser_validator.py's _guarded_routes) with the
    corresponding backend API resource (matched by first path segment -
    the same best-effort naming convention db_validators.py's
    ContractValidator/be_validators.py's ContractOperationValidator
    already use), then calls that resource's read endpoint over real HTTP
    with NO Authorization header. A 401/403 is correct; a 200 means the
    backend never actually enforces what the frontend visually gates - a
    genuine, exploitable security gap only visible at the INTEGRATION
    level, since neither Backend nor Frontend alone has both halves of
    this picture (Backend doesn't know what the UI implies should be
    protected; Frontend can't inspect the backend's own auth
    dependencies)."""
    name = "security"

    def validate(self, backend_url: str, guarded_routes: set, backend_ops: set) -> list[str]:
        import urllib.request
        import urllib.error

        if not guarded_routes or not backend_ops:
            return []

        backend_get_resources = {op[0].strip("/").split("/")[0] for op in backend_ops if op[1] == "get"}
        problems = []
        for route in sorted(guarded_routes):
            resource = route.strip("/").split("/")[0]
            if not resource or resource not in backend_get_resources:
                continue
            url = f"{backend_url.rstrip('/')}/{resource}"
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    status = resp.status
            except urllib.error.HTTPError as e:
                status = e.code
            except Exception:
                continue  # can't reach it at all - not this validator's concern

            if status == 200:
                problems.append(_tag("backend", f"GET /{resource} returns 200 with NO Authorization "
                                                f"header, but the frontend page for '{route}' is guarded "
                                                f"by an auth component - the backend isn't actually "
                                                f"enforcing the protection the UI implies"))
        return problems


class PerformanceValidator(Validator):
    """Lightweight, deterministic timers with generous thresholds -
    explicitly NOT benchmarking (a few hundred ms of variance is not this
    validator's concern). Only catches a genuinely hung/broken deployment,
    same spirit as the boot_timeout_s already used elsewhere."""
    name = "performance"

    _BOOT_THRESHOLD_S = 120
    _RESPONSE_THRESHOLD_S = 10

    def validate(self, boot_elapsed_s: float, frontend_url: str = "", backend_url: str = "") -> list[str]:
        import time
        import urllib.request

        problems = []
        if boot_elapsed_s > self._BOOT_THRESHOLD_S:
            problems.append(_tag("system", f"the stack took {boot_elapsed_s:.0f}s to become reachable - "
                                           f"unusually slow (threshold: {self._BOOT_THRESHOLD_S}s)"))

        for label, url in (("frontend", frontend_url), ("backend", backend_url)):
            if not url:
                continue
            t0 = time.time()
            try:
                urllib.request.urlopen(url, timeout=self._RESPONSE_THRESHOLD_S + 2)
            except Exception:
                continue  # reachability itself is a different validator's job
            elapsed = time.time() - t0
            if elapsed > self._RESPONSE_THRESHOLD_S:
                problems.append(_tag(label, f"{label} took {elapsed:.1f}s to respond - unusually slow "
                                            f"(threshold: {self._RESPONSE_THRESHOLD_S}s)"))
        return problems


def run_e2e_val_validators(project_dir, project_id: str, workspace: dict, e2e_boot: dict) -> list[str]:
    """E2E_VAL's integration engine (Phase 2 content) - flattened into a
    plain list[str], same reasoning as run_e2e_ut_validators. Gathers the
    shared inputs (schema tables, guarded routes, backend operations) ONCE
    and passes them to each validator, rather than each one re-deriving
    the same data independently."""
    if e2e_boot is None:
        return []

    host_ports = e2e_boot.get("host_ports", {})
    frontend_url = f"http://localhost:{host_ports['frontend']}/" if e2e_boot.get("has_frontend") else ""
    backend_url = f"http://localhost:{host_ports['backend']}/" if e2e_boot.get("has_backend") else ""

    from core.logger import get_logger
    logger = get_logger()

    def _run(name, result):
        problems.extend(result)
        logger.validator_result("testing", "val", name, "FAIL" if result else "PASS", issue_count=len(result))

    problems = []
    _run("PerformanceValidator", PerformanceValidator().validate(e2e_boot.get("boot_elapsed_s", 0), frontend_url, backend_url))

    if frontend_url:
        _run("ApiIntegrationValidator", ApiIntegrationValidator().validate(frontend_url))
    else:
        logger.validator_result("testing", "val", "ApiIntegrationValidator", "SKIPPED")

    if e2e_boot.get("has_database"):
        from skills.db_validators import build_schema_model
        schema = workspace.get("database", {}).get("schema", "")
        model = build_schema_model(schema) if schema else None
        expected_tables = set(model.tables.keys()) if model and not model.parse_error else set()
        _run("DatabaseIntegrationValidator", DatabaseIntegrationValidator().validate(project_id, host_ports, expected_tables))
    else:
        logger.validator_result("testing", "val", "DatabaseIntegrationValidator", "SKIPPED")

    if frontend_url and backend_url:
        frontend_files = workspace.get("frontend", {}).get("files", {})
        backend_files = workspace.get("backend", {}).get("files", {})
        from skills.fe_browser_validator import _guarded_routes
        from skills.fe_validators import _AUTH_GUARD_DEF_PATTERN
        from skills.be_validators import _extract_backend_operations

        all_frontend_content = "\n".join(c for p, c in frontend_files.items()
                                         if p.endswith((".js", ".jsx", ".ts", ".tsx")))
        guard_names = set(_AUTH_GUARD_DEF_PATTERN.findall(all_frontend_content))
        guarded_routes = _guarded_routes(frontend_files, guard_names) if guard_names else set()
        backend_ops = _extract_backend_operations(backend_files)
        _run("SecurityValidator", SecurityValidator().validate(backend_url, guarded_routes, backend_ops))
    else:
        logger.validator_result("testing", "val", "SecurityValidator", "SKIPPED")

    return problems


# ============================================================================
# Acceptance Test Runner + Regression Validator (Phase 3) - per the spec:
# "Do NOT generate tests here. Execute the generated Playwright end-to-end
# acceptance suite." This only EXECUTES what Frontend's own FE_RUN already
# wrote (test_frontend.spec.cjs, via the shared Acceptance Test Generator -
# see capabilities/frontend.py's run()), against E2E's own fresh, final
# full-stack boot, as the last integration gate before the whole project
# is considered done. Reuses the SAME .fe_validation_cache npm install/
# Chromium Frontend's own FE_VAL already set up (skills/fe_build_
# validator.py) - no second install, no second browser download.
# ============================================================================

_ACCEPTANCE_CONFIG_CJS = """module.exports = {
  testDir: '.',
  testMatch: 'test_frontend.spec.cjs',
  timeout: 30000,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL,
  },
  reporter: [['line'], ['json', { outputFile: 'e2e_acceptance_results.json' }]],
}
"""

_ACCEPTANCE_SUITE_TIMEOUT_S = 120


def run_acceptance_suite(project_dir, spec_content: str, frontend_url: str) -> tuple:
    """
    Re-executes Frontend's already-generated Playwright suite via a real
    `npx playwright test` in the SAME cache Frontend's FE_VAL already
    installed into - a different config file (JSON reporter, for per-test
    regression comparison below) is the only thing unique to this call.

    Returns (problem_strings, {test_title: passed_bool}) - the outcomes
    dict is empty if the suite couldn't run at all (harness-level problem,
    fails open) or its JSON report couldn't be parsed; only real test
    FAILURES (a non-zero exit with a parseable report) populate it with
    real per-test results.
    """
    logger = get_logger()
    cache_path = Path(project_dir) / ".fe_validation_cache"
    if not (cache_path / "node_modules" / "@playwright" / "test").exists():
        logger.warning("Acceptance Test Runner: @playwright/test isn't installed in the validation cache "
                       "- skipping (Frontend's own FE_VAL should have set this up already)")
        return [], {}

    (cache_path / "playwright.config.cjs").write_text(_ACCEPTANCE_CONFIG_CJS, encoding="utf-8")
    (cache_path / "test_frontend.spec.cjs").write_text(spec_content, encoding="utf-8")
    results_path = cache_path / "e2e_acceptance_results.json"
    results_path.unlink(missing_ok=True)

    env = {**os.environ, "PLAYWRIGHT_BASE_URL": frontend_url}
    try:
        result = subprocess.run(
            ["npx", "playwright", "test"], cwd=cache_path, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_ACCEPTANCE_SUITE_TIMEOUT_S, shell=True, env=env,
        )
    except subprocess.TimeoutExpired:
        return [f"acceptance suite did not finish within {_ACCEPTANCE_SUITE_TIMEOUT_S}s - "
                f"likely hung, not just slow"], {}

    test_outcomes = {}
    if results_path.exists():
        try:
            report = json.loads(results_path.read_text(encoding="utf-8"))
            for suite in report.get("suites", []):
                for spec in suite.get("specs", []):
                    test_outcomes[spec.get("title", "unknown test")] = bool(spec.get("ok", False))
        except (json.JSONDecodeError, OSError):
            pass

    if result.returncode == 0:
        return [], test_outcomes

    output = (result.stdout or "") + "\n" + (result.stderr or "")
    # Tagged "frontend" as a best-effort default (Playwright drives through
    # the UI, and Frontend is generally the fastest agent to iterate on a
    # broken user flow) - the true root cause can be either side; the raw
    # output above (and testing_report's free text, which the Supervisor
    # also reads) still carries the real detail either way.
    return [_tag("frontend", f"acceptance test(s) failed against the complete, really-booted system:\n"
                             f"{output[-1500:]}")], test_outcomes


class AcceptanceRegressionValidator(Validator):
    """Compares THIS round's per-test acceptance outcomes against the
    immediately preceding E2E_VAL run's outcomes (persisted in state) - a
    test that PASSED last time and FAILS now is a precise, named
    regression signal (a specific workflow that used to work), distinct
    from a test that has simply never passed yet (ordinary in-progress
    work, not a regression) - same severity distinction already drawn for
    Backend's ContractOperationValidator regression path."""
    name = "acceptance_regression"

    def validate(self, previous_outcomes: dict, current_outcomes: dict) -> list[str]:
        if not previous_outcomes:
            return []
        return [_tag("frontend", f"workflow '{name}' PASSED in the previous system integration run and "
                                 f"now FAILS - this is a regression, not incomplete new work")
                for name, was_ok in previous_outcomes.items()
                if was_ok and current_outcomes.get(name) is False]


# ============================================================================
# Holistic LLM Review (Phase 4) - the ONE subjective validation step inside
# E2E_VAL, per the spec: "produce findings only... must NEVER modify source
# code." Reuses quality_skills.review_code_skill + review_cache AS-IS,
# UNCHANGED prompt and all - that prompt's "real, blocking bugs only /
# DO NOT flag subjective opinions" discipline was deliberately tuned to
# avoid exactly the false-positive/unfixable-loop risk a broader, more
# subjective "architectural issues / cross-module inconsistencies" prompt
# would reintroduce. The only thing this adds is RICHER CONTEXT (Planner's
# architecture + acceptance_criteria, not just a bare task list) so the
# SAME disciplined reviewer can better judge "does this satisfy what was
# actually asked" - not a new, more permissive reviewer. Gated behind
# every deterministic check above (Phases 1-3) already passing - no point
# spending a model call scrutinizing an app already known broken by free
# checks.
# ============================================================================

MAX_REVIEW_REPEATS = 3  # same reasoning as the old e2e.py: a finding that
                        # keeps recurring despite Backend/Frontend's own
                        # many self-heal attempts is a subjective nitpick
                        # the model can't converge on, not a real defect.


def run_holistic_review(project_dir, workspace: dict, architecture: str, tasks: list,
                        acceptance_criteria: list, issue_history: list) -> tuple:
    """
    ONE final LLM pass over the complete assembled backend+frontend code,
    informed by Planner's own intent - does the implementation actually
    satisfy what was asked, is a required feature entirely missing, are
    there real security vulnerabilities or crash bugs static analysis
    missed. Verification only - no write tool is ever given to this call,
    so it structurally cannot modify code.

    Returns (blocking_findings, all_findings_for_issue_history) - blocking
    excludes a finding that's recurred >= MAX_REVIEW_REPEATS times across
    rounds despite Backend/Frontend's own self-heal attempts (can't
    converge on it - a contract-side or subjective nitpick), but
    all_findings still includes it so repeat-tracking keeps working.
    Fails open ([], []) if there's no code to review yet.
    """
    from skills.quality_skills import review_code_skill
    from skills.review_cache import load_review_cache, save_review_cache, split_changed_files, rebuild_cache

    all_files = {}
    for artifact_type in ("backend", "frontend"):
        for path, content in workspace.get(artifact_type, {}).get("files", {}).items():
            all_files[f"{artifact_type}/{path}"] = content
    if not all_files:
        return [], []

    cache = load_review_cache(project_dir)
    changed_files, current_hashes, cached_issues = split_changed_files(all_files, cache)

    new_issues_by_file = {}
    if changed_files:
        planner_context = [
            f"Architecture: {architecture}",
            f"Acceptance criteria: {acceptance_criteria}",
            f"Tasks: {tasks}",
        ]
        new_issues = review_code_skill(changed_files, planner_context)
        for issue in new_issues:
            new_issues_by_file.setdefault(issue.get("file", ""), []).append(issue)

    updated_cache = rebuild_cache(all_files, current_hashes, cache, set(changed_files), new_issues_by_file)
    save_review_cache(project_dir, updated_cache)

    all_findings = cached_issues + [i for issues in new_issues_by_file.values() for i in issues]

    repeat_counts = {}
    for past in issue_history:
        if past.get("source") != "holistic_review":
            continue
        key = (past.get("file"), str(past.get("severity", "medium")).lower())
        repeat_counts[key] = repeat_counts.get(key, 0) + 1

    blocking = []
    for issue in all_findings:
        key = (issue.get("file"), str(issue.get("severity", "medium")).lower())
        if repeat_counts.get(key, 0) >= MAX_REVIEW_REPEATS:
            continue
        blocking.append(issue)

    return blocking, all_findings
