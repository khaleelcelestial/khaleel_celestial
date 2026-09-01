"""
Single-shot, whole-layer code generation - the deterministic replacement for
running backend/frontend generation through a ReAct tool-calling loop.

Architectural rationale (see the staff-level review this closes): backend/
frontend's RUN step is a pure "read the known context, write the files"
task - every piece of information it needs (schema, contract, existing
files, feedback) is already known BEFORE the call starts, and nothing about
which file to write next depends on an unpredictable prior tool result.
That's exactly the case where ReAct (a loop justified by "the next action
depends on what a previous action returned") adds N-1 unnecessary model
round-trips for an N-file layer, with no upside - confirmed live, repeatedly,
this session: a 7-file backend write cost 7+ separate LLM calls through
create_react_agent, one per write_file, plus repeated list_files/read_file
calls on unchanged content despite the data already being in the prompt.

E2E/CICD are the opposite case - "run docker compose ps, read its real
(unpredictable) output, decide what to check next" is genuinely adaptive,
so run_tool_agent's ReAct loop stays justified there. This module is
deliberately NOT a replacement for that.

Output format: a plain-text delimiter scheme, not JSON - the same reasoning
that already led agent_runtime.py's Gateway path to move write_file content
out of JSON entirely (multi-line code full of quotes/backslashes/newlines is
extremely error-prone to JSON-escape correctly). "@@@" delimiters are chosen
because they're vanishingly unlikely to collide with real code content
(unlike triple-backtick fences, which CAN legitimately appear inside a
docstring/comment that itself shows example code).
"""

import re

from core.model_router import get_router
from core.logger import get_logger
from skills.agent_tools import _resolve_scoped_path

FILE_FORMAT_INSTRUCTIONS = """
Output EVERY file you need to write or change using EXACTLY this format, one block per file, and
nothing else outside these blocks (no prose before/after, no markdown fences around the blocks
themselves):

@@@FILE: relative/path/to/file.ext@@@
<the complete, exact file content - no escaping, no quotes needed, raw as it should appear on disk>
@@@END@@@

Repeat that block for every file. To remove a file instead of writing it, use:

@@@DELETE: relative/path/to/file.ext@@@

Write EVERY file the task needs in THIS SAME response, in one pass - there is no follow-up turn to
add a file you forgot. Do not truncate or abbreviate a file's content with "..." or "// rest unchanged" -
each @@@FILE@@@ block must contain that file's COMPLETE content as it should exist on disk.

CRITICAL: only output a @@@FILE@@@ block for a file whose content actually needs to change for THIS
specific task. Some existing files are shown to you below purely as reference/context (to understand
the current app - its routes, models, conventions) - that does NOT mean you should rewrite them. If a
reference file doesn't need to change, do not include a @@@FILE@@@ block for it at all. Never
restructure, reorganize, rename, or "improve" a file's existing content (routes, components, functions)
unless the task explicitly asks for that specific change - an app-shell/routing file that already has
routes for pages A, B, and C must still have all of A, B, and C after your edit, even if your only
actual job was to add page D. When in doubt about whether to touch a file: don't.

CRITICAL for list/array content (navigation items, menu entries, route tables, dropdown options, and
similar): when adding one entry to an existing list, copy every existing entry into your output
UNCHANGED and append/insert only the new one. Do not regenerate the list from memory or from what you
infer it "should" contain - reproduce it verbatim from the existing file content shown to you, plus
your one change. Silently dropping an existing entry while adding a new one is the single most common
mistake here - re-check your output list against the existing file's list before finishing.
"""

_FILE_BLOCK_PATTERN = re.compile(r"@@@FILE:\s*(.+?)@@@\r?\n(.*?)(?=\r?\n@@@END@@@)\r?\n@@@END@@@", re.DOTALL)
_DELETE_PATTERN = re.compile(r"@@@DELETE:\s*(.+?)@@@")


def parse_batch_response(text: str) -> tuple[dict[str, str], list[str]]:
    """
    Parse a model's @@@FILE@@@/@@@DELETE@@@ response into (files, deletes).
    files: relative path -> content (paths as the model wrote them, not yet
    scoped/validated - that happens in write_batch_files). deletes: list of
    relative paths. Tolerant of extra prose around the blocks (a small model
    occasionally adds a summary sentence despite being told not to) - only
    the recognized blocks are extracted, everything else is ignored.
    """
    files = {}
    for match in _FILE_BLOCK_PATTERN.finditer(text):
        path = match.group(1).strip()
        content = match.group(2)
        files[path] = content
    deletes = [m.group(1).strip() for m in _DELETE_PATTERN.finditer(text)]
    return files, deletes


def write_batch_files(project_dir, write_prefix: str, files: dict[str, str],
                      deletes: list[str]) -> tuple[list[str], list[str], list[str]]:
    """
    Deterministically write/delete every parsed file, enforcing the same
    write_prefix scoping and path-escape safety as agent_tools.py's
    write_file/delete_file tools (reuses the exact same _resolve_scoped_path
    check - no separate/weaker safety logic for this path).

    Returns (written_paths, deleted_paths, refused_paths) - refused_paths
    covers anything outside write_prefix or that tried to escape project_dir,
    same as a tool call would have refused, just without spending a model
    turn to find out.
    """
    logger = get_logger()
    stage_label = write_prefix.rstrip("/") if write_prefix else ""
    written, deleted, refused = [], [], []
    unchanged = 0

    for path, content in files.items():
        normalized = path.replace("\\", "/").lstrip("./")
        if write_prefix and not normalized.startswith(write_prefix):
            refused.append(path)
            logger.warning(f"  Batch write refused '{path}' - outside this agent's scope ({write_prefix})")
            continue
        try:
            full_path = _resolve_scoped_path(project_dir, path)
        except ValueError as e:
            refused.append(path)
            logger.warning(f"  Batch write refused '{path}': {e}")
            continue
        # No redundant writes: a file the model regenerated identically to
        # what's already on disk (common on a revision round where most of
        # the response is just "confirming" unchanged files) costs a real
        # filesystem write and - more importantly downstream - makes
        # touched_since()-based "did this round make real progress" checks
        # and file-content-hash caches (e.g. skills/review_cache.py) treat
        # it as freshly changed when nothing actually changed.
        existed = full_path.exists() and full_path.is_file()
        prior_content = None
        if existed:
            try:
                prior_content = full_path.read_text(encoding="utf-8")
                if prior_content == content:
                    unchanged += 1
                    logger.file_changed(stage_label, path, "SKIPPED",
                                       size_before=len(prior_content), size_after=len(content))
                    continue
            except (UnicodeDecodeError, OSError):
                prior_content = None  # can't compare - fall through and write normally
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        logger.file_changed(stage_label, path, "MODIFIED" if existed else "CREATED",
                           size_before=len(prior_content) if prior_content is not None else None,
                           size_after=len(content))
        written.append(path)

    for path in deletes:
        normalized = path.replace("\\", "/").lstrip("./")
        if write_prefix and not normalized.startswith(write_prefix):
            refused.append(path)
            continue
        try:
            full_path = _resolve_scoped_path(project_dir, path)
        except ValueError:
            refused.append(path)
            continue
        if full_path.exists() and full_path.is_file():
            size_before = full_path.stat().st_size
            full_path.unlink()
            deleted.append(path)
            logger.file_changed(stage_label, path, "DELETED", size_before=size_before)
            parent = full_path.parent
            while parent != project_dir and parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent

    logger.info(f"  Batch generation: wrote {len(written)}, deleted {len(deleted)}"
               + (f", {unchanged} unchanged (skipped)" if unchanged else "")
               + (f", refused {len(refused)}" if refused else ""))
    return written, deleted, refused


def run_batch_generation(skill_name: str, system_prompt: str, user_message: str) -> tuple[str, bool]:
    """
    One deterministic, single-shot generation call - no tool-calling loop.
    Uses model_router's own `invoke()`, which already falls back through
    every configured credential for this skill's tier on failure (the same
    mechanism database_skills.generate_schema_skill already relies on) - so
    provider resilience is preserved without a ReAct agent.

    Returns (raw_response_text, ok) - ok is False only when every configured
    credential for this skill failed, matching run_tool_agent's contract so
    callers don't need to special-case which path generated the result.
    """
    router = get_router()
    logger = get_logger()
    try:
        response = router.invoke(skill_name, [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ])
        return response, True
    except RuntimeError as e:
        logger.warning(f"  Batch generation unavailable for '{skill_name}': {str(e)[:200]}")
        return f"Agent unavailable: {str(e)[:200]}", False
