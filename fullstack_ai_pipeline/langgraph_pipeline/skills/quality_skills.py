"""
Quality skills - Testing, code review, documentation
"""

from core.model_router import get_router
from core.logger import get_logger
from skills.text_utils import extract_code_block, repair_truncated_json
import json


def review_code_skill(files: dict[str, str], tasks: list[str]) -> list[dict]:
    """
    Review code and produce structured issues.
    Returns: list of Issue dicts with severity, file, description, suggested_fix
    """
    
    router = get_router()
    
    system_prompt = """You are a code reviewer. Review the provided code files and identify ONLY real, blocking bugs.

CRITICAL: This is for GENERATED code that will be deployed. Only flag issues that would cause:
1. Runtime crashes (undefined variables, syntax errors, import failures)
2. Security vulnerabilities (SQL injection, XSS, exposed secrets)
3. Complete feature absence (required endpoint/function entirely missing)

DO NOT flag subjective opinions like:
- "Could add more error handling" (unless it causes actual crashes)
- "API URL should use a config manager" (if env vars already work)
- "Could add more validation" (unless it breaks requirements)
- Style/formatting issues that don't affect functionality

Before flagging an issue, verify it's ACTUALLY broken:
- Claiming "API URL is hardcoded" when the code uses import.meta.env? NO
- Claiming "CRUD not implemented" when GET/POST/PATCH/DELETE exist? NO
- Claiming "CORS not set up" when CORSMiddleware is added? NO

Output a JSON array of issues, where each issue has:
{
  "severity": "high",
  "file": "path/to/file",
  "description": "SPECIFIC bug that will cause failure",
  "suggested_fix": "Specific fix guidance"
}

Use "high" severity ONLY. If the code works but could be "better," return an empty array [].
Output ONLY the JSON array, nothing else."""

    # Limit files sent to avoid token limits - capped per-file, not truncated
    # to a token-hostile 1000 chars. That cap used to cut a typical backend
    # main.py off mid-class, making the reviewer report real models/routes
    # as "missing" simply because it never saw them. The diff-cache in
    # review_code (skills/agent_tools.py) already keeps the total volume
    # down by only sending files that changed since the last pass, so there's
    # room for a per-file limit that covers a real single generated file.
    files_preview = {}
    for path, content in list(files.items())[:10]:  # First 10 files
        if len(content) > 6000:
            files_preview[path] = content[:6000] + f"\n... [truncated, {len(content) - 6000} more chars]"
        else:
            files_preview[path] = content
    
    context = f"Files:\n{json.dumps(files_preview, indent=2)}\n\nRequired Tasks:\n{tasks}"
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context}
    ]
    
    try:
        response_text = router.invoke("review_code", messages)
    except RuntimeError as e:
        # All providers/fallbacks exhausted (e.g. rate limit) - fail open
        # rather than crashing the whole pipeline and losing everything
        # already generated. Next quality pass gets another chance.
        get_logger().warning(f"Review unavailable: {str(e)[:150]}. Skipping review this pass.")
        return []

    response_text = extract_code_block(response_text, "json")

    try:
        issues = json.loads(response_text)
    except json.JSONDecodeError:
        try:
            issues = json.loads(repair_truncated_json(response_text))
        except json.JSONDecodeError as e:
            # Fail open rather than crashing the whole pipeline on a bad
            # generation - next quality pass (or bug_fix retry) gets another
            # chance to catch real issues.
            get_logger().warning(f"JSON parse error: {str(e)[:100]}. Skipping review this pass.")
            issues = []

    return issues


def generate_docs_skill(workspace: dict, requirements: str) -> dict:
    """
    Generate README and documentation.
    Returns: dict of doc files (path -> content)
    """
    
    router = get_router()
    
    system_prompt = """You are a technical writer. Generate documentation for the project.

Output a JSON object with documentation files:
{
  "README.md": "# Project Name\\n\\nDescription...\\n\\n## Installation\\n\\n## Usage\\n...",
  "API.md": "# API Documentation\\n\\n..."
}

Include:
- README with project overview, installation, usage
- API documentation if backend exists
- Setup instructions
- Dependencies

Keep README.md under 400 words and API.md under 250 words - concise beats
exhaustive here. This keeps the response short enough to finish generating
without being cut off mid-file. Output ONLY the JSON object, nothing else."""

    context = f"Requirements:\n{requirements}\n\nProject Workspace:\n{json.dumps(workspace, indent=2, default=str)}"
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context[:3000]}  # Limit context
    ]
    
    minimal_docs = {
        "README.md": f"# Project\n\n{requirements[:500]}\n\n## Setup\nSee generated files for implementation details.",
        "API.md": "# API Documentation\n\nSee OpenAPI specification for endpoints."
    }

    try:
        response_text = router.invoke("generate_docs", messages)
    except RuntimeError as e:
        get_logger().warning(f"Docs generation unavailable: {str(e)[:150]}. Returning minimal docs.")
        return minimal_docs

    response_text = extract_code_block(response_text, "json")

    try:
        docs = json.loads(response_text)
    except json.JSONDecodeError:
        try:
            docs = json.loads(repair_truncated_json(response_text))
        except json.JSONDecodeError as e:
            get_logger().warning(f"JSON parse error: {str(e)[:100]}. Returning minimal docs.")
            docs = minimal_docs

    return docs


def _pyflakes_errors(content: str, filename: str) -> list[str]:
    """
    Real static analysis via pyflakes - catches bugs ast.parse can't, like a
    typo'd/undefined name (`raise Exceptio(...)` is syntactically valid
    Python, so ast.parse accepts it; only something that actually resolves
    names, like pyflakes, catches it). Only "undefined name/local" findings
    are treated as failures here - things like unused imports are real but
    too noisy/benign for generated scaffolds to fail the build over.
    """
    import io
    from pyflakes.api import check
    from pyflakes.reporter import Reporter

    out, err = io.StringIO(), io.StringIO()
    try:
        check(content, filename, Reporter(out, err))
    except Exception:
        return []  # pyflakes itself choking on the input isn't this file's fault

    return [
        line for line in out.getvalue().splitlines()
        if "undefined name" in line or "undefined local" in line
    ]


def _check_frontend_api_wiring(content: str):
    """
    Deterministic check for the exact bug that broke frontend/backend
    connectivity previously: a hardcoded API base URL with no reference to
    the env var that actually carries the backend's real (dynamically-
    allocated per project) port. Only flags a file that both (a) contains a
    hardcoded http(s)://localhost|127.0.0.1:PORT literal and (b) never
    references import.meta.env/process.env anywhere - a hardcoded value
    used ONLY as a fallback alongside a real env var read is the correct,
    expected pattern and is not flagged.
    """
    import re

    match = re.search(r"""https?://(?:localhost|127\.0\.0\.1):\d+""", content)
    if not match:
        return None

    has_env_reference = "import.meta.env" in content or "process.env" in content
    if has_env_reference:
        return None

    return (f"Hardcoded API URL '{match.group(0)}' with no import.meta.env/process.env reference - "
            f"this won't match the backend's actual (dynamically-allocated) port.")


def _check_relative_imports(tree) -> list[str]:
    """
    Deterministic check for relative imports ("from .x import y" / "from ..x
    import y") in backend/ files. This pipeline's generated backend has no
    __init__.py package structure - it's a flat folder copied into the Docker
    image and run as "uvicorn main:app" from inside it - so ANY relative
    import fails at import time with "attempted relative import with no
    known parent package", crashing the container before it ever binds a
    port. Confirmed against a real deployment failure, not theoretical:
    ast.parse/pyflakes both accept relative imports as syntactically valid,
    so neither existing check catches this on its own.
    """
    import ast

    errors = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level and node.level > 0:
            names = ", ".join(alias.name for alias in node.names)
            errors.append(
                f"relative import 'from {'.' * node.level}{node.module or ''} import {names}' won't "
                f"resolve - backend/ has no package structure, use an absolute import instead"
            )
    return errors


def _module_key_for(path: str) -> str:
    """'routers/users.py' -> 'routers.users', 'schemas.py' -> 'schemas' -
    matches how this pipeline's flat-per-folder backend imports its own
    local modules (see _check_relative_imports's docstring: no package
    __init__.py machinery beyond one level, imports are always absolute)."""
    return path[:-3].replace("/", ".").replace("\\", ".") if path.endswith(".py") else path


def _top_level_names(tree) -> set[str]:
    """
    Every name a module actually defines at module level: classes,
    functions, and plain assignments (`router = ...`, `SECRET_KEY = ...`) -
    the exact set of things a `from this_module import X` could legally
    reach for.

    Recurses into try/except/else/finally and if/else bodies - an
    assignment inside a top-level `try: engine = create_engine(...) except
    ...:` is still a real module attribute once the module finishes
    executing, exactly as importable as one written with no try/except at
    all. Confirmed as a live false positive without this: database.py
    wrapping its engine setup in a try/except (a real, common pattern for
    "fail loudly with a clearer message") made `engine` invisible to this
    function, flagging a completely correct `from database import engine`
    as broken. Does NOT descend into function/class bodies - those are
    properly scoped, not module-level, regardless of nesting.
    """
    import ast

    names = set()

    def visit(stmts):
        for node in stmts:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                # a bare re-export ("from x import Y" then something else
                # does "from this_module import Y") - count imported names
                # as available too, matching real Python semantics.
                for alias in node.names:
                    names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Try):
                visit(node.body)
                for handler in node.handlers:
                    visit(handler.body)
                visit(node.orelse)
                visit(node.finalbody)
            elif isinstance(node, ast.If):
                visit(node.body)
                visit(node.orelse)
            elif isinstance(node, ast.With):
                visit(node.body)

    visit(tree.body)
    return names


def _check_backend_cross_file_imports(backend_files: dict) -> list[str]:
    """
    Deterministic, project-wide check: does every 'from local_module import
    X' actually find X defined in local_module? This is the single check
    every earlier per-file tool (ast.parse, pyflakes) structurally cannot
    do - pyflakes only resolves names WITHIN one file's own scope, so a
    missing/shadowed symbol in a DIFFERENT file is invisible to it. This is
    exactly the bug class that repeatedly reached Docker instead of being
    caught here: schemas.py missing a Pydantic class a router imported,
    'from models import User' shadowed by a later 'from schemas import
    User', auth.py missing a function routers/users.py called - every one
    of these is a plain "name not found in target module" that this check
    catches for free, no LLM or third-party tooling needed.

    Also flags the specific pattern that caused a real deployment failure:
    a local module exporting its aggregate name as a bare list/tuple
    literal (e.g. routers/__init__.py's `router = [a, b, c]`) - FastAPI's
    app.include_router() requires a single APIRouter instance, and a list
    only surfaces as a crash at actual runtime import time, never at
    per-file syntax checking.
    """
    import ast

    py_files = {p: c for p, c in backend_files.items() if p.endswith(".py")}
    trees = {}
    for path, content in py_files.items():
        try:
            trees[path] = ast.parse(content, filename=path)
        except SyntaxError:
            continue  # already reported separately by the per-file syntax check

    module_names = {_module_key_for(p): p for p in trees}
    exported = {path: _top_level_names(tree) for path, tree in trees.items()}

    errors = []
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
                continue  # relative imports handled separately; only care about local modules below
            target_path = module_names.get(node.module)
            if target_path is None:
                continue  # not one of our own files (fastapi/sqlalchemy/etc.) - nothing to check
            target_names = exported[target_path]
            for alias in node.names:
                if alias.name != "*" and alias.name not in target_names:
                    errors.append(
                        f"{path}: imports '{alias.name}' from '{node.module}' but no such name is "
                        f"defined in {target_path}"
                    )

        # The list-vs-APIRouter pattern: a module-level assignment whose
        # value is a bare List/Tuple display, in a file that looks like a
        # router aggregator (__init__.py under any folder - the only place
        # this pipeline ever combines sub-routers).
        if path.endswith("__init__.py"):
            for node in tree.body:
                if isinstance(node, ast.Assign) and isinstance(node.value, (ast.List, ast.Tuple)):
                    for target in node.targets:
                        # __all__ (and other dunder module attributes) are
                        # never "the router" - __all__ = [...] is the
                        # standard Python export-list convention and is
                        # ALWAYS a list of strings, never router objects.
                        # Confirmed false positive without this exclusion:
                        # a correct routers/__init__.py combining sub-routers
                        # into one real `router = APIRouter()` still trips
                        # this check purely because it also declares
                        # `__all__ = ["router"]`, sending the pipeline into
                        # an unfixable retry loop over code that was never
                        # broken.
                        if (isinstance(target, ast.Name) and
                                not (target.id.startswith("__") and target.id.endswith("__"))):
                            errors.append(
                                f"{path}: '{target.id}' is assigned a plain list/tuple of routers - "
                                f"FastAPI's app.include_router() requires a single APIRouter instance, "
                                f"not a list. Build one with APIRouter() and call .include_router() on "
                                f"it once per sub-router instead."
                            )
    return errors


def _check_duplicate_routes(backend_files: dict) -> list[str]:
    """
    Deterministic check: does the same (HTTP method, full path) get
    registered more than once across all router files? A real, not just
    theoretical, footgun once multiple routers are combined - the second
    registration silently shadows the first at runtime, no error, just
    quietly wrong behavior FastAPI never warns about.

    Resolves each file's own "router = APIRouter(prefix=...)" the same way
    extract_backend_routes does, and for the same reason: without it, EVERY
    router in a multi-router project decorated with a bare "/" (idiomatic
    FastAPI - each file declares its prefix once, routes below are relative
    to it) looks identical across files even though they're genuinely
    different real paths once the prefix is applied - confirmed as a live
    false positive the first time this check ran for real.
    """
    import re

    router_prefix_pattern = re.compile(r"""(\w+)\s*=\s*APIRouter\([^)]*prefix\s*=\s*[`'"]([^`'"]*)[`'"]""")
    decorator_pattern = re.compile(r"""@(\w+)\.(get|post|put|patch|delete)\(\s*[`'"]([^`'"]*)[`'"]""")

    seen = {}
    errors = []
    for path, content in backend_files.items():
        if not path.endswith(".py"):
            continue
        prefixes = dict(router_prefix_pattern.findall(content))
        for var_name, method, route_path in decorator_pattern.findall(content):
            full_path = re.sub(r"/+", "/", prefixes.get(var_name, "") + route_path) or "/"
            key = (method.upper(), full_path)
            if key in seen and seen[key] != path:
                errors.append(f"{method.upper()} {full_path} is registered in both "
                             f"{seen[key]} and {path} - the second silently shadows the first")
            else:
                seen[key] = path
    return errors


def _resolve_frontend_import_path(current_path: str, import_path: str, frontend_files: dict) -> str | None:
    """
    Resolves a relative JS import ('./Foo', '../pages/Foo') against the
    importing file's own directory to an actual key in frontend_files,
    trying the common extensions/index-file fallbacks a bundler would.
    Returns None if nothing matches - a genuinely missing file/module.

    Extension order matters and must match Vite's actual default
    resolve.extensions (['.mjs', '.js', '.mts', '.ts', '.jsx', '.tsx',
    '.json']) - .js/.ts before .jsx/.tsx. Confirmed as a live false
    positive with the order reversed: a project had both api/client.js
    (the real, complete file) and a stale, near-empty api/client.jsx side
    by side: checking .jsx first resolved every import to the WRONG file
    and flagged real exports as missing, when Vite itself would have
    resolved to client.js and worked fine.
    """
    import posixpath

    current_dir = posixpath.dirname(current_path)
    base = posixpath.normpath(posixpath.join(current_dir, import_path))
    candidates = [base] if posixpath.splitext(base)[1] else [
        base, f"{base}.js", f"{base}.ts", f"{base}.jsx", f"{base}.tsx", f"{base}.css",
        f"{base}/index.js", f"{base}/index.jsx",
    ]
    for candidate in candidates:
        if candidate in frontend_files:
            return candidate
    return None


def _frontend_module_exports(content: str) -> set[str]:
    """Regex-based export scan (no JS parser dependency available, same
    constraint as _check_bracket_balance) - covers the export forms this
    pipeline's own generated code actually uses: named const/function/class
    exports and a default export."""
    import re

    names = set(re.findall(r'export\s+(?:const|function|class)\s+(\w+)', content))
    names.update(re.findall(r'export\s*\{\s*([^}]+)\s*\}', content))
    if re.search(r'export\s+default\b', content):
        names.add("default")
    # "export { a, b as c }" - normalize the comma list from the second
    # regex above into individual names.
    flattened = set()
    for n in names:
        if "," in n:
            flattened.update(part.strip().split(" as ")[0].strip() for part in n.split(","))
        else:
            flattened.add(n.split(" as ")[0].strip())
    return flattened


def _check_frontend_cross_file_imports(frontend_files: dict) -> list[str]:
    """
    Deterministic, project-wide check mirroring
    _check_backend_cross_file_imports for the frontend: does every relative
    import ('./X', '../pages/X') actually resolve to a file that exists,
    and for named imports, does that file actually export the requested
    name? This is exactly the bug class that reached a blank white page in
    production instead of being caught here: api.js missing
    'sendPasswordReset'/'approveVisitor'/'getSettings' that a page imported,
    a CSS import pointing at a file that was never generated, a plain
    default import ('import ProtectedRoute from "./components/
    ProtectedRoute"') of a component that was never created - none of
    these are syntax errors, so the bracket-balance check can't see them,
    and each file parses individually just fine.

    Deliberately two-step (extract the import clause with one simple regex,
    then parse ITS text in plain Python) rather than one combined regex for
    every import shape - confirmed necessary by a live false negative: a
    single regex covering "Default", "{ named }", "Default, { named }", and
    "* as ns" via nested optional groups silently failed to match a bare
    "import Default from './X'" with no trailing comma at all (the default-
    name group's own comma requirement meant it could only ever fire
    alongside a named import), so the ENTIRE import was invisible to this
    check - not one specific piece of it wrong, the whole line unmatched.
    """
    import re

    clause_pattern = re.compile(r'import\s+(.+?)\s+from\s+["\'](\.[^"\']*)["\']')
    bare_pattern = re.compile(r'import\s+["\'](\.[^"\']*)["\']')

    errors = []
    for path, content in frontend_files.items():
        if not path.endswith((".js", ".jsx", ".ts", ".tsx")):
            continue

        import_specs = []  # (named_str_or_None, mod_path)
        for clause, mod_path in clause_pattern.findall(content):
            clause = clause.strip()
            if clause.startswith("*"):
                import_specs.append((None, mod_path))  # namespace import - nothing to verify by name
                continue
            brace_match = re.search(r'\{([^}]*)\}', clause)
            import_specs.append((brace_match.group(1) if brace_match else None, mod_path))
        for mod_path in bare_pattern.findall(content):
            import_specs.append((None, mod_path))  # side-effect only (e.g. a CSS import)

        for named, mod_path in import_specs:
            target = _resolve_frontend_import_path(path, mod_path, frontend_files)
            if target is None:
                errors.append(f"{path}: imports '{mod_path}' but no such file exists")
                continue
            if named and target.endswith((".js", ".jsx", ".ts", ".tsx")):
                exported = _frontend_module_exports(frontend_files[target])
                for name in named.split(","):
                    name = name.strip().split(" as ")[0].strip()
                    if name and name not in exported:
                        errors.append(f"{path}: imports '{name}' from '{mod_path}' but no such "
                                     f"export exists in {target}")
    return errors


def _check_frontend_package_dependencies(frontend_files: dict) -> list[str]:
    """
    Deterministic check: does every bare (non-relative) import - a real npm
    package, not a local file - actually appear in package.json's
    dependencies/devDependencies? _check_frontend_cross_file_imports only
    ever looks at relative ('./X', '../pages/X') imports; a bare import like
    '@mui/material' is a completely different failure mode (a real,
    confirmed live bug: ProtectedRoute.jsx imported CircularProgress/Box
    from '@mui/material', which was never added to package.json, so `npm
    install` never installed it and Vite's import analysis fails at dev
    server startup - the exact same class of "reaches Docker instead of
    being caught here" bug this whole check family exists to close).
    """
    import re, json

    package_json_raw = frontend_files.get("package.json", "")
    try:
        package_json = json.loads(package_json_raw) if package_json_raw else {}
    except json.JSONDecodeError:
        return []  # malformed package.json is a separate, already-caught problem
    declared = set(package_json.get("dependencies", {})) | set(package_json.get("devDependencies", {}))

    bare_import_pattern = re.compile(r"""from\s+["']([^./][^"']*)["']""")
    errors = []
    seen_missing = set()
    for path, content in frontend_files.items():
        if not path.endswith((".js", ".jsx", ".ts", ".tsx")):
            continue
        for spec in bare_import_pattern.findall(content):
            # "@scope/pkg/sub/path" -> "@scope/pkg"; "pkg/sub/path" -> "pkg" -
            # the installable package name is never the full import path
            # once a package exposes named sub-paths (react-icons/fi,
            # react-dom/client both being real, correctly-declared examples
            # in this very project).
            parts = spec.split("/")
            package_name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
            if package_name not in declared and package_name not in seen_missing:
                errors.append(f"'{package_name}' is imported (e.g. in {path}) but not listed in "
                             f"package.json's dependencies - npm install will never install it")
                seen_missing.add(package_name)
    return errors


def _check_duplicate_router_mount(frontend_files: dict) -> list[str]:
    """
    Deterministic check: is <BrowserRouter>/<Router> actually rendered
    (as JSX, not just imported) in more than one file? React Router v6
    throws a hard runtime error the instant this happens ("You cannot
    render a <Router> inside another <Router>") - a completely blank page
    with no import-analysis error at all, since every file involved is
    individually valid; this only shows up once React actually tries to
    mount the tree. Confirmed as a real, live bug: main.jsx correctly wraps
    <App/> in a single <BrowserRouter>, but App.jsx ALSO independently
    wrapped its own <Routes> in a second <Router>, nesting two router
    instances.

    Generic on purpose - not specific to react-router or to this project:
    any project this pipeline generates that uses react-router (which its
    own stack mandate makes essentially every multi-page frontend) can hit
    this exact double-mount mistake regardless of what the routes/pages
    actually are.
    """
    import re

    mount_pattern = re.compile(r"<(BrowserRouter|Router)\b")
    mounts = []
    for path, content in frontend_files.items():
        if not path.endswith((".js", ".jsx", ".ts", ".tsx")):
            continue
        for _ in mount_pattern.findall(content):
            mounts.append(path)

    if len(mounts) > 1:
        return [f"<Router>/<BrowserRouter> is rendered in more than one file ({', '.join(sorted(set(mounts)))}) "
               f"- nesting two router instances is a hard runtime error in react-router v6 "
               f"('You cannot render a <Router> inside another <Router>'), and blanks the whole page "
               f"with no import-time error at all. Only ONE file (normally main.jsx/index.jsx, the app's "
               f"entry point) should ever mount a Router - every other file should just render "
               f"<Routes>/<Route> without its own Router wrapper."]
    return []


def _check_design_system_present(frontend_files: dict, is_static: bool) -> list[str]:
    """
    Deterministic check: does the pipeline-authored design-system.css exist
    on disk, and is it actually wired into the app (imported from
    src/main.jsx, or linked from index.html for a static project)? This is
    the check that stops a revision round from silently dropping the design
    system - the CSS file itself is rewritten every FE_RUN unconditionally
    (see FrontendCapability.run()), but nothing previously verified the
    Frontend Agent kept the import/link that makes it actually apply.
    """
    css_path = "styles/design-system.css" if is_static else "src/styles/design-system.css"
    if css_path not in frontend_files:
        return [f"{css_path} is missing - the pipeline's design system must exist on disk for "
                f"consistent styling across every generated page"]

    if is_static:
        index_html = frontend_files.get("index.html", "")
        if "design-system.css" not in index_html:
            return ["index.html does not <link> styles/design-system.css - the design system exists "
                    "on disk but isn't applied to the page"]
    else:
        entry = frontend_files.get("src/main.jsx", "") or frontend_files.get("src/main.tsx", "")
        if "design-system.css" not in entry:
            return ["src/main.jsx does not import './styles/design-system.css' - the design system "
                    "exists on disk but isn't applied to the app"]
    return []


def _check_layout_components_rendered(frontend_files: dict) -> list[str]:
    """
    Deterministic, project-agnostic check: any component whose name looks
    like a layout/navigation component (Sidebar/TopBar/Header/Nav/Navbar/
    Navigation) must actually be rendered as a JSX tag somewhere, not just
    defined and exported. Generalizes a real, confirmed bug (a Sidebar
    component that existed and was even imported, but was never placed in
    the JSX tree, so the app rendered with no navigation at all) into a
    permanent check that applies to any project regardless of what its
    pages/routes actually are.
    """
    import re

    # Name must start with an uppercase letter (React component naming
    # convention) - without this, a plain handler function like
    # "toggleSidebar" or "handleNavClick" matches the keyword substring and
    # produces a false positive, since it's never meant to be a JSX tag.
    definition_pattern = re.compile(
        r"\b(?:function|const|class)\s+([A-Z]\w*(?:Sidebar|TopBar|Navbar|Navigation|AppHeader|AppNav)\w*)\b"
    )
    defined = set()
    for path, content in frontend_files.items():
        if not path.endswith((".js", ".jsx", ".ts", ".tsx")):
            continue
        for name in definition_pattern.findall(content):
            defined.add(name)

    if not defined:
        return []

    all_content = "\n".join(
        content for path, content in frontend_files.items()
        if path.endswith((".js", ".jsx", ".ts", ".tsx"))
    )

    unrendered = []
    for name in sorted(defined):
        usage_pattern = re.compile(r"<" + re.escape(name) + r"\b")
        if not usage_pattern.search(all_content):
            unrendered.append(name)

    if unrendered:
        return [f"component(s) {', '.join(unrendered)} are defined but never rendered as a JSX tag "
                f"anywhere - a navigation/layout component that's only defined/exported and never "
                f"placed in the tree produces an app with no visible sidebar/nav at all"]
    return []


# ============================================================================
# VALIDATION-AGAINST-CONTRACT (phase 2 of the per-agent self-heal loop) -
# distinct from the UT checks above: those catch "is this code syntactically
# broken", these catch "does this code actually match the plan/contract it
# was supposed to implement" - e.g. Frontend calling a URL Backend never
# implemented, which is a real bug no syntax checker can ever catch.
# ============================================================================

def _extract_path_literal(raw: str) -> str:
    """
    Normalize a fetch()/axios() call's literal argument into a comparable
    path: strips everything before the first '/' (drops a leading
    ${API_BASE_URL} or similar), and collapses any ${...}/{...} template
    segment into a generic {param} placeholder so "/api/notes/${id}"
    compares equal to a backend route's "/api/notes/{note_id}".
    """
    import re
    idx = raw.find("/")
    if idx == -1:
        return ""
    path = re.sub(r"\$\{[^}]*\}", "{param}", raw[idx:])
    path = re.sub(r"\{[^}]*\}", "{param}", path)
    return path.rstrip("/")


def extract_frontend_api_paths(files: dict) -> set[str]:
    """Best-effort extraction of API paths a frontend actually calls (fetch()/axios.*() literals)."""
    import re
    pattern = re.compile(r"""(?:fetch|axios\.(?:get|post|put|delete|patch))\(\s*[`'"]([^`'"]*)[`'"]""")
    paths = set()
    for path, content in files.items():
        if not path.endswith((".js", ".jsx", ".ts", ".tsx")):
            continue
        for m in pattern.finditer(content):
            normalized = _extract_path_literal(m.group(1))
            if normalized:
                paths.add(normalized)
    return paths


def extract_backend_routes(files: dict) -> set[str]:
    """
    Best-effort extraction of routes a FastAPI backend actually implements
    (@app./@router. decorators), accounting for "router = APIRouter(prefix=
    ...)" - a real, idiomatic FastAPI pattern where a route file declares
    the prefix ONCE and every route below it uses a path relative to that
    (e.g. router = APIRouter(prefix="/api/notes"); @router.get("/{id}") ->
    the real path is "/api/notes/{id}", not "/{id}" alone). Missing this
    produced a real, confirmed false positive: a correctly-implemented
    "/api/notes" endpoint got flagged as "not implemented" because the
    decorator's own literal argument was just "" or "/{id}".

    Does NOT resolve a prefix supplied later via
    app.include_router(router, prefix=...) in a DIFFERENT file - only the
    same-file "APIRouter(prefix=...)" declaration is handled.
    """
    import re
    router_prefix_pattern = re.compile(r"""(\w+)\s*=\s*APIRouter\([^)]*prefix\s*=\s*[`'"]([^`'"]*)[`'"]""")
    decorator_pattern = re.compile(r"""@(\w+)\.(?:get|post|put|delete|patch)\(\s*[`'"]([^`'"]*)[`'"]""")
    paths = set()
    for path, content in files.items():
        if not path.endswith(".py"):
            continue
        prefixes = dict(router_prefix_pattern.findall(content))
        for var_name, route_path in decorator_pattern.findall(content):
            full_path = re.sub(r"/+", "/", prefixes.get(var_name, "") + route_path)
            normalized = re.sub(r"\{[^}]*\}", "{param}", full_path).rstrip("/") or "/"
            paths.add(normalized)
    return paths


def extract_openapi_paths(openapi_spec: str) -> set[str]:
    """Best-effort extraction of the top-level path keys declared under openapi.yaml's `paths:` section."""
    import re
    paths = set()
    in_paths_section = False
    for line in openapi_spec.splitlines():
        stripped = line.strip()
        if not in_paths_section:
            if stripped == "paths:":
                in_paths_section = True
            continue
        if line and not line[0].isspace():
            break  # dedented back to column 0 - left the paths: section
        m = re.match(r"^\s{1,4}(/[^\s:]*):\s*$", line)
        if m:
            normalized = re.sub(r"\{[^}]*\}", "{param}", m.group(1)).rstrip("/")
            paths.add(normalized)
    return paths


def check_backend_matches_contract(openapi_spec: str, backend_files: dict) -> list[str]:
    """
    VAL step for Backend: does it actually implement every path openapi.yaml
    declares? Reports paths present in the contract with no matching route -
    "built something that doesn't match the plan," not just "no syntax errors."
    """
    if not openapi_spec:
        return []
    missing = sorted(extract_openapi_paths(openapi_spec) - extract_backend_routes(backend_files))
    return [f"openapi.yaml declares '{p}' but no backend route implements it" for p in missing]


def check_frontend_matches_backend(openapi_spec: str, backend_files: dict, frontend_files: dict) -> list[str]:
    """
    VAL step for Frontend: does every API call it actually makes correspond
    to a real backend route (or at least a contract path)? This is the exact
    class of bug ("frontend calls a URL the backend never implemented") that
    no static syntax check can ever catch.
    """
    frontend_paths = extract_frontend_api_paths(frontend_files)
    if not frontend_paths:
        return []
    known_paths = extract_backend_routes(backend_files) | extract_openapi_paths(openapi_spec or "")
    if not known_paths:
        return []  # nothing generated yet to validate against
    unmatched = sorted(p for p in frontend_paths if p not in known_paths)
    return [f"frontend calls '{p}' but no backend route/contract path matches it" for p in unmatched]


def run_tests_skill(workspace: dict) -> dict:
    """
    Real static verification, not a mock: parses every backend .py file with
    Python's own ast module (catches genuine syntax errors deterministically,
    for free) and additionally runs pyflakes to catch undefined-name-class
    bugs that are syntactically valid but wrong (e.g. "raise Exceptio(...)").
    Does a lightweight bracket-balance sanity check on frontend JS/TS files
    (no JS parser dependency is available, so this only catches gross
    mismatches, not full syntax validation). This replaces the previous mock
    that counted files and always reported 100% passed regardless of content.

    Returns: {"total", "passed", "failed", "skipped", "failures": [{"file", "error"}]}
    """
    import ast

    test_results = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "failures": []}

    backend_files = workspace.get("backend", {}).get("files", {})
    for path, content in backend_files.items():
        if not path.endswith(".py"):
            test_results["skipped"] += 1
            continue
        test_results["total"] += 1
        try:
            tree = ast.parse(content, filename=path)
        except SyntaxError as e:
            test_results["failed"] += 1
            test_results["failures"].append({
                "file": f"backend/{path}",
                "error": f"SyntaxError: {e.msg} (line {e.lineno})"
            })
            continue

        file_errors = _pyflakes_errors(content, path) + _check_relative_imports(tree)
        if file_errors:
            test_results["failed"] += 1
            test_results["failures"].append({
                "file": f"backend/{path}",
                "error": "; ".join(file_errors)
            })
        else:
            test_results["passed"] += 1

    # Project-level checks (need the FULL file set, not one file at a time) -
    # see _check_backend_cross_file_imports/_check_duplicate_routes'
    # docstrings for exactly what per-file ast.parse/pyflakes structurally
    # cannot catch that these do.
    if backend_files:
        cross_file_errors = _check_backend_cross_file_imports(backend_files)
        cross_file_errors += _check_duplicate_routes(backend_files)
        if cross_file_errors:
            test_results["total"] += 1
            test_results["failed"] += 1
            test_results["failures"].append({
                "file": "backend/ (cross-file)",
                "error": "; ".join(cross_file_errors)
            })

    frontend_files = workspace.get("frontend", {}).get("files", {})

    # Real, observed failure mode: a React+Vite frontend with no
    # vite.config.js (or one that doesn't actually wire up
    # @vitejs/plugin-react) falls back to esbuild's default JSX handling,
    # which uses the CLASSIC runtime - every JSX element compiles to a bare
    # "React.createElement(...)" call, and that runtime requires "React" to
    # be imported and in scope in every file that uses JSX. Plain
    # "import { useState } from 'react'"-style named imports don't provide
    # that, so the app crashes immediately on load ("React is not defined"),
    # blanking the entire page - confirmed live, this is exactly what
    # happened. Static-checking each file in isolation can never catch this
    # (every individual file's syntax is fine) - it's a missing/misconfigured
    # PROJECT-level file, so it's checked once here, not per-file.
    has_jsx = any(p.endswith((".jsx", ".tsx")) for p in frontend_files)
    uses_vite = "vite" in frontend_files.get("package.json", "")
    if has_jsx and uses_vite:
        test_results["total"] += 1
        vite_config = frontend_files.get("vite.config.js") or frontend_files.get("vite.config.ts")
        if vite_config is None:
            test_results["failed"] += 1
            test_results["failures"].append({
                "file": "frontend/vite.config.js",
                "error": "missing entirely - a React+Vite project with JSX files needs vite.config.js "
                        "configuring @vitejs/plugin-react, or JSX falls back to the classic runtime and "
                        "crashes with 'React is not defined' (no file imports the React default export)"
            })
        elif "@vitejs/plugin-react" not in vite_config or "plugins" not in vite_config:
            test_results["failed"] += 1
            test_results["failures"].append({
                "file": "frontend/vite.config.js",
                "error": "exists but doesn't wire up @vitejs/plugin-react in a plugins: [...] array - "
                        "without it JSX falls back to the classic runtime and crashes with "
                        "'React is not defined'"
            })
        else:
            test_results["passed"] += 1

    # Project-level frontend check (needs the FULL file set) - see
    # _check_frontend_cross_file_imports' docstring for why this catches a
    # whole class of blank-white-page bugs no per-file check can see.
    if frontend_files:
        is_static_frontend = "package.json" not in frontend_files
        cross_file_errors = _check_frontend_cross_file_imports(frontend_files)
        cross_file_errors += _check_frontend_package_dependencies(frontend_files)
        cross_file_errors += _check_duplicate_router_mount(frontend_files)
        cross_file_errors += _check_design_system_present(frontend_files, is_static_frontend)
        cross_file_errors += _check_layout_components_rendered(frontend_files)
        if cross_file_errors:
            test_results["total"] += 1
            test_results["failed"] += 1
            test_results["failures"].append({
                "file": "frontend/ (cross-file)",
                "error": "; ".join(cross_file_errors)
            })

    for path, content in frontend_files.items():
        if not path.endswith((".ts", ".tsx", ".js", ".jsx")):
            test_results["skipped"] += 1
            continue
        test_results["total"] += 1

        errors = []
        balance_error = _check_bracket_balance(content)
        if balance_error:
            errors.append(balance_error)
        wiring_error = _check_frontend_api_wiring(content)
        if wiring_error:
            errors.append(wiring_error)

        if errors:
            test_results["failed"] += 1
            test_results["failures"].append({
                "file": f"frontend/{path}",
                "error": "; ".join(errors)
            })
        else:
            test_results["passed"] += 1

    return test_results


# Characters after which a "/" is unambiguously the START of a regex
# literal, not a division operator - the standard lexer disambiguation
# rule (division only ever follows a value: an identifier, number, ')',
# ']', or a closing string/template). Confirmed false-positive source
# without this: a regex like /filename="?([^"]+)"?/i has a literal '"'
# and '[...]'/'(...)' inside it that _check_bracket_balance was reading as
# real string/bracket tokens (no regex awareness at all before this),
# desyncing the bracket stack on perfectly valid code no amount of
# regenerating the file could ever fix.
_REGEX_PRECEDING_CHARS = set("([{,;:=!&|?+-~*%^<>\n")
_REGEX_PRECEDING_KEYWORDS = {
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "throw", "case", "do", "else", "yield", "await",
}


def _looks_like_regex_start(content: str, i: int) -> bool:
    """True if the '/' at position i begins a regex literal rather than a
    division operator, based on what precedes it (skipping whitespace)."""
    j = i - 1
    while j >= 0 and content[j] in " \t":
        j -= 1
    if j < 0:
        return True  # start of file - can't be division
    ch = content[j]
    if ch in _REGEX_PRECEDING_CHARS:
        return True
    if ch.isalnum() or ch in "_$":
        k = j
        while k >= 0 and (content[k].isalnum() or content[k] in "_$"):
            k -= 1
        word = content[k + 1:j + 1]
        return word in _REGEX_PRECEDING_KEYWORDS
    return False


def _skip_regex_literal(content: str, i: int) -> int:
    """
    Given content[i] == '/' at a confirmed regex-literal start, returns the
    index just past the literal's closing '/' and any trailing flag
    letters (g, i, m, s, u, y, d) - so the caller can skip straight there
    without ever inspecting the regex's own internal brackets/quotes as if
    they were real code.
    """
    n = len(content)
    j = i + 1
    in_char_class = False
    while j < n:
        c = content[j]
        if c == "\\":
            j += 2
            continue
        if c == "[":
            in_char_class = True
        elif c == "]":
            in_char_class = False
        elif c == "/" and not in_char_class:
            j += 1
            break
        elif c == "\n":
            # A real regex literal never spans a line - this wasn't one
            # after all (division, or malformed) - bail without skipping.
            return i + 1
        j += 1
    while j < n and content[j].isalpha():
        j += 1
    return j


def _check_bracket_balance(content: str):
    """
    Lightweight brace/bracket/paren balance check for JS/TS/JSX/TSX. Not a
    real parser (no JS parser dependency available) - only catches gross
    structural mismatches (unclosed/extra braces), not genuine syntax
    errors. Tracks string, comment, AND regex-literal state on a
    best-effort basis so brackets/quotes inside any of them aren't counted.
    """
    pairs = {")": "(", "]": "[", "}": "{"}
    opens = set(pairs.values())
    stack = []
    in_string = None
    in_line_comment = False
    in_block_comment = False

    i = 0
    n = len(content)
    while i < n:
        ch = content[i]
        nxt = content[i + 1] if i + 1 < n else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
        elif in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 1
        elif in_string:
            if ch == "\\":
                i += 1
            elif ch == in_string:
                in_string = None
        else:
            if ch == "/" and nxt == "/":
                in_line_comment = True
                i += 1
            elif ch == "/" and nxt == "*":
                in_block_comment = True
                i += 1
            elif ch == "/" and nxt not in ("/", "*") and _looks_like_regex_start(content, i):
                i = _skip_regex_literal(content, i)
                continue
            elif ch == "'" and i > 0 and content[i - 1].isalnum() and nxt.isalpha():
                # An English contraction in plain JSX text (don't, it's,
                # user's, can't) - not a real string delimiter. Confirmed
                # false-positive source: this single quote was being read as
                # a string open, silently swallowing every real bracket
                # after it (including genuine unclosed ones the model never
                # wrote) until the next stray "'" anywhere later in the
                # file, producing a false "Unclosed '('" that no amount of
                # regenerating the file could ever fix. A genuine
                # string-opening quote is never directly adjacent to a
                # preceding alphanumeric with no operator/punctuation/
                # whitespace between (that would itself be invalid JS), so
                # this heuristic only skips real contractions.
                pass
            elif ch in ("'", '"', "`"):
                in_string = ch
            elif ch in opens:
                stack.append(ch)
            elif ch in pairs:
                if not stack or stack[-1] != pairs[ch]:
                    return f"Unbalanced '{ch}' at position {i}"
                stack.pop()
        i += 1

    if stack:
        return f"Unclosed '{stack[-1]}'"
    return None
