"""
Project Index (Code Map) - a lightweight, INTERNAL, cached knowledge layer
over an already-generated project, used only by the Generation Strategy
Engine's incremental mode (skills/incremental_codegen.py) to seed a tool-
calling agent with likely-relevant file candidates before it ever calls
list_files(). The Supervisor is completely unaware this exists - it never
appears in any stage's pass/fail decision, only as a hint inside an
incremental-mode prompt.

The filesystem remains the source of truth, always. This index is a cache,
never authoritative - if it's missing, stale, or wrong, the fallback is
always to just call list_files()/search_files() for real (see
skills/incremental_codegen.py) and rebuild the affected entries. Nothing
in the pipeline's pass/fail path depends on this file being present,
correct, or even parseable.

Deliberately NOT an AST/compiler/language-server-grade index - every
extractor here reuses the SAME regex/lightweight-AST logic already proven
in this session's Quality Framework validators (db_validators.py's
build_schema_model, be_validators.py's operation/model extraction,
fe_validators.py's route/API-call extraction) rather than a new, heavier
parsing layer. "Good enough to point an agent at the right 2-5 files" is
the bar, not "understands the code."

Storage: one JSON file per project (.project_index.json), matching the
pipeline's existing caching pattern (skills/review_cache.py's
.review_cache.json, skills/project_registry.py's .project.json) rather
than introducing a new storage technology (SQLite, etc.) for no proven
benefit at this scale.
"""

import ast
import hashlib
import json
import re
from pathlib import Path

from core.logger import get_logger

_INDEX_VERSION = 1
_INDEX_FILENAME = ".project_index.json"

_TEST_FILENAMES = {"test_database.py", "test_backend.py", "test_frontend.test.jsx", "test_frontend.spec.cjs"}
_CONFIG_FILENAMES = {"Dockerfile", "docker-compose.yml", "compose.yaml", "package.json", "requirements.txt",
                    "pyproject.toml", ".env", ".env.example", "vite.config.js", "tailwind.config.js"}

_BARE_IMPORT_PATTERN = re.compile(r'^\s*(?:from|import)\s+([\w.]+)', re.MULTILINE)


def _index_path(project_dir: Path) -> Path:
    return Path(project_dir) / _INDEX_FILENAME


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def _extract_backend_file_entry(path: str, content: str) -> dict:
    """Routes + SQLAlchemy model tablename/columns for one backend .py
    file. Reuses the SAME extraction approach already proven in
    be_validators.py (Phase 1/2 of the Backend Quality Framework) -
    written as fresh, small, standalone functions here rather than
    refactoring those validators to share code, to avoid any risk of
    regressing already-verified validator behavior for a cache that's
    explicitly allowed to be imperfect."""
    entry = {"routes": [], "models": [], "imports": []}
    if not path.endswith(".py"):
        return entry

    entry["imports"] = sorted(set(_BARE_IMPORT_PATTERN.findall(content)))

    try:
        tree = ast.parse(content)
    except SyntaxError:
        return entry

    # Same-file APIRouter(prefix=...) resolution - without this, a route
    # decorated `@router.post("/create")` under `router = APIRouter(
    # prefix="/users")` would record the bucket-clustering key as "create"
    # instead of "users", exactly the false-entity noise confirmed via a
    # live run against a real project before this fix (be_validators.py's
    # own _extract_backend_operations already resolves this correctly for
    # its own purpose, but operates on ALL backend files at once and
    # doesn't retain per-file attribution, which this index needs - so
    # this mirrors its same-file-prefix logic rather than reusing it as-is).
    local_prefixes = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Call)):
            continue
        func = node.value.func
        call_name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
        if call_name != "APIRouter":
            continue
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                prefix = kw.value.value
        local_prefixes[node.targets[0].id] = prefix

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for deco in node.decorator_list:
                if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)):
                    continue
                method = deco.func.attr.lower()
                target = deco.func.value
                var_name = target.id if isinstance(target, ast.Name) else None
                if method in ("get", "post", "put", "patch", "delete") and deco.args \
                        and isinstance(deco.args[0], ast.Constant):
                    prefix = local_prefixes.get(var_name, "") if var_name != "app" else ""
                    full_path = (prefix or "") + str(deco.args[0].value)
                    entry["routes"].append([full_path, method])
        elif isinstance(node, ast.ClassDef):
            tablename = None
            columns = []
            for stmt in node.body:
                if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                        and isinstance(stmt.targets[0], ast.Name)):
                    continue
                name = stmt.targets[0].id
                if name == "__tablename__" and isinstance(stmt.value, ast.Constant):
                    tablename = stmt.value.value
                elif isinstance(stmt.value, ast.Call):
                    func = stmt.value.func
                    call_name = func.id if isinstance(func, ast.Name) else (
                        func.attr if isinstance(func, ast.Attribute) else None)
                    if call_name == "Column":
                        columns.append(name)
            if tablename:
                entry["models"].append({"class": node.name, "table": str(tablename).lower(), "columns": columns})
    return entry


def _extract_frontend_file_entry(path: str, content: str) -> dict:
    """Routes + API calls + component name for one frontend file. Reuses
    fe_validators.py's own regex patterns directly (same module, same
    proven behavior) rather than duplicating them."""
    entry = {"routes": [], "api_calls": [], "component": None}
    if not path.endswith((".js", ".jsx", ".ts", ".tsx")):
        return entry

    from skills.fe_validators import _ROUTE_PATTERN, _normalize_path, extract_frontend_api_operations

    entry["routes"] = sorted({_normalize_path(m) for m in _ROUTE_PATTERN.findall(content)})
    entry["api_calls"] = sorted(f"{method.upper()} {path_}" for path_, method in
                                extract_frontend_api_operations({path: content}))

    component_match = re.search(r"\b(?:function|const|class)\s+([A-Z]\w*)\b", content)
    if component_match:
        entry["component"] = component_match.group(1)
    return entry


def _classify(path: str) -> str:
    name = Path(path).name
    if path in ("schema.sql",):
        return "schema"
    if path in ("openapi.yaml",):
        return "contract"
    if name in _TEST_FILENAMES:
        return "test"
    if name in _CONFIG_FILENAMES:
        return "config"
    if path.startswith("backend/"):
        return "backend"
    if path.startswith("frontend/"):
        return "frontend"
    return "other"


_FILENAME_TO_STAGE = {
    "schema.sql": "database", "openapi.yaml": "database",
    "test_database.py": "database", "test_backend.py": "backend",
    "test_frontend.test.jsx": "frontend", "test_frontend.spec.cjs": "frontend",
}


def _stage_for(path: str) -> str:
    if path.startswith("backend/"):
        return "backend"
    if path.startswith("frontend/"):
        return "frontend"
    return _FILENAME_TO_STAGE.get(Path(path).name, "config")


def _build_file_entry(path: str, content: str) -> dict:
    kind = _classify(path)
    entry = {"hash": _hash(content), "stage": _stage_for(path), "kind": kind}

    if kind == "schema":
        from skills.db_validators import build_schema_model
        model = build_schema_model(content)
        if not model.parse_error:
            entry["tables"] = {
                name: {"columns": sorted(t.columns.keys()),
                      "foreign_keys": [{"columns": fk.local_columns, "references": fk.ref_table}
                                       for fk in t.foreign_keys]}
                for name, t in model.tables.items()
            }
    elif kind == "backend":
        entry.update(_extract_backend_file_entry(path, content))
    elif kind == "frontend":
        entry.update(_extract_frontend_file_entry(path, content))

    return entry


def _entities_from_files(files: dict) -> dict:
    """Best-effort cross-stage entity clustering - the SAME normalized-
    resource-name heuristic already proven across three different Contract
    Validators this session (db_validators.py, be_validators.py,
    fe_validators.py's singular/plural-tolerant matching), applied here to
    group "users table" + "User model" + "/users route" + "Users.jsx page"
    under one entity name instead of re-deriving that link three times."""
    from skills.db_validators import _normalize_resource_name, _NON_RESOURCE_PATH_SEGMENTS

    entities = {}

    def _bucket(name: str, stage: str, path: str):
        key = _normalize_resource_name(name).rstrip("s") or name
        entities.setdefault(key, {}).setdefault(stage, [])
        if path not in entities[key][stage]:
            entities[key][stage].append(path)

    for path, entry in files.items():
        for table_name in entry.get("tables", {}):
            _bucket(table_name, "database", path)
        for model in entry.get("models", []):
            _bucket(model["table"], "backend", path)
        for route, _method in entry.get("routes", []) if entry.get("kind") == "backend" else []:
            segment = route.strip("/").split("/")[0] if route.strip("/") else ""
            # Excludes the SAME auth/utility path segments (login, token,
            # export, ...) db_validators.py's ContractValidator already
            # excludes from its own "API without a table" check - a real
            # endpoint with no 1:1 entity behind it, not a false negative.
            if segment and "{" not in segment and segment not in _NON_RESOURCE_PATH_SEGMENTS:
                _bucket(segment, "backend", path)
        for route in entry.get("routes", []) if entry.get("kind") == "frontend" else []:
            segment = route.strip("/").split("/")[0] if route.strip("/") else ""
            if segment and "{param}" not in segment and segment not in _NON_RESOURCE_PATH_SEGMENTS:
                _bucket(segment, "frontend", path)
    # Test files are consolidated per-stage (one test_backend.py covers
    # every backend entity, etc. - see the Acceptance Test Generator's own
    # "one file per stage" design) rather than per-entity, so there's no
    # real content signal to cluster them more precisely than "attach to
    # every entity already known by the time tests exist" - crude, but an
    # honest reflection of how these test files are actually organized,
    # not a false precision this data doesn't support.
    test_paths = [path for path, entry in files.items() if entry.get("kind") == "test"]
    for key in entities:
        for path in test_paths:
            entities[key].setdefault("tests", [])
            if path not in entities[key]["tests"]:
                entities[key]["tests"].append(path)

    return entities


def _collect_all_files(project_dir, workspace: dict) -> dict:
    """Every file this index cares about, keyed the same way everywhere
    else in the pipeline (workspace-relative for backend/frontend,
    project-root-relative for schema/contract/tests) - shared by both
    build_project_index() and update_project_index() so they can never
    silently drift into collecting a different file set."""
    all_files = {}
    for artifact_type in ("backend", "frontend"):
        for path, content in workspace.get(artifact_type, {}).get("files", {}).items():
            all_files[f"{artifact_type}/{path}"] = content
    schema = workspace.get("database", {}).get("schema", "")
    if schema:
        all_files["schema.sql"] = schema
    openapi_spec = workspace.get("contract", {}).get("openapi_spec", "")
    if openapi_spec:
        all_files["openapi.yaml"] = openapi_spec
    for name in _TEST_FILENAMES:
        test_path = Path(project_dir) / name
        if test_path.exists():
            try:
                all_files[name] = test_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                pass
    return all_files


def build_project_index(project_dir, workspace: dict) -> dict:
    """
    Full (re)build - only used for a fresh project or when no index exists
    yet. Updates use update_project_index() instead (Phase 2) to avoid
    re-parsing every file every round. Writes .project_index.json and
    returns the index dict.
    """
    all_files = _collect_all_files(project_dir, workspace)
    files_index = {path: _build_file_entry(path, content) for path, content in all_files.items()}
    index = {
        "version": _INDEX_VERSION,
        "files": files_index,
        "entities": _entities_from_files(files_index),
    }
    _index_path(project_dir).write_text(json.dumps(index, indent=2), encoding="utf-8")
    return index


def update_project_index(project_dir, workspace: dict) -> dict:
    """
    Incremental update (Phase 2) - only re-extracts entries for files that
    are new or whose content genuinely changed since the last index (by
    content hash), mirroring skills/review_cache.py's split_changed_files/
    rebuild_cache SHAPE exactly - the same already-proven pattern for
    "only re-derive what changed," not a new one invented for this file.
    A file no longer present is dropped automatically (only files
    currently in the real file set are ever written back). Falls back to
    a full build_project_index() if no usable index exists yet - the
    normal case for a fresh project's first round.
    """
    existing = load_project_index(project_dir)
    if not existing:
        return build_project_index(project_dir, workspace)

    all_files = _collect_all_files(project_dir, workspace)
    old_files = existing.get("files", {})

    new_files_index = {}
    changed_count = 0
    for path, content in all_files.items():
        h = _hash(content)
        old_entry = old_files.get(path)
        if old_entry and old_entry.get("hash") == h:
            new_files_index[path] = old_entry
        else:
            new_files_index[path] = _build_file_entry(path, content)
            changed_count += 1

    index = {
        "version": _INDEX_VERSION,
        "files": new_files_index,
        "entities": _entities_from_files(new_files_index),
    }
    _index_path(project_dir).write_text(json.dumps(index, indent=2), encoding="utf-8")

    dropped = len(old_files) - len(set(old_files) & set(new_files_index))
    logger = get_logger()
    logger.info(f"Project Index updated: {changed_count} file(s) re-indexed, "
               f"{len(new_files_index) - changed_count} unchanged (reused)"
               + (f", {dropped} removed" if dropped else ""))
    return index


def load_project_index(project_dir) -> dict:
    """Returns {} if no index exists yet, or it's unreadable/wrong-version
    - callers must treat that as 'no index available', not an error (see
    module docstring - this is a cache, never a hard dependency)."""
    path = _index_path(project_dir)
    if not path.exists():
        return {}
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if index.get("version") != _INDEX_VERSION:
        return {}
    return index


# ============================================================================
# Search API (Phase 3) - queries against the already-loaded index dict,
# zero filesystem access. Every function degrades to an empty list if the
# index has nothing for that query - never an error, and never a reason to
# skip a real list_files()/search_files() fallback (see skills/
# incremental_codegen.py, which only ever TREATS these as a head start,
# not a hard restriction on what an agent may still look at).
# ============================================================================

def _norm(name: str) -> str:
    from skills.db_validators import _normalize_resource_name
    return _normalize_resource_name(name).rstrip("s")


def find_module(index: dict, name: str) -> dict:
    """Broadest search - every file (any stage) associated with an
    entity/module matching `name` (e.g. "reports", "Visitor", "user_id" -
    matched via the same normalization the entity clustering itself uses).
    Returns the entity dict ({stage: [paths]}) or {} if nothing matches."""
    entities = index.get("entities", {})
    key = _norm(name)
    if key in entities:
        return entities[key]
    for entity_key, entity in entities.items():
        if key in entity_key or entity_key in key:
            return entity
    return {}


def find_table(index: dict, name: str) -> list:
    """File paths where a table matching `name` is declared - schema.sql
    plus any backend file whose SQLAlchemy model targets that table."""
    key = _norm(name)
    matches = []
    for path, entry in index.get("files", {}).items():
        if key in {t.rstrip("s") for t in entry.get("tables", {})}:
            matches.append(path)
        if any(_norm(m["table"]) == key for m in entry.get("models", [])):
            matches.append(path)
    return sorted(set(matches))


def find_model(index: dict, name: str) -> list:
    """Backend file paths defining a model class matching `name` (by
    class name OR the table it targets)."""
    key = name.lower()
    norm_key = _norm(name)
    matches = []
    for path, entry in index.get("files", {}).items():
        for model in entry.get("models", []):
            if model["class"].lower() == key or _norm(model["table"]) == norm_key:
                matches.append(path)
    return sorted(set(matches))


def find_route(index: dict, resource_or_path: str) -> list:
    """Backend [{"file", "path", "method"}] entries whose first path
    segment matches `resource_or_path` (tolerant of a leading '/' and
    singular/plural, same as the entity clustering)."""
    key = _norm(resource_or_path)
    matches = []
    for path, entry in index.get("files", {}).items():
        if entry.get("kind") != "backend":
            continue
        for route, method in entry.get("routes", []):
            segment = route.strip("/").split("/")[0] if route.strip("/") else ""
            if _norm(segment) == key:
                matches.append({"file": path, "path": route, "method": method})
    return matches


def find_component(index: dict, name: str) -> list:
    """Frontend file paths defining a component matching `name` (exact,
    case-insensitive component-name match - deliberately not fuzzy the way
    find_module/find_table are, since component names are usually typed
    exactly by whoever's asking)."""
    key = name.lower()
    return sorted(path for path, entry in index.get("files", {}).items()
                 if entry.get("kind") == "frontend" and (entry.get("component") or "").lower() == key)


def summarize_project_structure(index: dict) -> str:
    """
    Deterministic (NO LLM call), always-current summary of what the project
    ACTUALLY has right now - every backend route and every frontend route/
    component the index knows about. Built specifically to replace/augment
    the stale "Architecture" text every --update generation call otherwise
    relies on: that text is frozen from the project's very first build and
    never reflects pages/routes/endpoints added by later updates. A real,
    observed consequence of that staleness: a generation round given only
    the stale architecture description proceeded to rewrite App.jsx's
    routing from scratch, silently dropping several real routes it had no
    way of knowing existed. This function costs nothing extra to compute -
    the index is already refreshed every round regardless of strategy.

    Returns "" if the index is empty/missing - callers should treat that as
    "nothing to add," not an error (the index is a cache, never the source
    of truth - see this module's own docstring).
    """
    files = index.get("files", {}) if index else {}
    if not files:
        return ""

    backend_routes = set()
    frontend_routes = set()
    frontend_components = set()
    for path, entry in files.items():
        if entry.get("kind") == "backend":
            for route, method in entry.get("routes", []):
                backend_routes.add(f"{method.upper()} {route}")
        elif entry.get("kind") == "frontend":
            for route in entry.get("routes", []):
                frontend_routes.add(f"{route}  (defined in {path})")
            if entry.get("component"):
                frontend_components.add(entry["component"])

    sections = []
    if backend_routes:
        sections.append("Current backend API routes (do not remove any of these unless the "
                        "change explicitly requires it):\n" +
                        "\n".join(f"  {r}" for r in sorted(backend_routes)))
    if frontend_routes:
        sections.append("Current frontend routes/pages (do not remove any of these unless the "
                        "change explicitly requires it):\n" +
                        "\n".join(f"  {r}" for r in sorted(frontend_routes)))
    if frontend_components:
        sections.append("Current frontend components: " + ", ".join(sorted(frontend_components)))
    return "\n\n".join(sections)
