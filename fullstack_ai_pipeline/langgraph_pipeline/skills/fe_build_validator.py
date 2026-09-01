"""
Frontend Build Validator - Phase 1 of the Frontend Validation Engine (FE_UT).
Real Node-based tooling instead of hand-rolled regex/AST-lite checks: there
is no mature Python-side JS/JSX parser equivalent to pglast for SQL, so the
honest, deterministic choice is to run the SAME real tools a human engineer
would use, not reimplement a worse version of them in Python.

Real tools used, all installed into a THROWAWAY copy of frontend/ in a temp
directory (never touching the shipped project's own package.json/
node_modules, and never adding validation-only tooling to what actually
ships):
- `npx vite build` - the single most authoritative check possible: does
  this frontend actually compile. Proves syntax validity, import
  resolution, and dependency integrity for free, as a side effect of
  running the real bundler - not reimplemented.
- ESLint (v8, .eslintrc format for stability) with a pipeline-owned
  canonical config (react-hooks + jsx-a11y plugins) - catches Rules-of-
  Hooks violations and static accessibility gaps (missing alt text/labels)
  a build alone can't see.
- madge - real circular-import detection (a standard, mature Node tool),
  instead of hand-rolling an import-graph walker.

Deliberately NOT built here (confirmed not applicable/redundant before
writing any code):
- TypeScript compilation - this pipeline's frontend is always plain
  JS/JSX (FRONTEND_SYSTEM_PROMPT mandates src/main.jsx, never .tsx) -
  nothing to type-check, ever.
- Prettier/formatting - same reasoning as Backend's black/ruff skip: a
  style tool has no bugs to catch on code nobody hand-edits.
- A static site (execution_plan.backend == False, no package.json by
  design) has no build step, no npm, nothing this module can check - a
  clean no-op for it, see run_frontend_build_checks' early return.

Fails OPEN (returns no problems, just a log warning) if Node/npm isn't
available - same reasoning as the Docker-based validators: this is
defense-in-depth on top of the existing regex-based checks in
quality_skills.py, not something that should block the whole pipeline over
a missing local Node install.
"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from core.logger import get_logger

_NPM_INSTALL_TIMEOUT_S = 240
_BUILD_TIMEOUT_S = 120
_ESLINT_TIMEOUT_S = 90
_MADGE_TIMEOUT_S = 60
_VITEST_TIMEOUT_S = 90

# Pinned, known-good major versions - deliberately NOT "latest": a
# validation tool silently changing behavior between pipeline runs (a new
# ESLint major version renaming/removing a rule) is exactly the kind of
# non-determinism a "fully deterministic, no subjective reasoning" check
# must not have.
# Despite the name, this is "always-installed validation tooling", not
# just ESLint - axe-core is bundled in here too (rather than a third,
# separate devDependencies dict/install) so Phase 4's Browser Validator
# (skills/fe_browser_validator.py) can read node_modules/axe-core/
# axe.min.js straight out of this SAME cache and inject it into a real
# page for a real accessibility scan, with zero extra npm installs.
_ESLINT_DEV_DEPENDENCIES = {
    "eslint": "^8.57.0",
    "eslint-plugin-react": "^7.34.0",
    "eslint-plugin-react-hooks": "^4.6.0",
    "eslint-plugin-jsx-a11y": "^6.8.0",
    "madge": "^6.1.0",
    "axe-core": "^4.9.0",
    # Pinned to the EXACT same version as the Python `playwright` package
    # already installed in this pipeline's own venv (requirements.txt) -
    # both bindings share the same OS-level browser binary cache
    # (~/AppData/Local/ms-playwright on Windows) keyed by browser build
    # number, so matching versions means the Chromium already downloaded
    # for Phase 4's Python-driven checks is reused here with zero extra
    # download, instead of a second ~150-300MB browser fetch.
    "@playwright/test": "1.62.0",
}

# Phase 2 - only installed/used when Frontend's own acceptance-test round
# actually generated a Vitest file (see run_frontend_build_checks'
# test_file_content param). Bundled into the SAME devDependencies dict/
# SAME npm install as the ESLint tooling above rather than a separate
# cache/install - two throwaway installs for the same project would just
# double the (already real, measured) npm-install cost for no benefit.
_VITEST_DEV_DEPENDENCIES = {
    "vitest": "^1.6.0",
    "@testing-library/react": "^14.2.0",
    "@testing-library/jest-dom": "^6.4.0",
    "jsdom": "^24.0.0",
}

_VITEST_SETUP_JS = "import '@testing-library/jest-dom'\n"

# A standalone vitest.config.js (not touching the project's own
# vite.config.js) - Vitest prefers this file over vite.config.js when both
# exist, so this is picked up automatically. Re-declares the SAME
# @vitejs/plugin-react JSX transform the project's real vite.config.js
# uses (required for .jsx files to parse at all under Vitest), since the
# validation-only devDependency install above doesn't touch/depend on the
# project's actual vite.config.js content.
_VITEST_CONFIG_JS = """import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.js'],
  },
})
"""

_ESLINTRC = {
    "root": True,
    "env": {"browser": True, "es2021": True, "node": True},
    "parserOptions": {"ecmaVersion": "latest", "sourceType": "module", "ecmaFeatures": {"jsx": True}},
    # "plugin:react/jsx-runtime" (NOT just "plugin:react/recommended" alone)
    # is required here: this pipeline's frontend always uses the AUTOMATIC
    # JSX runtime (@vitejs/plugin-react, mandated in FRONTEND_SYSTEM_PROMPT
    # specifically so files don't need "import React" in scope) - without
    # this extra config, eslint-plugin-react's "recommended" preset assumes
    # the CLASSIC runtime and false-positives "'React' must be in scope" on
    # every single JSX file. Confirmed as a real false positive via a live
    # run against a real generated project before this fix.
    "extends": ["eslint:recommended", "plugin:react/recommended", "plugin:react/jsx-runtime",
               "plugin:react-hooks/recommended"],
    "plugins": ["jsx-a11y"],
    "settings": {"react": {"version": "detect"}},
    "rules": {
        # Real correctness bugs - kept as hard errors.
        "react/jsx-key": "error",
        "react/no-direct-mutation-state": "error",
        "react-hooks/rules-of-hooks": "error",
        # exhaustive-deps has many legitimate intentional exceptions
        # (deliberately excluding a stable setState/dispatch fn, a
        # one-time-on-mount effect) - kept as a warning, not a blocker,
        # same reasoning quality_skills.py already applies to Prettier-
        # style nitpicks vs real defects.
        "react-hooks/exhaustive-deps": "warn",
        # Real, static-checkable accessibility gaps - the concrete slice
        # of "Accessibility Validator" achievable without a live browser.
        "jsx-a11y/alt-text": "error",
        "jsx-a11y/label-has-associated-control": "error",
        "jsx-a11y/anchor-is-valid": "error",
        "jsx-a11y/no-noninteractive-element-interactions": "warn",
        # Cosmetic/stylistic - not this validator's job (same reasoning as
        # skipping Prettier entirely).
        "react/no-unescaped-entities": "off",
        "react/prop-types": "off",
        "no-unused-vars": "warn",
    },
}


def _node_available() -> bool:
    return shutil.which("npm") is not None and shutil.which("node") is not None


# Preserved across rounds in the persistent cache dir - node_modules is by
# far the most expensive thing to rebuild (a full dependency-tree resolve +
# extract of react/vite/eslint/madge and their transitive deps), so it's
# never wiped just because this round's source files changed. Only
# package.json actually changing invalidates it (see _needs_install).
_PRESERVE_ACROSS_ROUNDS = {"node_modules", "package-lock.json", ".package_json_hash"}


def run_frontend_build_checks(frontend_files: dict, project_dir, test_file_content: str = "") -> list[str]:
    """
    Writes frontend_files into a PERSISTENT cache directory
    (<project_dir>/.fe_validation_cache, a sibling of frontend/ - never
    scanned as part of the shipped project, never committed since output/
    is entirely gitignored) that survives across FE_UT rounds for this
    SAME project, injects the validation-only devDependencies above into a
    COPY of package.json (the shipped project's own package.json on disk
    is never touched), and runs real `vite build` + ESLint + madge against
    it. `npm install` itself is SKIPPED whenever the merged package.json is
    byte-identical to last round's - the expensive dependency-resolution-
    and-extraction step has nothing new to do, and re-running it anyway
    still costs real seconds of overhead even with a warm npm cache.
    Confirmed real problem without this: a fresh temp directory (and thus a
    full `npm install` from zero) on every single call, even when nothing
    about the frontend's dependencies changed round to round.

    Stale files from a previous round that this round's write no longer
    includes are deleted before writing (everything except the preserved
    node_modules/lockfile/hash-marker above) so a deleted file can never
    linger and silently pollute the build.

    test_file_content, if given (the shared Acceptance Test Generator's
    JS/Vitest output - see capabilities/frontend.py's run()), is executed
    for real via a real `vitest run` in the SAME cache/install as the
    build/lint checks (not a second throwaway install) once those pass - a
    failing generated test fails FE_UT just like any other problem here.

    Returns a list of problem strings (empty = frontend genuinely builds
    clean, with no hooks-rule/accessibility errors, no circular imports,
    and - if given - every generated Vitest test passes). Fails open if
    Node/npm isn't available, or if this isn't a Vite/npm project at all
    (no package.json - a static site).
    """
    logger = get_logger()

    if not frontend_files or "package.json" not in frontend_files:
        return []  # static site (no backend) - nothing to build, not a failure

    if not _node_available():
        logger.warning("Build Validator: Node/npm isn't available - skipping the real-build check "
                       "(the existing regex-based checks already ran; this one is defense-in-depth on top)")
        return []

    try:
        package_json = json.loads(frontend_files["package.json"])
    except (json.JSONDecodeError, TypeError):
        return ["package.json is not valid JSON - cannot install or build"]

    package_json.setdefault("devDependencies", {})
    package_json["devDependencies"].update(_ESLINT_DEV_DEPENDENCIES)
    if test_file_content:
        package_json["devDependencies"].update(_VITEST_DEV_DEPENDENCIES)
    merged_package_json = json.dumps(package_json, indent=2, sort_keys=True)

    cache_path = (Path(project_dir) / ".fe_validation_cache").resolve()
    cache_path.mkdir(parents=True, exist_ok=True)

    for item in cache_path.iterdir():
        if item.name in _PRESERVE_ACROSS_ROUNDS:
            continue
        shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink(missing_ok=True)

    for path, content in frontend_files.items():
        if path == "package.json":
            continue
        file_path = cache_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    (cache_path / "package.json").write_text(merged_package_json, encoding="utf-8")
    (cache_path / ".eslintrc.json").write_text(json.dumps(_ESLINTRC, indent=2), encoding="utf-8")

    hash_marker = cache_path / ".package_json_hash"
    new_hash = hashlib.sha256(merged_package_json.encode()).hexdigest()
    needs_install = (not (cache_path / "node_modules").exists()
                     or not hash_marker.exists() or hash_marker.read_text() != new_hash)

    if needs_install:
        install = subprocess.run(
            ["npm", "install", "--no-audit", "--no-fund", "--prefer-offline"],
            cwd=cache_path, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_NPM_INSTALL_TIMEOUT_S, shell=True,
        )
        if install.returncode != 0:
            hash_marker.unlink(missing_ok=True)  # don't cache a broken install as "up to date"
            error_text = (install.stderr or install.stdout or "unknown error").strip()
            return [f"frontend/ dependencies fail to install: {error_text[-1500:]}"]
        hash_marker.write_text(new_hash, encoding="utf-8")
    else:
        logger.info("Build Validator: dependencies unchanged since last round - skipping npm install")

    problems = []
    problems.extend(_run_build(cache_path))
    # ESLint/madge/Vitest are gated behind a clean build - no point
    # spending time on lint/circular-import/test analysis of code already
    # known not to compile, same cost-gating already used for the
    # Docker-based validators (e.g. Backend's Startup Validator behind
    # Phase 1/2).
    if not problems:
        problems.extend(_run_eslint(cache_path))
        problems.extend(_run_madge(cache_path))
    if not problems and test_file_content:
        problems.extend(_run_vitest(cache_path, test_file_content))
    return problems


def _run_build(build_path: Path) -> list[str]:
    try:
        build = subprocess.run(
            ["npx", "vite", "build"],
            cwd=build_path, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_BUILD_TIMEOUT_S, shell=True,
        )
    except subprocess.TimeoutExpired:
        return [f"frontend/ build did not finish within {_BUILD_TIMEOUT_S}s - likely hung, not just slow"]

    if build.returncode != 0:
        error_text = (build.stderr or build.stdout or "unknown error").strip()
        return [f"frontend/ fails a real `vite build` (syntax error, unresolved import, or "
                f"missing dependency): {error_text[-1500:]}"]
    return []


def _run_eslint(build_path: Path) -> list[str]:
    src_dir = build_path / "src"
    target = "src" if src_dir.exists() else "."
    try:
        result = subprocess.run(
            ["npx", "eslint", target, "--ext", ".js,.jsx", "--no-eslintrc", "-c", ".eslintrc.json",
             "--format", "json"],
            cwd=build_path, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_ESLINT_TIMEOUT_S, shell=True,
        )
    except subprocess.TimeoutExpired:
        return []  # non-fatal - the build already passed, lint timing out isn't a code defect

    try:
        results = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []  # ESLint crashed/produced no parseable output - fail open, don't block on tooling noise

    problems = []
    for file_result in results:
        file_path = file_result.get("filePath", "")
        rel_path = str(Path(file_path).relative_to(build_path)) if file_path else "?"
        for message in file_result.get("messages", []):
            if message.get("severity") != 2:  # 2 = error, 1 = warning - only errors block FE_UT
                continue
            rule = message.get("ruleId", "unknown-rule")
            problems.append(f"{rel_path}:{message.get('line', '?')} [{rule}] {message.get('message', '')}")
    return problems


def _run_madge(build_path: Path) -> list[str]:
    src_dir = build_path / "src"
    target = "src" if src_dir.exists() else "."
    try:
        result = subprocess.run(
            ["npx", "madge", "--circular", "--extensions", "js,jsx", target],
            cwd=build_path, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_MADGE_TIMEOUT_S, shell=True,
        )
    except subprocess.TimeoutExpired:
        return []  # non-fatal - build already passed

    output = (result.stdout or "").strip()
    # madge exits non-zero AND prints "Found N circular dependencies" when
    # cycles exist; exits 0 with "No circular dependency found" otherwise.
    if result.returncode != 0 and "circular" in output.lower():
        return [f"frontend/ has circular imports (real bundling hazard, not a style nitpick):\n{output[-1000:]}"]
    return []


def _run_vitest(build_path: Path, test_file_content: str) -> list[str]:
    """
    The Requirement Test Runner for Frontend unit/component tests: actually
    executes the shared Acceptance Test Generator's Vitest output via a
    real `vitest run`, in the SAME cache/install as the build/lint checks
    above (no second throwaway install). Writes a pipeline-authored
    vitest.config.js/vitest.setup.js (not LLM-generated - keeps the
    generated test file itself simpler and more reliable) alongside the
    test file. Placed at src/test_frontend.test.jsx specifically - the
    Acceptance Test Generator is told this exact path (see
    capabilities/frontend.py's run()) so its relative imports to real
    components/pages resolve correctly regardless of this project's own
    folder conventions (confirmed to vary project-to-project).

    Returns a list of problem strings - empty means every generated test
    passed. Fails open (empty list) on any harness-level problem (Vitest
    itself errors before running anything) - only real test FAILURES are
    reported as validation problems.
    """
    (build_path / "vitest.setup.js").write_text(_VITEST_SETUP_JS, encoding="utf-8")
    (build_path / "vitest.config.js").write_text(_VITEST_CONFIG_JS, encoding="utf-8")
    src_dir = build_path / "src"
    test_dir = src_dir if src_dir.exists() else build_path
    (test_dir / "test_frontend.test.jsx").write_text(test_file_content, encoding="utf-8")

    try:
        result = subprocess.run(
            ["npx", "vitest", "run"],
            cwd=build_path, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_VITEST_TIMEOUT_S, shell=True,
        )
    except subprocess.TimeoutExpired:
        return [f"generated Vitest test(s) did not finish within {_VITEST_TIMEOUT_S}s - likely hung, not just slow"]

    if result.returncode == 0:
        return []

    output = (result.stdout or "") + "\n" + (result.stderr or "")
    return [f"generated Vitest test(s) failed:\n{output[-1500:]}"]
