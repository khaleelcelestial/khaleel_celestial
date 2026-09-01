"""
Frontend static Validation Engine - Phase 3 of the Frontend Validation
Engine (FE_VAL). Same Validator base class/shape as db_validators.py/
be_validators.py: fully deterministic (real regex/AST-lite parsing, no LLM,
no subjective reasoning) - this is FE_VAL's static-layer content, proving
"the frontend correctly implements the contract, navigation, and auth
gating it's supposed to", distinct from FE_UT's "the frontend is
structurally correct".

No real JS/JSX AST parser is available in Python (unlike pglast for SQL),
so these validators use the SAME best-effort regex approach already
established and proven in quality_skills.py's existing frontend checks
(_check_duplicate_router_mount, _check_layout_components_rendered) - this
is a deliberate, documented tradeoff, not an oversight.

Deliberately NOT built here (confirmed not reliably buildable without an
upstream change, before writing any code):
- A strict Planner Validator ("does every page Planner intended actually
  exist") - Planner doesn't emit a structured page/route list, only free-
  text architecture + acceptance_criteria (the exact same gap that made
  Backend's Planner Validator unbuildable too - see be_validators.py).
- Full route-reachability analysis (tracing every possible click path) -
  NavigationConsistencyValidator below is deliberately a simpler,
  self-consistency check instead (see its own docstring for the tradeoff).
"""

import re

_PATH_TEMPLATE_PATTERN = re.compile(r"\$\{[^}]*\}")
_PARAM_BRACE_PATTERN = re.compile(r"\{[^}]*\}")
_PARAM_COLON_PATTERN = re.compile(r":\w+")


def _normalize_path(raw: str) -> str:
    """Collapses template-literal interpolation ('/tasks/${id}'), react-
    router v6 param syntax ('/tasks/:id'), and any remaining '{expr}' into
    a single '{param}' placeholder, so a Route's declared path, a Link's
    target, and an axios/fetch call's literal all compare equal regardless
    of which syntax each one happens to use."""
    path = _PATH_TEMPLATE_PATTERN.sub("{param}", raw)
    path = _PARAM_COLON_PATTERN.sub("{param}", path)
    path = _PARAM_BRACE_PATTERN.sub("{param}", path)
    return path.rstrip("/") or "/"


class Validator:
    name = "base"

    def validate(self, *args, **kwargs) -> list[str]:
        raise NotImplementedError


# ============================================================================
# API Contract Validator - method-aware upgrade of the old
# check_frontend_matches_backend (path-only substring/regex match). Reuses
# be_validators.py's own contract/backend-operation extractors rather than
# re-implementing YAML parsing or backend AST parsing a second time.
# ============================================================================

_AXIOS_CALL_PATTERN = re.compile(r"""axios\.(get|post|put|delete|patch)\(\s*[`'"]([^`'"]*)[`'"]""")
_FETCH_CALL_PATTERN = re.compile(r"""fetch\(\s*[`'"]([^`'"]*)[`'"](?:\s*,\s*\{([^}]*)\})?""", re.DOTALL)
_FETCH_METHOD_PATTERN = re.compile(r"""method\s*:\s*[`'"](\w+)[`'"]""")


def _extract_path_literal(raw: str) -> str:
    idx = raw.find("/")
    return _normalize_path(raw[idx:]) if idx != -1 else ""


def extract_frontend_api_operations(files: dict) -> set[tuple[str, str]]:
    """Method-aware upgrade of quality_skills.py's extract_frontend_api_paths
    (which only tracked paths, not methods) - axios.<method>(...) calls give
    the method directly; a bare fetch(url) defaults to GET per the Fetch
    API spec, fetch(url, {method: 'POST', ...}) is read from its options
    object."""
    ops = set()
    for path, content in files.items():
        if not path.endswith((".js", ".jsx", ".ts", ".tsx")):
            continue
        for method, raw in _AXIOS_CALL_PATTERN.findall(content):
            normalized = _extract_path_literal(raw)
            if normalized:
                ops.add((normalized, method.lower()))
        for raw, opts in _FETCH_CALL_PATTERN.findall(content):
            normalized = _extract_path_literal(raw)
            if not normalized:
                continue
            method_match = _FETCH_METHOD_PATTERN.search(opts or "")
            ops.add((normalized, method_match.group(1).lower() if method_match else "get"))
    return ops


class FrontendContractValidator(Validator):
    """Does every API call the frontend actually makes correspond to a
    real backend route or contract operation - the exact bug class
    ('frontend calls a URL/method the backend never implemented') no
    syntax check can ever catch. Method-aware: a frontend calling DELETE
    on a path that only has GET/POST implemented is now caught, where the
    old path-only version would have silently passed. One-directional by
    design (frontend -> must exist in backend/contract) - the reverse
    isn't meaningful, a page legitimately doesn't have to exercise every
    CRUD verb a resource supports."""
    name = "api_contract"

    def validate(self, frontend_files: dict, openapi_spec: str = "", backend_files: dict = None) -> list[str]:
        frontend_ops = extract_frontend_api_operations(frontend_files)
        if not frontend_ops:
            return []

        from skills.be_validators import _extract_contract_operations, _extract_backend_operations
        known_ops = set()
        if openapi_spec:
            known_ops |= _extract_contract_operations(openapi_spec)
        if backend_files:
            known_ops |= _extract_backend_operations(backend_files)
        if not known_ops:
            return []  # nothing generated yet to validate against

        return [f"frontend calls {method.upper()} {path} but no backend route/contract operation matches it"
                for path, method in sorted(frontend_ops - known_ops)]


# ============================================================================
# Navigation Consistency Validator - a route with no way to reach it from
# the UI, or a nav link pointing at a route that doesn't exist. Regex-based
# "reachability" evidence deliberately covers <Link>/<NavLink> JSX tags,
# useNavigate()-style programmatic navigate(...) calls, AND internal <a
# href> tags - a route only reachable via navigate() (not a static <Link>)
# is common and legitimate (e.g. "redirect after save"), so restricting
# evidence to just <Link> would false-positive on that real pattern.
# ============================================================================

_ROUTE_PATTERN = re.compile(r"""<Route\s[^>]*\bpath\s*=\s*[{]?[`'"]([^`'"]*)[`'"][}]?""")
_LINK_PATTERN = re.compile(r"""<(?:Link|NavLink)\s[^>]*\bto\s*=\s*[{]?[`'"]([^`'"]*)[`'"][}]?""")
_NAVIGATE_CALL_PATTERN = re.compile(r"""\bnavigate\(\s*[`'"]([^`'"]*)[`'"]""")
_ANCHOR_HREF_PATTERN = re.compile(r"""<a\s[^>]*\bhref\s*=\s*[{]?[`'"](/[^`'"]*)[`'"][}]?""")

# Data-driven nav lists - `<NavLink to={item.to}>` rendered from an array
# like `[{ to: '/x', label: 'X' }, ...].map(...)` - are idiomatic, common
# React and are NOT detectable by _LINK_PATTERN at all (the `to` attribute
# holds a JS expression, not a literal string). Confirmed real: a generated
# Sidebar.jsx used exactly this pattern and every one of its links was
# false-positive-flagged as unreachable, which then caused the repair loop
# to thrash between this (correct) form and an inline-literal-links form
# (detectable, but a worse component) across retries. Only trust `to:
# '/path'` object-literal keys as nav evidence in files that also render at
# least one <Link>/<NavLink> with a dynamic (non-literal) `to={...}` - this
# keeps an unrelated "to:" field elsewhere (e.g. an email/message payload)
# from being misread as navigation evidence.
_DYNAMIC_NAV_TAG_PATTERN = re.compile(r"<(?:Link|NavLink)\s[^>]*\bto\s*=\s*\{")
_NAV_ITEM_OBJECT_PATTERN = re.compile(r"""\bto\s*:\s*[`'"]([^`'"]*)[`'"]""")

# The equally common row-navigation pattern of building a path into a
# template-literal variable first, then passing it BY NAME to navigate()/
# <Link to={...}> (e.g. `const detailPath = `/users/${user.id}`; ...
# navigate(detailPath)` or `<Link to={detailPath}>`) - confirmed real, same
# blind spot as the data-driven nav-array case above: _NAVIGATE_CALL_PATTERN
# and _LINK_PATTERN only match a literal string argument/attribute, never a
# bare identifier, so a perfectly correct, idiomatic row-click-to-detail
# page was flagged as unreachable for the same structural reason.
_TEMPLATE_LITERAL_VAR_PATTERN = re.compile(r"""\b(?:const|let|var)\s+(\w+)\s*=\s*`(/[^`]*)`""")

# Route segments that legitimately have no nav-menu entry (auth flow pages,
# the catch-all/not-found route) - same "exempt utility paths" reasoning as
# be_validators.py's _NON_RESOURCE_PATH_SEGMENTS concept, applied to
# navigation instead of API resources.
_NAV_EXEMPT_FIRST_SEGMENTS = {"login", "register", "signup", "forgot-password", "reset-password",
                              "logout", "*", "404", "not-found", ""}


def _extract_jsx_string_paths(files: dict, pattern: re.Pattern) -> set[str]:
    paths = set()
    for path, content in files.items():
        if not path.endswith((".js", ".jsx", ".ts", ".tsx")):
            continue
        for raw in pattern.findall(content):
            normalized = _normalize_path(raw)
            if normalized:
                paths.add(normalized)
    return paths


def _extract_dynamic_navitem_paths(files: dict) -> set[str]:
    """See _NAV_ITEM_OBJECT_PATTERN's comment - recovers reachability
    evidence for data-driven nav lists that _LINK_PATTERN structurally
    cannot see."""
    paths = set()
    for path, content in files.items():
        if not path.endswith((".js", ".jsx", ".ts", ".tsx")):
            continue
        if not _DYNAMIC_NAV_TAG_PATTERN.search(content):
            continue
        for raw in _NAV_ITEM_OBJECT_PATTERN.findall(content):
            normalized = _normalize_path(raw)
            if normalized:
                paths.add(normalized)
    return paths


def _extract_template_variable_nav_paths(files: dict) -> set[str]:
    """See _TEMPLATE_LITERAL_VAR_PATTERN's comment. Only trusts a
    template-literal variable as nav evidence if it's actually passed to
    navigate(...) or a <Link>/<NavLink> to={...} by name somewhere in the
    same file - a variable that's merely declared and used for something
    else (e.g. a fetch URL) is not navigation evidence."""
    paths = set()
    for path, content in files.items():
        if not path.endswith((".js", ".jsx", ".ts", ".tsx")):
            continue
        for var_name, template in _TEMPLATE_LITERAL_VAR_PATTERN.findall(content):
            used_in_navigate = re.search(r"\bnavigate\(\s*" + re.escape(var_name) + r"\s*[,)]", content)
            used_in_link = re.search(r"\bto\s*=\s*\{\s*" + re.escape(var_name) + r"\s*\}", content)
            if not (used_in_navigate or used_in_link):
                continue
            normalized = _normalize_path(template)
            if normalized:
                paths.add(normalized)
    return paths


class NavigationConsistencyValidator(Validator):
    """Deliberately a SELF-consistency check, not full reachability
    analysis: does every <Route> have SOME evidence it's reachable from
    the UI (a nav <Link>/<NavLink>, a programmatic navigate() call, or an
    internal <a href>), and does every nav target point at a real route?
    This can't verify Planner's INTENDED navigation structure (Planner
    doesn't emit one - see module docstring), but it catches the concrete,
    confirmed bug class of a page that exists in the router yet has no way
    for a user to ever reach it, and the reverse (a broken nav link)."""
    name = "navigation_consistency"

    def validate(self, frontend_files: dict) -> list[str]:
        routes = _extract_jsx_string_paths(frontend_files, _ROUTE_PATTERN)
        if not routes:
            return []  # no <Route> usage detected at all - not this validator's business (or not react-router)

        reachable = (_extract_jsx_string_paths(frontend_files, _LINK_PATTERN)
                    | _extract_jsx_string_paths(frontend_files, _NAVIGATE_CALL_PATTERN)
                    | _extract_jsx_string_paths(frontend_files, _ANCHOR_HREF_PATTERN)
                    | _extract_dynamic_navitem_paths(frontend_files)
                    | _extract_template_variable_nav_paths(frontend_files))

        problems = []
        for route in sorted(routes):
            first_segment = route.strip("/").split("/")[0].lower()
            if first_segment in _NAV_EXEMPT_FIRST_SEGMENTS:
                continue
            if route not in reachable:
                problems.append(f"route '{route}' is defined but has no <Link>/<NavLink>, navigate() call, "
                                f"or <a href> anywhere pointing at it - a page a user has no way to reach")

        nav_links_only = _extract_jsx_string_paths(frontend_files, _LINK_PATTERN)
        for link in sorted(nav_links_only):
            if link not in routes and link.strip("/").split("/")[0].lower() not in _NAV_EXEMPT_FIRST_SEGMENTS:
                problems.append(f"navigation link '{link}' does not correspond to any defined <Route> - "
                                f"clicking it leads nowhere")
        return problems


# ============================================================================
# Auth Gating Validator - conditional, only runs when the frontend itself
# defines a route-guard component. Directly evidenced by the exact real bug
# class fixed by hand earlier this session (a defined-but-unused guard).
# ============================================================================

_AUTH_GUARD_DEF_PATTERN = re.compile(
    # No leading [A-Z] requirement before the keyword itself - the keyword
    # names (ProtectedRoute/PrivateRoute/...) already start with a capital
    # letter, so requiring one MORE capital letter before it would make the
    # bare name alone (e.g. exactly "ProtectedRoute", no prefix) fail to
    # match at all, since \w* has nothing left to consume before the
    # literal keyword must start. Confirmed as a real bug via a synthetic
    # test before this fix - only a PREFIXED name like "MyProtectedRoute"
    # matched, the far more common bare name did not.
    r"\b(?:function|const|class)\s+(\w*(?:ProtectedRoute|PrivateRoute|RequireAuth|AuthGuard)\w*)\b"
)


class AuthGatingValidator(Validator):
    """Conditional - only runs when the frontend defines a
    ProtectedRoute/PrivateRoute/RequireAuth/AuthGuard-style component (the
    app has opted into route-level auth gating at all; a project with no
    such pattern has nothing for this to check). Confirmed real bug class
    (hand-fixed earlier this session): such a component existing and even
    being imported is not the same as it actually wrapping the routes that
    need it - if it's defined but never rendered as a JSX tag, every
    'protected' page is actually wide open. Deliberately NOT attempting
    token-expiry/refresh or flash-of-protected-content checks here - those
    need a real running browser, not static analysis (deferred to the
    Playwright-based Runtime Validator)."""
    name = "auth_gating"

    def validate(self, frontend_files: dict) -> list[str]:
        all_content = "\n".join(c for p, c in frontend_files.items() if p.endswith((".js", ".jsx", ".ts", ".tsx")))
        guard_names = set(_AUTH_GUARD_DEF_PATTERN.findall(all_content))
        if not guard_names:
            return []

        unused = [name for name in sorted(guard_names) if not re.search(r"<" + re.escape(name) + r"\b", all_content)]
        if unused:
            return [f"auth-guard component(s) {', '.join(unused)} are defined but never rendered as a JSX "
                    f"tag anywhere - any route meant to be protected by them is actually completely open"]
        return []


# ============================================================================
# Update Validation (Phase 5) - ADVISORY ONLY, deliberately not part of
# run_fe_val_validators' hard-blocking list. See RouteRegressionValidator's
# docstring for exactly why: Database/Backend's equivalent regression
# checks (db_validators.py's MigrationValidator, be_validators.py's
# ContractOperationValidator regression path) can both tell an INTENTIONAL
# removal apart from an accidental one via a real signal (an explicit DROP
# statement; the operation also no longer being required by the current
# contract) - Frontend has no equivalent. Planner's architecture/tasks are
# free text, not a structured page list, so there's no reliable way to
# check "is this page still supposed to exist" the way a contract or a
# DROP statement lets Database/Backend check it. Hard-blocking on an
# unreliable signal would make every legitimate navigation redesign an
# infinite retry loop - worse than the gap it would close. Surfaced as
# feedback text instead (visible to the next FE_RUN attempt and to anyone
# reading logs), not a pass/fail gate.
# ============================================================================

class RouteRegressionValidator(Validator):
    """Diffs the previous round's routes against this round's - a route
    that existed before and is now completely gone is EITHER a real,
    accidental regression OR an intentional redesign; this validator
    can't tell which (see module-level comment above), so it's surfaced as
    advisory feedback only, never a blocking failure."""
    name = "route_regression"

    def validate(self, previous_frontend_files: dict, frontend_files: dict) -> list[str]:
        if not previous_frontend_files:
            return []
        old_routes = _extract_jsx_string_paths(previous_frontend_files, _ROUTE_PATTERN)
        new_routes = _extract_jsx_string_paths(frontend_files, _ROUTE_PATTERN)
        removed = sorted(old_routes - new_routes)
        if not removed:
            return []
        return [f"route(s) {', '.join(removed)} existed before this round's changes and are now gone - "
                f"if this wasn't an intentional redesign, it's a regression"]


def check_route_regressions(previous_frontend_files: dict, frontend_files: dict) -> list[str]:
    """Advisory-only wrapper - see the Update Validation comment above for
    why this is deliberately kept separate from run_fe_val_validators'
    blocking return value."""
    return RouteRegressionValidator().validate(previous_frontend_files, frontend_files)


def run_fe_val_validators(frontend_files: dict, openapi_spec: str = "", backend_files: dict = None) -> list[str]:
    """FE_VAL's static engine - API contract (method-aware), navigation
    self-consistency, and conditional auth-gating - flattened into the
    same list[str] shape the old check_frontend_matches_backend() returned
    so FrontendCapability.check_val() (and the Supervisor, which only ever
    sees ONE pass/fail from it) needed zero changes."""
    if not frontend_files:
        return []

    from core.logger import get_logger
    logger = get_logger()

    problems = []
    for name, result in (
        ("FrontendContractValidator", FrontendContractValidator().validate(frontend_files, openapi_spec, backend_files)),
        ("NavigationConsistencyValidator", NavigationConsistencyValidator().validate(frontend_files)),
        ("AuthGatingValidator", AuthGatingValidator().validate(frontend_files)),
    ):
        problems.extend(result)
        logger.validator_result("frontend", "val", name, "FAIL" if result else "PASS", issue_count=len(result))
    return problems
