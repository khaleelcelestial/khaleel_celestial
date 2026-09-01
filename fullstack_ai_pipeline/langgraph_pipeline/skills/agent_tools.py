"""
Real tool access for the supervisor workflow's agents: scoped file
read/write/list, and an allowlisted terminal command runner.

This is what makes agents genuinely coordinate instead of writing blind -
the frontend agent can read what the backend agent actually wrote (routes,
port config) instead of only seeing an OpenAPI spec, and the testing/
deployment agents can run real commands and see real results instead of an
LLM guessing whether generated code works.
"""

import re
import subprocess
from pathlib import Path
from typing import Union

from langchain_core.tools import tool

from core.logger import get_logger

_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:/")

# Prefix-allowlist: only commands starting with one of these are permitted.
# Combined with the character blacklist below (no shell metacharacters), this
# prevents an agent from running arbitrary commands or chaining a second
# command onto an allowed one.
ALLOWED_COMMAND_PREFIXES = (
    "pytest",
    "python -m pytest",
    "python -m py_compile",
    "npm install",
    "npm test",
    "npm run",
    "docker compose build",
    "docker compose up",
    "docker compose down",
    "docker compose ps",
    "docker compose logs",
    "docker compose run",
)

FORBIDDEN_CHARS = (";", "&&", "||", "|", "`", "$(", ">", "<", "\n")

# Directories that are never part of the actual generated project, no matter
# which stack it's in - a Python venv, node_modules, VCS metadata, or cache
# dir left inside backend/ or frontend/ (e.g. from someone manually running
# `python -m venv venv` there to test the app outside Docker) must never be
# treated as project source. Real, observed failure: a `backend/venv/` left
# behind this way got walked by list_files/sync_workspace_from_disk and fed
# whole to static checks, which then "found" 600+ undefined-name errors
# inside third-party site-packages (typing_extensions.py etc.) - a real
# generated-code bug and a directory-full-of-someone-else's-library look
# identical to a walker that doesn't know to skip one.
_EXCLUDED_DIRS = ("node_modules", "venv", ".venv", "env", "__pycache__", ".git", ".pytest_cache")


def has_marker_file(root: Path, filenames: tuple) -> bool:
    """
    True if any file anywhere under root matches one of filenames - used as a
    minimum-viability check (e.g. a frontend/ with no package.json anywhere
    can't actually be installed/run, no matter how many other files exist).
    """
    if not root.exists():
        return False
    return any(p.name in filenames for p in root.rglob("*")
              if p.is_file() and not any(part in _EXCLUDED_DIRS for part in p.parts))


def touched_since(root: Path, since_ts: float) -> bool:
    """
    True if any file anywhere under root was written at or after since_ts -
    used to tell a round that actually wrote/changed something apart from a
    round where the agent replied with only a text plan and made zero
    write_file/delete_file calls. root already having files from a PRIOR
    round (has_marker_file / any(root.rglob("*"))) says nothing about
    whether THIS round did any real work - a stale "done" from a no-op round
    would otherwise loop forever re-reporting the same unfixed issue.
    """
    if not root.exists():
        return False
    return any(p.stat().st_mtime >= since_ts for p in root.rglob("*")
              if p.is_file() and not any(part in _EXCLUDED_DIRS for part in p.parts))


def files_touched_since(root: Path, since_ts: float) -> list[str]:
    """
    Same signal as touched_since(), but returns the actual list of
    root-relative paths instead of a bare bool - used by the incremental
    tool-calling generation path (skills/incremental_codegen.py), which
    writes files directly via real write_file/delete_file tool calls
    rather than a single parsed batch response, so there's no
    write_batch_files()-returned "written" list to report from. This gives
    capabilities/backend.py etc. the same "which files did this round
    touch" signal for task-status self-reporting regardless of which
    generation mode actually ran.
    """
    if not root.exists():
        return []
    return sorted(
        str(p.relative_to(root.parent)).replace("\\", "/")
        for p in root.rglob("*")
        if p.is_file() and not any(part in _EXCLUDED_DIRS for part in p.parts) and p.stat().st_mtime >= since_ts
    )


def _resolve_scoped_path(project_dir: Path, relative_path: str) -> Path:
    """Resolve a path an agent gave us, refusing anything that escapes project_dir."""
    project_dir = project_dir.resolve()

    # An agent occasionally emits a path as if it were root-anchored (e.g.
    # "/frontend/package.json" for a "top-level" file, or a full "C:/..."
    # path). pathlib's `/` operator treats the right side as authoritative
    # when it looks absolute - `Path("C:/project") / "/frontend/x"` silently
    # discards "C:/project" and resolves to "C:/frontend/x", which then
    # (correctly, but unhelpfully) trips the escape check below. Strip any
    # such anchor first so every path is treated as relative to project_dir,
    # which is what an agent always means here - there's no legitimate
    # reason for one of these tools to reach outside the project.
    cleaned = relative_path.replace("\\", "/").lstrip("/")
    cleaned = _DRIVE_PREFIX.sub("", cleaned)

    # Build the candidate path - don't resolve() yet since the file may not exist
    candidate = project_dir / cleaned
    
    # Check containment using absolute paths (resolving project_dir is enough)
    # We resolve candidate only for the comparison, using strict=False for Windows
    # compatibility with non-existent paths
    try:
        candidate_resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        # On some Windows configurations, resolve() can fail for paths that don't exist yet
        # Fall back to absolute() which works for non-existent paths
        candidate_resolved = candidate.absolute()
    
    if candidate_resolved != project_dir and project_dir not in candidate_resolved.parents:
        raise ValueError(f"path '{relative_path}' escapes the project directory - refused")
    
    # Return the unresolved path so write_file can create the directories
    return candidate


def make_agent_tools(project_dir: Path, write_prefix: str = "", allow_commands: bool = False,
                     include_static_checks: bool = False, include_write: bool = True,
                     include_search: bool = False) -> list:
    """
    Build the tool set for one agent.

    project_dir: the project's root output directory - every path an agent
                 gives is resolved relative to this.
    write_prefix: if set (e.g. "backend/"), write_file refuses any path
                 outside that subdirectory. read_file/list_files are NOT
                 restricted by this - an agent can always see the whole
                 project (that's the point: real coordination), it just
                 can't write outside its own area.
    allow_commands: whether this agent gets the run_command tool at all
                 (only testing/deployment agents should).
    include_write: whether this agent gets write_file at all. False for
                 testing/deployment - their job is to verify and deploy
                 what backend/frontend wrote, not silently rewrite it; if
                 they find a problem they report it back so the supervisor
                 can route to the agent that actually owns that code.
    include_search: whether this agent gets search_files - a plain-text/
                 regex grep across the project (see skills/
                 incremental_codegen.py). Deliberately ONE generic tool,
                 not five stack-specific ones (find_route/find_component/
                 etc.) - a universal grep works identically regardless of
                 language/framework, matching the "don't hardcode to one
                 tech stack" requirement, and list_files() already gives
                 an agent the full path tree in one call (often enough on
                 its own when names are descriptive) - this is only for
                 the remaining case of "which file actually defines X"
                 when the name alone doesn't say.
    """
    project_dir = Path(project_dir).resolve()
    project_dir.mkdir(parents=True, exist_ok=True)
    # Best-effort stage label for file_changed() events - derived from
    # write_prefix (e.g. "backend/" -> "backend") since a tool set is always
    # built scoped to one stage's own directory. Empty for read-only agents
    # (testing/deployment), which never call write_file/delete_file anyway.
    _stage_label = write_prefix.rstrip("/") if write_prefix else ""

    @tool
    def read_file(path: str) -> str:
        """Read a file's contents. path is relative to the project root (e.g. 'backend/main.py')."""
        logger = get_logger()
        tcid = logger.tool_call("read_file", path)
        try:
            full_path = _resolve_scoped_path(project_dir, path)
        except ValueError as e:
            logger.tool_result("read_file", f"ERROR: {e}", tcid)
            return f"ERROR: {e}"
        if not full_path.exists() or not full_path.is_file():
            logger.tool_result("read_file", f"not found: {path}", tcid)
            return f"ERROR: file not found: {path}"
        try:
            content = full_path.read_text(encoding="utf-8")
            logger.file_read(_stage_label, path, len(content.encode("utf-8")))
            logger.tool_result("read_file", f"{len(content)} chars", tcid)
            return content
        except Exception as e:
            logger.tool_result("read_file", f"ERROR: {e}", tcid)
            return f"ERROR reading {path}: {e}"

    @tool
    def list_files(subdirectory: str = ".") -> str:
        """List files under a subdirectory of the project root (default: the whole project)."""
        logger = get_logger()
        tcid = logger.tool_call("list_files", subdirectory)
        try:
            full_path = _resolve_scoped_path(project_dir, subdirectory)
        except ValueError as e:
            logger.tool_result("list_files", f"ERROR: {e}", tcid)
            return f"ERROR: {e}"
        if not full_path.exists():
            logger.tool_result("list_files", "(nothing here yet)", tcid)
            return f"(nothing here yet - {subdirectory} doesn't exist)"
        files = sorted(
            str(p.relative_to(project_dir)).replace("\\", "/")
            for p in full_path.rglob("*")
            if p.is_file() and not any(part in _EXCLUDED_DIRS for part in p.parts) and not p.name.startswith(".")
        )
        logger.tool_result("list_files", f"{len(files)} file(s)", tcid)
        return "\n".join(files) if files else "(no files)"

    @tool
    def write_file(path: str, content: str) -> str:
        """Write a file's full contents. path is relative to the project root (e.g. 'backend/main.py')."""
        logger = get_logger()
        tcid = logger.tool_call("write_file", f"{path}, {len(content)} chars")
        normalized = path.replace("\\", "/").lstrip("./")
        if write_prefix and not normalized.startswith(write_prefix):
            result = f"ERROR: this agent may only write under '{write_prefix}' - refused '{path}'"
            logger.tool_result("write_file", result, tcid)
            return result
        try:
            full_path = _resolve_scoped_path(project_dir, path)
        except ValueError as e:
            logger.tool_result("write_file", f"ERROR: {e}", tcid)
            return f"ERROR: {e}"

        existed = full_path.exists() and full_path.is_file()
        prior_content = None
        if existed:
            try:
                prior_content = full_path.read_text(encoding="utf-8")
            except Exception:
                prior_content = None  # unreadable/binary - treat as a real change below

        if existed and prior_content == content:
            logger.file_changed(_stage_label, path, "SKIPPED", size_before=len(prior_content), size_after=len(content))
            logger.tool_result("write_file", f"unchanged - skipped {path}", tcid)
            return f"OK: {path} already matches this content - no write needed"

        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        logger.file_changed(_stage_label, path, "MODIFIED" if existed else "CREATED",
                            size_before=len(prior_content) if prior_content is not None else None,
                            size_after=len(content))
        logger.tool_result("write_file", f"OK - wrote {path}", tcid)
        return f"OK: wrote {len(content)} chars to {path}"

    @tool
    def delete_file(path: str) -> str:
        """
        Actually remove a file (e.g. when abandoning a stack/approach and
        switching to a different one). path is relative to the project root.
        Use this instead of overwriting the file with empty content - an
        empty file is still a file (breaks package.json/requirements parsing,
        clutters the tree, can make static checks or a build think something
        exists when it doesn't).
        """
        logger = get_logger()
        tcid = logger.tool_call("delete_file", path)
        normalized = path.replace("\\", "/").lstrip("./")
        if write_prefix and not normalized.startswith(write_prefix):
            result = f"ERROR: this agent may only delete under '{write_prefix}' - refused '{path}'"
            logger.tool_result("delete_file", result, tcid)
            return result
        try:
            full_path = _resolve_scoped_path(project_dir, path)
        except ValueError as e:
            logger.tool_result("delete_file", f"ERROR: {e}", tcid)
            return f"ERROR: {e}"
        if not full_path.exists() or not full_path.is_file():
            logger.tool_result("delete_file", f"not found: {path}", tcid)
            return f"ERROR: file not found: {path}"
        size_before = full_path.stat().st_size
        full_path.unlink()
        # Prune now-empty parent directories back up to (not including)
        # project_dir, so removing the last file of an old subtree doesn't
        # leave a trail of empty folders behind.
        parent = full_path.parent
        while parent != project_dir and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
        logger.file_changed(_stage_label, path, "DELETED", size_before=size_before)
        logger.tool_result("delete_file", f"OK - deleted {path}", tcid)
        return f"OK: deleted {path}"

    tools = [read_file, list_files]

    if include_search:
        @tool
        def search_files(query: str, subdirectory: str = ".") -> str:
            """
            Search for a literal string or regex pattern across every
            source file under subdirectory (default: the whole project) -
            use this to find which file actually defines/uses something
            (e.g. "ReportsRouter", "def get_reports", "sidebar") when
            list_files()'s path names alone don't make it obvious. Returns
            up to 30 matches as "path:line: matched line text". Prefer
            this over read_file-ing files speculatively one by one when
            you're not sure which file has what you need.
            """
            logger = get_logger()
            tcid = logger.tool_call("search_files", query[:60])
            try:
                full_path = _resolve_scoped_path(project_dir, subdirectory)
            except ValueError as e:
                logger.tool_result("search_files", f"ERROR: {e}", tcid)
                return f"ERROR: {e}"
            if not full_path.exists():
                logger.tool_result("search_files", "(nothing here yet)", tcid)
                return f"(nothing here yet - {subdirectory} doesn't exist)"

            try:
                pattern = re.compile(query)
            except re.error:
                pattern = re.compile(re.escape(query))

            matches = []
            for p in sorted(full_path.rglob("*")):
                if not p.is_file() or any(part in _EXCLUDED_DIRS for part in p.parts) or p.name.startswith("."):
                    continue
                try:
                    text = p.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                rel = str(p.relative_to(project_dir)).replace("\\", "/")
                for i, line in enumerate(text.splitlines(), start=1):
                    if pattern.search(line):
                        matches.append(f"{rel}:{i}: {line.strip()[:200]}")
                        if len(matches) >= 30:
                            break
                if len(matches) >= 30:
                    break

            result = "\n".join(matches) if matches else f"No matches for '{query}'"
            logger.tool_result("search_files", f"{len(matches)} match(es)", tcid)
            return result

        tools.append(search_files)

    if include_write:
        tools.append(write_file)
        tools.append(delete_file)

    if include_static_checks:
        @tool
        def run_static_checks() -> str:
            """
            Run deterministic static checks over every backend/frontend file
            currently on disk: Python files are parsed (ast) and checked for
            undefined names (pyflakes) - catches real bugs like a typo'd
            exception name, which is syntactically valid Python but wrong.
            JS/TS files get a bracket-balance sanity check. Returns a summary.
            """
            logger = get_logger()
            tcid = logger.tool_call("run_static_checks")
            from skills.project_registry import sync_workspace_from_disk
            from skills.quality_skills import run_tests_skill

            workspace = sync_workspace_from_disk(project_dir, {})
            results = run_tests_skill(workspace)

            if not results["failures"]:
                summary = f"{results['passed']}/{results['total']} files passed static checks. No issues found."
                logger.tool_result("run_static_checks", summary, tcid)
                return summary

            lines = [f"{results['passed']}/{results['total']} passed, {results['failed']} failed:"]
            for f in results["failures"]:
                lines.append(f"  {f['file']}: {f['error']}")
            logger.tool_result("run_static_checks", f"{results['failed']} failure(s) found", tcid)
            return "\n".join(lines)

        tools.append(run_static_checks)

        @tool
        def review_code(tasks_summary: Union[str, list, None] = "") -> str:
            """
            Get a second opinion from an LLM code reviewer on backend/frontend
            files currently on disk - catches things static analysis can't
            (missing error handling, security issues, logic bugs, requirements
            the code doesn't actually satisfy yet). Use this in addition to
            run_static_checks, not instead of it. Pass a short summary of the
            required tasks/features if you have one, so the reviewer can check
            for missing functionality too. Only files that changed since the
            last review are actually re-sent to the LLM - unchanged files
            reuse their last verdict.
            """
            # Some models pass a list of task strings instead of one string -
            # accept either rather than letting a strict str-only schema
            # reject the call outright (that surfaced as a spurious testing
            # failure with no actual code problem behind it).
            if isinstance(tasks_summary, list):
                tasks_summary = ", ".join(str(t) for t in tasks_summary if t)
            tasks_summary = tasks_summary or ""

            logger = get_logger()
            tcid = logger.tool_call("review_code", tasks_summary[:60])
            from skills.project_registry import sync_workspace_from_disk
            from skills.quality_skills import review_code_skill
            from skills.review_cache import (
                load_review_cache, save_review_cache, split_changed_files, rebuild_cache
            )

            workspace = sync_workspace_from_disk(project_dir, {})
            all_files = {}
            for artifact_type in ("backend", "frontend"):
                for path, content in workspace.get(artifact_type, {}).get("files", {}).items():
                    all_files[f"{artifact_type}/{path}"] = content

            if not all_files:
                logger.tool_result("review_code", "no files to review yet", tcid)
                return "No files to review yet."

            cache = load_review_cache(project_dir)
            changed_files, current_hashes, cached_issues = split_changed_files(all_files, cache)

            new_issues_by_file = {}
            if changed_files:
                tasks = [tasks_summary] if tasks_summary else []
                new_issues = review_code_skill(changed_files, tasks)
                for issue in new_issues:
                    new_issues_by_file.setdefault(issue.get("file", ""), []).append(issue)

            updated_cache = rebuild_cache(all_files, current_hashes, cache,
                                          set(changed_files), new_issues_by_file)
            save_review_cache(project_dir, updated_cache)

            all_issues = cached_issues + [i for issues in new_issues_by_file.values() for i in issues]

            skipped = len(all_files) - len(changed_files)
            note = f"({len(changed_files)} file(s) re-reviewed, {skipped} unchanged/cached)"

            if not all_issues:
                logger.tool_result("review_code", f"no issues found {note}", tcid)
                return f"LLM review found no issues. {note}"

            lines = [f"LLM review found {len(all_issues)} issue(s) {note}:"]
            for issue in all_issues:
                lines.append(f"  [{issue.get('severity', '?')}] {issue.get('file', '?')}: "
                            f"{issue.get('description', '')}")
            logger.tool_result("review_code", f"{len(all_issues)} issue(s) found {note}", tcid)
            return "\n".join(lines)

        tools.append(review_code)

    if allow_commands:
        @tool
        def run_command(command: str) -> str:
            """
            Run a shell command inside the project directory to verify your work
            (e.g. "pytest", "npm install", "npm test", "docker compose up --build -d",
            "docker compose logs backend"). Only these operations are permitted -
            anything else is refused. Returns exit code + stdout/stderr (truncated).
            """
            logger = get_logger()
            tcid = logger.tool_call("run_command", command)

            stripped = command.strip()
            if not any(stripped.startswith(p) for p in ALLOWED_COMMAND_PREFIXES):
                result = (f"REFUSED: '{command}' is not an allowlisted command. "
                         f"Allowed prefixes: {', '.join(ALLOWED_COMMAND_PREFIXES)}")
                logger.tool_result("run_command", result, tcid)
                return result
            if any(bad in stripped for bad in FORBIDDEN_CHARS):
                result = "REFUSED: command contains disallowed characters (no chaining/redirection)"
                logger.tool_result("run_command", result, tcid)
                return result

            is_docker = stripped.startswith("docker")
            try:
                proc_result = subprocess.run(
                    stripped,
                    shell=True,
                    cwd=str(project_dir),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180,
                )
            except subprocess.TimeoutExpired:
                logger.tool_result("run_command", "ERROR: timed out after 180s", tcid)
                if is_docker:
                    logger.deployment("docker_command", "FAILED", {"command": stripped, "error_type": "TIMEOUT"})
                return "ERROR: command timed out after 180s"
            except OSError as e:
                logger.tool_result("run_command", f"ERROR: {e}", tcid)
                if is_docker:
                    logger.deployment("docker_command", "FAILED", {"command": stripped, "error_type": "DOCKER_ERROR"})
                return f"ERROR: failed to run command: {e}"

            logger.tool_result("run_command", f"exit code {proc_result.returncode}", tcid)
            if is_docker:
                logger.deployment("docker_command", "COMPLETED" if proc_result.returncode == 0 else "FAILED",
                                  {"command": stripped, "exit_code": proc_result.returncode})
            return (f"exit code: {proc_result.returncode}\n"
                    f"stdout:\n{proc_result.stdout[-3000:]}\n"
                    f"stderr:\n{proc_result.stderr[-1500:]}")

        tools.append(run_command)

    return tools
