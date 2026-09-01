"""
Backend structural validation - Phase 1 of the Backend Validation Engine,
same shape as skills/db_validators.py: a Validator base class, one focused
subclass per concern, fully deterministic (real Python `ast`, no LLM, no
subjective reasoning). This is BE_UT's new content, proving "the backend is
structurally correct" beyond what skills/quality_skills.py's existing
run_tests_skill already covers (syntax, undefined names, relative imports,
cross-file import existence, duplicate routes - those stay as-is, this file
is additive, not a replacement).

Deliberately NOT built here (confirmed redundant with existing checks
before writing any code):
- "Depends() resolves to a real function" - already covered by the
  existing cross-file-import-existence check (a Depends(x) reference to an
  undefined/unimported name is already caught either by pyflakes'
  undefined-name check or _check_backend_cross_file_imports) - a dedicated
  Depends()-aware checker would just re-detect the same problem a second,
  redundant way.
- Repository-layer checks - confirmed this pipeline's BACKEND_SYSTEM_PROMPT
  never generates a repository/DAO pattern; routers query the SQLAlchemy
  session directly. Not applicable.
- black/ruff/isort/mypy - none installed, and none add real correctness
  value here: style tools have no bugs to catch on code nobody hand-edits,
  and mypy on LLM-generated code with incomplete type hints is a reliable
  source of false-positive noise, not real bugs.
"""

import ast


class Validator:
    name = "base"

    def validate(self, backend_files: dict, schema_model=None) -> list[str]:
        raise NotImplementedError


def _module_key_for(path: str) -> str:
    """Same convention as quality_skills.py's helper of the same name -
    'routers/users.py' -> 'routers.users' - EXCEPT for an __init__.py,
    where Python's real import semantics resolve `from routers import X` to
    the package name itself ("routers"), never "routers.__init__". Without
    this special case, the real aggregator pattern this pipeline actually
    generates (routers/__init__.py collecting sub-routers, main.py
    importing from the "routers" package) resolves to the wrong module key
    and every alias traced through it silently fails to match - confirmed
    as a real false positive on exactly that pattern before this fix."""
    if not path.endswith(".py"):
        return path
    key = path[:-3].replace("/", ".").replace("\\", ".")
    return key[:-len(".__init__")] if key.endswith(".__init__") else key


def _is_column_assignment(value_node) -> bool:
    """True only for `x = Column(...)` - explicitly NOT `x = relationship(...)`,
    which is ORM-only and has no corresponding schema.sql column at all.
    Excluding relationship() is required, not optional - without it every
    single relationship attribute would false-positive as a "missing
    column"."""
    if not isinstance(value_node, ast.Call):
        return False
    func = value_node.func
    name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
    return name == "Column"


class RouterRegistrationValidator(Validator):
    """A router file can define `router = APIRouter(...)` and simply never
    get wired into the app anywhere - syntactically perfect, silently
    unreachable code, the exact same bug class as Frontend's "Sidebar
    defined but never rendered" (confirmed real earlier this session).

    Deliberately a simpler check than full call-graph reachability to
    `app.include_router(...)` specifically: this flags a router as
    registered the moment ANY `<something>.include_router(that_router)`
    call references it ANYWHERE in backend/, regardless of whether the
    consuming object is `app` or an intermediate aggregator router. That
    intentionally under-flags a rarer, more complex case (an aggregator
    that itself never reaches the app) in exchange for zero false positives
    on the common, real aggregation pattern this pipeline actually
    generates (routers/__init__.py collecting sub-routers, main.py
    including that one combined router)."""
    name = "router_registration"

    def validate(self, backend_files: dict, schema_model=None) -> list[str]:
        defined = []  # list of (file, varname)
        aliases = {}  # file -> {local_name: (module_key, original_name)}
        consumed = set()  # set of (file, varname) OR (module_key, original_name) - resolved below

        trees = {}
        for path, content in backend_files.items():
            if not path.endswith(".py"):
                continue
            try:
                trees[path] = ast.parse(content)
            except SyntaxError:
                continue  # already caught elsewhere - nothing useful to check here

        module_to_file = {_module_key_for(p): p for p in trees}

        for path, tree in trees.items():
            file_aliases = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for alias in node.names:
                        local = alias.asname or alias.name
                        file_aliases[local] = (node.module, alias.name)
            aliases[path] = file_aliases

            for node in tree.body:
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name) and _is_router_call(node.value)):
                    defined.append((path, node.targets[0].id))

        def _resolve(path: str, name: str) -> tuple[str, str]:
            """Resolve a local name in `path` to its defining (file, name) -
            either a local router variable, or traced through an import
            alias back to its origin module/file."""
            if name in aliases.get(path, {}):
                module_key, original = aliases[path][name]
                origin_file = module_to_file.get(module_key, module_key)
                return (origin_file, original)
            return (path, name)

        def _resolve_attr(path: str, local: str, attr: str) -> tuple[str, str]:
            """Resolve `<local>.<attr>` (e.g. `notes.router` from
            `app.include_router(notes.router)`) back to the (file, name) that
            defines it. `local` is traced through the SAME import-alias map as
            `_resolve()` - the real, common pattern this covers is
            `from routers import notes` followed by `notes.router`, where
            `local` ('notes') resolves to submodule 'routers.notes' and
            `attr` ('router') is the variable name inside that submodule -
            NOT an attribute lookup on 'routers' itself."""
            if local in aliases.get(path, {}):
                module_key, original = aliases[path][local]
                submodule_key = f"{module_key}.{original}" if original else module_key
                target_file = module_to_file.get(submodule_key, submodule_key)
                return (target_file, attr)
            return (path, attr)

        for path, tree in trees.items():
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "include_router" and node.args):
                    continue
                arg = node.args[0]
                if isinstance(arg, ast.Name):
                    consumed.add(_resolve(path, arg.id))
                elif isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name):
                    consumed.add(_resolve_attr(path, arg.value.id, arg.attr))

        return [f"'{varname}' (APIRouter) is defined in {path} but never passed to any "
                f".include_router(...) call anywhere in backend/ - this router's endpoints "
                f"are unreachable"
                for (path, varname) in defined if (path, varname) not in consumed]


def _is_router_call(value_node) -> bool:
    if not isinstance(value_node, ast.Call):
        return False
    func = value_node.func
    name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
    return name == "APIRouter"


class ModelSchemaConsistencyValidator(Validator):
    """Does every SQLAlchemy model's declared columns actually exist in
    schema.sql? This is the EXACT bug class manually diagnosed and fixed
    multiple times earlier this session (models.py declaring columns like
    `is_approved`/`visitor_first_name` that the live database never had) -
    confirmed as the single highest-value gap in the whole backend
    validation upgrade. A model class is identified by declaring
    `__tablename__` (the real, unambiguous SQLAlchemy declarative marker) -
    not by tracing which name the Base class happens to be imported as."""
    name = "model_schema_consistency"

    def validate(self, backend_files: dict, schema_model=None) -> list[str]:
        if schema_model is None or not schema_model.tables:
            return []

        problems = []
        for path, content in backend_files.items():
            if not path.endswith(".py"):
                continue
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                tablename = None
                columns = []
                for stmt in node.body:
                    if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                            and isinstance(stmt.targets[0], ast.Name)):
                        continue
                    target_name = stmt.targets[0].id
                    if target_name == "__tablename__" and isinstance(stmt.value, ast.Constant):
                        tablename = stmt.value.value
                    elif _is_column_assignment(stmt.value):
                        columns.append(target_name)

                if tablename is None:
                    continue  # not a SQLAlchemy table model

                schema_table = schema_model.tables.get(str(tablename).lower())
                if schema_table is None:
                    problems.append(f"model class '{node.name}' in {path} declares "
                                    f"__tablename__ = '{tablename}' but no such table exists in schema.sql")
                    continue

                missing = [c for c in columns if c.lower() not in schema_table.columns]
                if missing:
                    problems.append(f"model class '{node.name}' in {path} (table '{tablename}') declares "
                                    f"column(s) {missing} that don't exist in schema.sql - the backend "
                                    f"expects data the database doesn't actually have")
        return problems


BE_UT_VALIDATORS: list[Validator] = [
    RouterRegistrationValidator(),
    ModelSchemaConsistencyValidator(),
]


def run_be_ut_validators(backend_files: dict, schema: str = "") -> list[str]:
    """BE_UT's new structural-correctness engine, additive to run_tests_skill
    (syntax/undefined-names/relative-imports/cross-file-imports/duplicate-
    routes stay exactly as they are - see capabilities/backend.py's
    check_ut, which calls both). Returns a flat list of problem strings,
    empty = nothing new found."""
    if not backend_files:
        return []

    from core.logger import get_logger
    logger = get_logger()

    schema_model = None
    if schema:
        from skills.db_validators import build_schema_model
        schema_model = build_schema_model(schema)
        if schema_model.parse_error:
            schema_model = None  # database's own DB_UT already reports this - don't pile on here

    problems = []
    for validator in BE_UT_VALIDATORS:
        before = len(problems)
        problems.extend(validator.validate(backend_files, schema_model))
        issue_count = len(problems) - before
        logger.validator_result("backend", "ut", type(validator).__name__,
                                "FAIL" if issue_count else "PASS", issue_count=issue_count)
    return problems


# ============================================================================
# BE_VAL VALIDATORS (Phase 2) - "does the backend correctly implement the
# contract", not just "is it structurally sound". Same Validator base
# class/shape as BE_UT above. Supervisor still only ever sees ONE pass/fail
# from BackendCapability.check_val() - see capabilities/backend.py, which
# flattens run_be_val_validators()'s output into the exact same list[str]
# shape the old check_backend_matches_contract() returned.
# ============================================================================

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _normalize_path(path: str) -> str:
    """'/notes/{note_id}/' -> '/notes/{param}' - same param-collapsing and
    trailing-slash tolerance as db_validators.py's contract-matching, so a
    path-parameter name difference (e.g. openapi's '{id}' vs the backend's
    '{note_id}') never produces a false mismatch."""
    segments = [s for s in path.split("/") if s]
    normalized = "/".join("{param}" if s.startswith("{") else s for s in segments)
    return "/" + normalized if normalized else "/"


def _extract_contract_operations(openapi_spec: str) -> set[tuple[str, str]]:
    """Real YAML parse (not substring search) of every (path, http_method)
    operation openapi.yaml actually declares - the per-METHOD granularity is
    what makes this "CRUD-completeness" rather than just "path exists": a
    resource with GET implemented but POST missing is a real gap the old
    path-only check_backend_matches_contract could never see."""
    import yaml

    try:
        spec = yaml.safe_load(openapi_spec) or {}
    except yaml.YAMLError:
        return set()

    operations = set()
    for path, methods in (spec.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        normalized_path = _normalize_path(path)
        for method in methods:
            if method.lower() in _HTTP_METHODS:
                operations.add((normalized_path, method.lower()))
    return operations


def _extract_backend_operations(backend_files: dict) -> set[tuple[str, str]]:
    """Real AST parse of every (path, http_method) operation the backend
    actually implements - `@router.get(...)`/`@app.post(...)`-style
    decorators, resolving a same-file `APIRouter(prefix=...)` the same way
    quality_skills.py's regex-based extract_backend_routes already did.
    Deliberately does NOT resolve a prefix supplied later via
    `app.include_router(router, prefix=...)` in a different file - same
    documented limitation extract_backend_routes already had; the same-file
    prefix pattern is what this pipeline's BACKEND_SYSTEM_PROMPT actually
    generates."""
    trees = {}
    for path, content in backend_files.items():
        if not path.endswith(".py"):
            continue
        try:
            trees[path] = ast.parse(content)
        except SyntaxError:
            continue

    file_prefixes = {}
    for path, tree in trees.items():
        local_prefixes = {}
        for node in tree.body:
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name) and _is_router_call(node.value)):
                continue
            prefix = ""
            for kw in node.value.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    prefix = kw.value.value
            local_prefixes[node.targets[0].id] = prefix
        file_prefixes[path] = local_prefixes

    operations = set()
    for path, tree in trees.items():
        local_prefixes = file_prefixes.get(path, {})
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)):
                    continue
                method = deco.func.attr.lower()
                if method not in _HTTP_METHODS or not deco.args or not isinstance(deco.args[0], ast.Constant):
                    continue
                target = deco.func.value
                if not isinstance(target, ast.Name):
                    continue
                prefix = local_prefixes.get(target.id, "") if target.id != "app" else ""
                full_path = (prefix or "") + str(deco.args[0].value)
                operations.add((_normalize_path(full_path), method))
    return operations


def _extract_token_urls(backend_files: dict) -> list[tuple[str, str]]:
    """Every `OAuth2PasswordBearer(tokenUrl="...")` call's file + declared
    tokenUrl string, via real AST (not regex) - the exact real bug class
    hand-fixed earlier this session (a tokenUrl pointing at a path the
    backend never actually implemented, so the Swagger UI's login button
    silently 404'd)."""
    results = []
    for path, content in backend_files.items():
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
            if name != "OAuth2PasswordBearer":
                continue
            for kw in node.keywords:
                if kw.arg == "tokenUrl" and isinstance(kw.value, ast.Constant):
                    results.append((path, kw.value.value))
    return results


class ContractOperationValidator(Validator):
    """Does the backend implement every (path, method) operation openapi.yaml
    declares? Method-aware (CRUD-complete) replacement for the old
    path-only check_backend_matches_contract - a resource missing just its
    DELETE method, say, is now a real, individually-reported gap instead of
    invisible because the path itself already has a GET.

    Takes precomputed operation sets and an optional `regressions` set (see
    run_be_val_validators) so the SAME missing-operation list can carry a
    sharper "this used to work" message where applicable, without a second,
    duplicate validator re-deriving the same sets."""
    name = "contract_operations"

    def validate(self, backend_ops: set, contract_ops: set, regressions: set = frozenset()) -> list[str]:
        if not contract_ops:
            return []
        problems = []
        for path, method in sorted(contract_ops - backend_ops):
            if (path, method) in regressions:
                problems.append(
                    f"REGRESSION: {method.upper()} {path} was previously implemented and is still "
                    f"required by openapi.yaml, but backend/ no longer implements it - this looks like "
                    f"accidental code loss, not incomplete progress"
                )
            else:
                problems.append(f"openapi.yaml declares {method.upper()} {path} but no backend route implements it")
        return problems


class AuthenticationValidator(Validator):
    """Conditional - only runs when the backend itself declares
    OAuth2PasswordBearer(tokenUrl=...) somewhere. A project with no
    token-based auth has nothing for this to check, and running it
    unconditionally would risk false positives on non-auth projects.
    Confirmed real bug class (hand-fixed earlier this session): tokenUrl
    pointed at a path the backend didn't actually implement."""
    name = "authentication"

    def validate(self, backend_files: dict, backend_ops: set) -> list[str]:
        token_urls = _extract_token_urls(backend_files)
        if not token_urls:
            return []  # no token-based auth in this backend - nothing to check

        implemented_paths = {p for p, _ in backend_ops}
        problems = []
        for file, token_url in token_urls:
            if _normalize_path(token_url) not in implemented_paths:
                problems.append(
                    f"{file} declares OAuth2PasswordBearer(tokenUrl='{token_url}') but no backend route "
                    f"implements '{token_url}' - login requests to that URL will 404"
                )
        return problems


def run_be_val_validators(backend_files: dict, openapi_spec: str = "",
                          previous_backend_files: dict = None, previous_openapi_spec: str = "",
                          run_startup_check: bool = True, test_file_content: str = "") -> list[str]:
    """BE_VAL's engine - contract/CRUD-completeness (method-aware) plus a
    conditional authentication check, and (if those all pass) a real Docker
    boot-and-serve check - flattened into the same list[str] shape the old
    check_backend_matches_contract() returned so BackendCapability.check_val()
    (and the Supervisor, which only ever sees ONE pass/fail from it) needed
    zero changes.

    previous_backend_files/previous_openapi_spec (BE_RUN's snapshot of what
    was on disk/in the contract BEFORE this round's write - see
    capabilities/backend.py's run(), mirroring database.py's previous_schema
    pattern) are what let this distinguish a genuine REGRESSION (an
    operation that was implemented and required before, still required now,
    but silently gone) from ordinary in-progress work (a newly-required
    operation nobody has built yet) - the same severity distinction
    db_validators.py's MigrationValidator draws for schema.sql, applied here
    to backend routes instead of tables/columns.

    The Startup Validator (skills/be_startup_validator.py) only runs when
    everything else already passed - it costs real wall-clock time (a full
    Docker build), so there's no point paying that cost to re-confirm a
    backend already known to be broken by checks that are free.
    run_startup_check=False skips it entirely (e.g. for fast tests that
    don't want to touch Docker) - same knob shape as db_validators.py's
    run_val_validators(run_postgres_check=...). test_file_content, if given
    (the shared Acceptance Test Generator's output - see backend.py's
    run()), is executed for real against that same really-booted container
    as the Requirement Test Runner - a failing generated test fails BE_VAL
    just like any other problem here."""
    if not backend_files:
        return []

    from core.logger import get_logger
    logger = get_logger()

    backend_ops = _extract_backend_operations(backend_files)
    contract_ops = _extract_contract_operations(openapi_spec) if openapi_spec else set()

    regressions = set()
    if previous_backend_files and previous_openapi_spec:
        previous_backend_ops = _extract_backend_operations(previous_backend_files)
        previous_contract_ops = _extract_contract_operations(previous_openapi_spec)
        regressions = (previous_backend_ops & previous_contract_ops & contract_ops) - backend_ops

    problems = []
    for name, result in (
        ("ContractOperationValidator", ContractOperationValidator().validate(backend_ops, contract_ops, regressions)),
        ("AuthenticationValidator", AuthenticationValidator().validate(backend_files, backend_ops)),
    ):
        problems.extend(result)
        logger.validator_result("backend", "val", name, "FAIL" if result else "PASS", issue_count=len(result))

    if not problems and run_startup_check:
        from skills.be_startup_validator import validate_backend_startup
        startup_problems = validate_backend_startup(backend_files, test_file_content)
        problems.extend(startup_problems)
        logger.validator_result("backend", "val", "StartupValidator",
                                "FAIL" if startup_problems else "PASS", issue_count=len(startup_problems))

    return problems
