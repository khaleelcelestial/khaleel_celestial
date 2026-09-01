"""
Real-Browser Validator - Phase 4 of the Frontend Validation Engine (FE_VAL).
Boots the REAL full stack (db + backend + frontend, via the SAME
docker_skills.py/e2e_skills.py machinery E2E itself uses for its own boot
check) and drives a real headless Chromium browser (Playwright) against it -
closing the gap no static check (Phase 1-3 above) can ever close: does the
app actually RENDER, actually NAVIGATE, actually TALK to a real backend, and
actually hold up across real viewport sizes and a real accessibility scan.

Why this reuses E2E's boot machinery instead of building a separate one:
`write_docker_assets` + `docker compose up` already produces the exact
shippable artifact (real schema.sql via docker-entrypoint-initdb.d, a real
backend, a real frontend, wired together via the same env vars a real
deployment uses) - building a second, different boot path here would test
something DIFFERENT from what actually ships, defeating the purpose.

Deliberately scoped down from the full spec's ambition, decided before
writing any code:
- "no overlapping elements" / "no hidden content" - a generic pixel-level
  bounding-box collision check across every element on a page has a very
  high false-positive rate (icons inside buttons, absolutely-positioned
  badges/tooltips/dropdowns are all legitimate, intentional overlaps) -
  not attempted. Responsive is scoped to the one reliable signal real QA
  tooling actually relies on: horizontal scroll at each viewport (always a
  real bug - a fixed-width element wider than the viewport is never
  intentional).
- No visual/pixel-diff/screenshot-comparison testing - there's no "golden"
  reference screenshot for a freshly generated project to diff against, so
  this would just be noise.
- Accessibility is axe-core's real ruleset filtered to "serious"/
  "critical" impact only - "moderate"/"minor" findings are real but
  numerous and often subjective (e.g. color-contrast on an intentional
  accent color), so including them risks this check never passing on any
  real project, defeating its purpose as a pass/fail gate.
- The auth-bypass check only tests routes CONFIRMED wrapped by a detected
  guard component (via the same regex evidence AuthGatingValidator uses in
  skills/fe_validators.py) - not every non-login route guessed to "probably
  need auth" (Planner doesn't emit that data - same documented gap noted
  throughout this validation engine).

Fails OPEN (returns no problems, logged warning) if Docker/Playwright isn't
available, or there's no frontend to boot at all (a static site) -
defense-in-depth on top of the Phase 1-3 static checks, not something that
should block the whole pipeline over a missing local Docker/Playwright
install.
"""

import os
import re
import subprocess
import time
from pathlib import Path

from core.logger import get_logger

_BOOT_TIMEOUT_S = 90
_NAV_TIMEOUT_MS = 15000
_MAX_ROUTES_VISITED = 8  # cap - a huge route list shouldn't make FE_VAL take forever
_VIEWPORTS = {
    "desktop": {"width": 1280, "height": 800},
    "tablet": {"width": 768, "height": 1024},
    "mobile": {"width": 375, "height": 812},
}
# "serious"/"critical" only - see module docstring for why moderate/minor
# findings are excluded from this pass/fail gate.
_AXE_BLOCKING_IMPACTS = {"serious", "critical"}


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _docker_available() -> bool:
    import shutil
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=15)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def validate_frontend_in_browser(project_dir, workspace: dict, playwright_spec_content: str = "") -> list[str]:
    """
    Boots db+backend+frontend for real via docker compose, waits for the
    frontend to genuinely respond, then drives a real Chromium browser
    through Runtime/Browser-console/Responsive/Accessibility/auth-bypass
    checks (see _run_browser_checks). Always tears the stack back down
    (this is a temporary verification boot, same as E2E's own - the real
    deployment is capabilities/cicd.py's job). playwright_spec_content, if
    given (the shared Acceptance Test Generator's Playwright output - see
    capabilities/frontend.py's run()), is executed for real via a real
    `npx playwright test` against this SAME already-booted stack (no
    second boot) as the Requirement Test Runner - a failing generated test
    fails FE_VAL just like any other problem here. Returns a list of
    problem strings (empty = the app genuinely renders, navigates, talks
    to the real backend, holds up across viewports, passes a real
    accessibility scan, and - if given - every generated Playwright test
    passes). Fails open per the module docstring.
    """
    logger = get_logger()

    frontend_files = workspace.get("frontend", {}).get("files", {})
    has_backend = bool(workspace.get("backend", {}).get("files"))
    if not frontend_files:
        return []

    if not _docker_available():
        logger.warning("Browser Validator: Docker isn't available - skipping the real-browser check")
        return []
    if not _playwright_available():
        logger.warning("Browser Validator: Playwright isn't installed - skipping the real-browser check")
        return []

    from skills.docker_skills import write_docker_assets
    from skills.project_registry import allocate_ports
    from skills.e2e_skills import http_reachable

    write_docker_assets(project_dir, workspace)
    host_ports = allocate_ports(Path(project_dir).name)

    try:
        up = subprocess.run(
            ["docker", "compose", "up", "--build", "-d"], cwd=str(project_dir),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning(f"Browser Validator: could not boot the stack ({e}) - skipping this check")
        return []

    if up.returncode != 0:
        _teardown(project_dir)
        return [f"frontend/backend/db fail to boot together via `docker compose up`: "
                f"{(up.stderr or up.stdout)[-1000:]}"]

    try:
        frontend_url = f"http://localhost:{host_ports['frontend']}/"
        frontend_ok, backend_ok = False, not has_backend
        deadline = time.time() + _BOOT_TIMEOUT_S
        while time.time() < deadline and not (frontend_ok and backend_ok):
            frontend_ok, _ = http_reachable(frontend_url)
            if has_backend:
                backend_ok, _ = http_reachable(f"http://localhost:{host_ports['backend']}/")
            if not (frontend_ok and backend_ok):
                time.sleep(2)

        if not frontend_ok:
            return ["frontend container started but never became reachable within the boot window - "
                    "see Startup/Runtime issues, not a frontend code defect this validator can pinpoint further"]

        problems = _run_browser_checks(project_dir, frontend_files, frontend_url)
        if not problems and playwright_spec_content:
            problems.extend(_run_playwright_spec(project_dir, playwright_spec_content, frontend_url))
        return problems
    finally:
        _teardown(project_dir)


def _teardown(project_dir) -> None:
    try:
        subprocess.run(["docker", "compose", "down"], cwd=str(project_dir), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _guarded_routes(frontend_files: dict, guard_names: set) -> set[str]:
    """Routes whose `element={<GuardName>...}` value ACTUALLY names one of
    the detected guard components - not every non-login route guessed to
    'probably need auth'. See module docstring for why this precision
    matters (Planner doesn't emit a structured 'which routes need auth'
    list to check against instead)."""
    from skills.fe_validators import _normalize_path

    routes = set()
    all_content = "\n".join(c for p, c in frontend_files.items() if p.endswith((".js", ".jsx", ".ts", ".tsx")))
    for guard in guard_names:
        pattern = re.compile(
            r'<Route\s[^>]*\bpath\s*=\s*[`\'"]([^`\'"]*)[`\'"][^>]*\belement\s*=\s*\{<' + re.escape(guard) + r'\b'
        )
        for raw in pattern.findall(all_content):
            normalized = _normalize_path(raw)
            if "{param}" not in normalized:
                routes.add(normalized)
    return routes


def _run_browser_checks(project_dir, frontend_files: dict, frontend_url: str) -> list[str]:
    from playwright.sync_api import sync_playwright
    from skills.fe_validators import _ROUTE_PATTERN, _extract_jsx_string_paths, _AUTH_GUARD_DEF_PATTERN

    all_content = "\n".join(c for p, c in frontend_files.items() if p.endswith((".js", ".jsx", ".ts", ".tsx")))
    guard_names = set(_AUTH_GUARD_DEF_PATTERN.findall(all_content))
    guarded_routes = _guarded_routes(frontend_files, guard_names) if guard_names else set()

    routes = sorted(_extract_jsx_string_paths(frontend_files, _ROUTE_PATTERN))
    static_routes = [r for r in routes if "{param}" not in r][:_MAX_ROUTES_VISITED]

    problems = []
    console_errors, page_errors, failed_requests = [], [], []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport=_VIEWPORTS["desktop"])
        page = context.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on("requestfailed", lambda req: failed_requests.append(f"{req.method} {req.url}"))

        # ---- Runtime Validator: does the app actually load and navigate? ----
        try:
            page.goto(frontend_url, timeout=_NAV_TIMEOUT_MS, wait_until="networkidle")
        except Exception as e:
            browser.close()
            return [f"frontend never finished loading at {frontend_url}: {str(e)[:300]}"]

        if not page.inner_text("body").strip():
            problems.append(f"the app renders a completely blank page at {frontend_url} (no visible text "
                            f"content) - likely a JS crash before React ever mounted")

        for route in static_routes:
            url = frontend_url.rstrip("/") + route
            try:
                page.goto(url, timeout=_NAV_TIMEOUT_MS, wait_until="networkidle")
            except Exception as e:
                problems.append(f"navigating to route '{route}' failed: {str(e)[:200]}")
                continue
            if not page.inner_text("body").strip():
                problems.append(f"route '{route}' renders a completely blank page")

        # ---- Auth-bypass check: a route CONFIRMED guarded must not leak
        # real content to an unauthenticated visitor (redirect away, or at
        # least show a login form, both count as correctly gated). ----
        for route in sorted(guarded_routes)[:_MAX_ROUTES_VISITED]:
            url = frontend_url.rstrip("/") + route
            try:
                page.goto(url, timeout=_NAV_TIMEOUT_MS, wait_until="networkidle")
            except Exception:
                continue
            redirected_away = route not in page.url
            has_login_form = page.locator('input[type="password"]').count() > 0
            if not redirected_away and not has_login_form and page.inner_text("body").strip():
                problems.append(f"route '{route}' is wrapped in an auth guard, but an UNAUTHENTICATED "
                                f"visit renders real page content instead of redirecting or showing a "
                                f"login form - the guard isn't actually enforcing anything")

        if page_errors:
            problems.append(f"the app threw {len(page_errors)} uncaught JavaScript exception(s) while "
                            f"loading/navigating: {page_errors[0][:300]}")
        if console_errors:
            problems.append(f"the browser console logged {len(console_errors)} real error(s): "
                            f"{console_errors[0][:300]}")
        if failed_requests:
            problems.append(f"{len(failed_requests)} network request(s) failed to load: "
                            f"{failed_requests[0][:300]}")

        # ---- Responsive Validator ----
        for name, size in _VIEWPORTS.items():
            page.set_viewport_size(size)
            try:
                page.goto(frontend_url, timeout=_NAV_TIMEOUT_MS, wait_until="networkidle")
            except Exception:
                continue
            scroll_width = page.evaluate("document.documentElement.scrollWidth")
            client_width = page.evaluate("document.documentElement.clientWidth")
            if scroll_width > client_width + 2:  # small tolerance for scrollbar rounding
                problems.append(f"horizontal scrolling detected at {name} viewport "
                                f"({size['width']}x{size['height']}) - content is "
                                f"{scroll_width - client_width}px wider than the viewport")

        # ---- Accessibility Validator (axe-core, real WCAG ruleset) ----
        axe_path = Path(project_dir) / ".fe_validation_cache" / "node_modules" / "axe-core" / "axe.min.js"
        if axe_path.exists():
            page.set_viewport_size(_VIEWPORTS["desktop"])
            try:
                page.goto(frontend_url, timeout=_NAV_TIMEOUT_MS, wait_until="networkidle")
                page.add_script_tag(content=axe_path.read_text(encoding="utf-8"))
                results = page.evaluate("async () => await axe.run()")
                blocking = [v for v in results.get("violations", []) if v.get("impact") in _AXE_BLOCKING_IMPACTS]
                for v in blocking[:5]:
                    problems.append(f"accessibility: [{v['impact']}] {v['id']} - {v['help']} "
                                    f"({len(v.get('nodes', []))} element(s) affected)")
            except Exception as e:
                logger = get_logger()
                logger.warning(f"Browser Validator: accessibility scan failed to run ({e}) - skipping it")

        browser.close()

    return problems


_PLAYWRIGHT_TIMEOUT_S = 120

# A pipeline-authored config (not LLM-generated) - same "config authored by
# the pipeline, test CONTENT generated by the LLM" split already used for
# Vitest (skills/fe_build_validator.py's _VITEST_CONFIG_JS). baseURL comes
# from an env var this module sets to the ALREADY-BOOTED stack's real
# frontend URL - the generated spec never needs to know or hardcode a port.
#
# ".cjs" (NOT ".js") is required here, not a style choice: a real generated
# project's package.json can legitimately set "type": "module" (confirmed
# on a real project during verification), which makes Node parse EVERY
# plain ".js" file as an ES module - `module.exports = {...}` is invalid
# syntax there and the config fails to load with a cryptic parse error.
# ".cjs" forces CommonJS interpretation unconditionally, regardless of
# whatever the project's own package.json happens to declare.
_PLAYWRIGHT_CONFIG_CJS = """module.exports = {
  testDir: '.',
  testMatch: 'test_frontend.spec.cjs',
  timeout: 30000,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL,
  },
  reporter: [['line']],
}
"""


def _run_playwright_spec(project_dir, spec_content: str, frontend_url: str) -> list[str]:
    """
    The Requirement Test Runner for Frontend user-flow tests: executes the
    shared Acceptance Test Generator's Playwright output via a real
    `npx playwright test`, reusing the SAME npm cache/install
    (.fe_validation_cache) skills/fe_build_validator.py already set up this
    round (no second throwaway install) - against the SAME already-booted
    stack this module just finished checking with Python's Playwright
    bindings (no second boot). `@playwright/test` is pinned to the exact
    same version as the Python `playwright` package (see
    fe_build_validator.py's _ESLINT_DEV_DEPENDENCIES) specifically so the
    Chromium binary already downloaded for THIS module's own checks is
    reused here too, not re-downloaded a second time.

    Returns a list of problem strings - empty means every generated test
    passed. Fails open (empty list) on any harness-level problem (the
    cache/install doesn't exist, Playwright's own CLI errors before
    running anything) - only real test FAILURES are reported as
    validation problems.
    """
    logger = get_logger()
    cache_path = Path(project_dir) / ".fe_validation_cache"
    if not (cache_path / "node_modules" / "@playwright" / "test").exists():
        logger.warning("Browser Validator: @playwright/test isn't installed in the validation cache - "
                       "skipping the generated Playwright test(s)")
        return []

    (cache_path / "playwright.config.cjs").write_text(_PLAYWRIGHT_CONFIG_CJS, encoding="utf-8")
    (cache_path / "test_frontend.spec.cjs").write_text(spec_content, encoding="utf-8")

    env = {**os.environ, "PLAYWRIGHT_BASE_URL": frontend_url}
    try:
        result = subprocess.run(
            ["npx", "playwright", "test"],
            cwd=cache_path, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_PLAYWRIGHT_TIMEOUT_S, shell=True, env=env,
        )
    except subprocess.TimeoutExpired:
        return [f"generated Playwright test(s) did not finish within {_PLAYWRIGHT_TIMEOUT_S}s - "
                f"likely hung, not just slow"]

    if result.returncode == 0:
        return []

    output = (result.stdout or "") + "\n" + (result.stderr or "")
    return [f"generated Playwright test(s) failed against the real, really-booted app:\n{output[-1500:]}"]
