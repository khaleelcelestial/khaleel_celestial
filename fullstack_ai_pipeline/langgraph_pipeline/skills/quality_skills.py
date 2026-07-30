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


def check_schema_matches_contract(schema: str, openapi_spec: str) -> list[str]:
    """
    VAL step for Database: does every table in schema.sql show up as a
    resource somewhere in openapi.yaml? Coarse (substring, singular/plural
    tolerant) rather than exact - there's no reliable automated way to map a
    table name to a REST resource name perfectly, but this is enough to
    catch a schema that's totally disconnected from the API surface.
    """
    if not schema or not openapi_spec:
        return []
    import re
    tables = re.findall(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(\w+)", schema, re.IGNORECASE)
    spec_lower = openapi_spec.lower()
    return [
        f"table '{table}' has no matching resource/path mentioned in openapi.yaml"
        for table in tables if table.lower().rstrip("s") not in spec_lower
    ]


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


def _check_bracket_balance(content: str):
    """
    Lightweight brace/bracket/paren balance check for JS/TS/JSX/TSX. Not a
    real parser (no JS parser dependency available) - only catches gross
    structural mismatches (unclosed/extra braces), not genuine syntax
    errors. Tracks string and comment state on a best-effort basis so
    brackets inside them aren't counted.
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
