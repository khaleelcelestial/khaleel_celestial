"""
Shared routing logic for every stage's RUN -> UT -> VAL graph nodes (see
core/graph.py). Each stage (database/backend/frontend/testing/deployment) is
now three real LangGraph nodes instead of one node hiding a Python for-loop -
this is what makes the compiled graph's mermaid diagram actually show the
documented RUN/UT/VAL structure. MAX_STAGE_ATTEMPTS is the same cap every
stage's old self-heal loop already used (MAX_SELF_HEAL_ATTEMPTS), just
centralized here since it's now shared across separate node functions
instead of a single function's local loop.

Convention every stage's UT/VAL node follows: write "" to
runtime.stage_feedback[stage] on pass, a non-empty description on fail -
stage_passed() below reads that single field so the routing here never needs
stage-specific knowledge of what "passed" means for e.g. database vs cicd.
"""

MAX_STAGE_ATTEMPTS = 3


def checkpoint(state) -> None:
    """
    Persist a resumable project-registry snapshot (.project.json +
    .workspace_snapshot.json) - called at the START of every single RUN/UT/
    VAL node (see each capability's node methods), not just once per outer
    Supervisor round. A crash/kill mid-stage (e.g. partway through a self-
    heal attempt) previously left the on-disk registry snapshot up to a full
    round stale; this closes that gap down to "at most one node's worth of
    work." Workspace is freshly synced from disk (always accurate,
    regardless of which node last touched it) - only stage_status/metadata
    come from the incoming state, which is at most one node behind current.
    No-op before the Planner has assigned a project_id (nothing to save yet).
    """
    project_id = state.get("project", {}).get("project_id", "")
    if not project_id:
        return

    from skills.project_registry import project_dir_for, sync_workspace_from_disk, save_project_snapshot

    project_dir = project_dir_for(project_id)
    workspace = sync_workspace_from_disk(project_dir, state["project"].get("workspace", {}))
    save_project_snapshot(
        project_dir=project_dir,
        project_id=project_id,
        user_request=state.get("user_request", ""),
        execution_plan=state["runtime"].get("execution_plan", {}),
        requirements=state["project"].get("requirements", ""),
        architecture=state["project"].get("architecture", ""),
        acceptance_criteria=state["project"].get("acceptance_criteria", []),
        tasks=state["project"].get("tasks", []),
        workspace=workspace,
        stage_status=state["runtime"].get("stage_status", {}),
    )


def stage_passed(state, stage: str) -> bool:
    return not state["runtime"].get("stage_feedback", {}).get(stage, "")


# Cap on how many feedback-referenced files get their full content inlined -
# feedback can theoretically name many files; without a cap a pathological
# case could blow up context size the same way we're trying to avoid blowing
# up tool-call count.
_MAX_INLINE_FEEDBACK_FILES = 5


_MAX_TOTAL_INLINE_BYTES = 30000  # a typical small generated project's whole
                                 # backend/ or frontend/ fits well under this


def existing_files_context(files: dict, feedback: str, stage_prefix: str, extra_text: str = "") -> str:
    """
    Real, observed problem: BE_RUN/FE_RUN call list_files + read_file on the
    same handful of files EVERY round even when nothing about them changed
    (confirmed live - list_files(backend), list_files(backend/routers),
    read_file(posts.py/__init__.py/database.py/models.py/etc.) repeated
    verbatim across consecutive rounds), each round-trip costing one full
    model call and pushing the provider closer to its rate limit. `files`
    (from workspace[stage]["files"], already synced from disk) already has
    everything list_files/read_file would return - this formats that same
    information directly into the prompt instead.

    First cut of this only inlined a file's content when `feedback`
    (stage_feedback - i.e. a UT/VAL self-heal retry) named it - confirmed
    live this misses the common case: round 1 of a --update run has NO
    feedback yet (nothing has failed this run), so a request like "fix the
    syntax error in backend/routers/__init__.py" named the file in the raw
    user_request/context text instead, which wasn't checked at all - the
    agent still called read_file/list_files on everything, and worse, ended
    up rewriting every backend file from scratch instead of the one asked
    for. Fixed properly: when the whole file set is small enough to be safe
    to inline outright (the common case for these generated projects),
    inline ALL of it - no text-matching heuristic needed, and it can't miss.
    Only for a project too large to inline wholesale does this fall back to
    matching file paths against feedback + extra_text (e.g. the user_request/
    context passed in by the caller).

    Returns "" if there are no existing files yet (nothing to summarize on a
    fresh build's first attempt).
    """
    if not files:
        return ""

    feedback = feedback or ""
    haystack = f"{feedback}\n{extra_text or ''}"
    listing = "\n".join(f"  {stage_prefix}{path}" for path in sorted(files))

    total_size = sum(len(c) for c in files.values())
    if total_size <= _MAX_TOTAL_INLINE_BYTES:
        inlined = list(files.items())
    else:
        inlined = [(path, content) for path, content in files.items()
                  if path in haystack or f"{stage_prefix}{path}" in haystack]
        inlined = inlined[:_MAX_INLINE_FEEDBACK_FILES]

    block = f"\n\nFiles already on disk under {stage_prefix} (already known - no need to list_files for these):\n{listing}"
    for path, content in inlined:
        block += (f"\n\n{stage_prefix}{path} (full current content already provided - no need to read_file it):\n{content}")
    return block


def known_issue_files_context(issue_files: dict) -> str:
    """
    Same idea as existing_files_context, for read-only verifier stages
    (E2E/CICD) instead of a write-scoped one: real, observed problem - E2E_RUN
    re-reads the same handful of files (e.g. backend/database.py,
    backend/Dockerfile, backend/main.py) every single round via read_file,
    even when a review_code/static-check finding against them hasn't changed
    since the last pass, each read costing a full model round-trip. Since the
    previous round's review_issues already say exactly which files matter,
    and the current on-disk content is already available (workspace, synced
    from disk), inline it directly instead of making the agent re-fetch it.

    issue_files: {full/path.ext: current content} for every file a prior
    round's review_issues named. Returns "" if there's nothing to inline yet
    (first pass this run - nothing flagged as an issue yet).
    """
    if not issue_files:
        return ""
    block = "\n\nFiles named by a previous review/static-check pass (already provided below - no need " \
            "to read_file these unless you need to re-verify something not shown here):"
    for path, content in issue_files.items():
        block += f"\n\n{path}:\n{content}"
    return block


def route_after_run(state, stage: str, next_node: str) -> str:
    """
    Used by every RUN node's conditional edge: if this attempt made zero
    real progress (no files touched at all), there's no point spending a
    UT/VAL check on it - bail straight back to the Supervisor. Only
    backend/frontend/cicd set stage_progress at all (the "the LLM replied
    with only a text plan"/"Docker isn't available" failure modes);
    database/e2e default to True (always proceed to UT).
    """
    if state["runtime"].get("stage_progress", {}).get(stage, True):
        return next_node
    return "supervisor"


def route_after_ut(state, stage: str, run_node: str, val_node: str) -> str:
    """
    UT passing moves on to VAL. UT failing loops back to RUN if attempts
    remain, otherwise gives up (the UT node itself is responsible for
    marking stage_status "failed" in that case) and exits to the Supervisor.
    """
    from core.logger import get_logger
    logger = get_logger()
    attempts = state["runtime"].get("stage_attempts", {}).get(stage, 0)
    feedback = state["runtime"].get("stage_feedback", {}).get(stage, "")

    if stage_passed(state, stage):
        logger.validator_result(stage, "ut", "UT", "PASS")
        return val_node

    logger.validator_result(stage, "ut", "UT", "FAIL", message=feedback)
    if attempts < MAX_STAGE_ATTEMPTS:
        logger.retry(stage, attempts, MAX_STAGE_ATTEMPTS, "UT failed", issue_count=None)
        return run_node
    logger.stage_completed(stage, "FAILED")
    return "supervisor"


def route_after_val(state, stage: str, run_node: str) -> str:
    """
    VAL passing exits the stage's sub-loop (done) back to the Supervisor.
    VAL failing loops back to RUN if attempts remain, otherwise gives up
    (marking stage_status "failed") and exits to the Supervisor.
    """
    from core.logger import get_logger
    logger = get_logger()
    attempts = state["runtime"].get("stage_attempts", {}).get(stage, 0)
    feedback = state["runtime"].get("stage_feedback", {}).get(stage, "")

    if stage_passed(state, stage):
        logger.validator_result(stage, "val", "VAL", "PASS")
        logger.stage_completed(stage, "PASSED")
        return "supervisor"

    logger.validator_result(stage, "val", "VAL", "FAIL", message=feedback)
    if attempts < MAX_STAGE_ATTEMPTS:
        logger.retry(stage, attempts, MAX_STAGE_ATTEMPTS, "VAL failed", issue_count=None)
        return run_node
    logger.stage_completed(stage, "FAILED")
    return "supervisor"
