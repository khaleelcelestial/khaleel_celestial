"""
Main entry point - Takes user request, invokes graph, returns final state
"""

import os
import sys
from pathlib import Path

# Load environment variables from .env file
from core.load_env import load_dotenv
load_dotenv()

from core.graph import app
from core.state import ProjectState, BuildStatus
from core.logger import get_logger
from core.status_tracker import StageTracker
from skills.project_registry import list_projects, load_project_snapshot


def _base_runtime(execution_plan: dict, mode: str = "build", stage_status: dict = None) -> dict:
    return {
        "mode": mode,
        "execution_plan": execution_plan,
        # None, not False - the Supervisor's own status message only shows
        # "not tested yet" when this "is not None" (see supervisor.py); a
        # starting value of False was indistinguishable from "Testing ran
        # and genuinely found a failure", so the Supervisor believed quality
        # had already failed before Testing had run even once, and kept
        # routing back to Backend instead of ever reaching Testing first.
        "quality_passed": None,
        "review_issues": [],
        "issue_history": [],
        "test_results": {},
        "logs": [],
        "final_project_path": "",
        "current_stage": "",
        "completed_nodes": [],
        "failed_nodes": [],
        "retry_count": 0,
        "next_agent": "",
        "supervisor_rounds": 0,
        "deployment_status": "",
        "testing_report": "",
        "consecutive_agent_failures": 0,
        "stage_attempts": {},
        "stage_feedback": {},
        "stage_progress": {},
        "task_status": {},
        "previous_schema": "",
        "previous_backend_files": {},
        "previous_openapi_for_backend": "",
        "previous_frontend_files": {},
        # For a fresh build, Planner sets this from the execution plan on its
        # first move - {} here is fine, it runs before the Supervisor ever
        # looks at it. For update mode, Planner is SKIPPED entirely (the
        # graph enters straight at Supervisor), so if this stayed {} the
        # Supervisor would have zero idea a previous build already exists -
        # initialize_update_state fills this in from the loaded snapshot.
        "stage_status": stage_status or {},
    }


def initialize_state(user_request: str) -> ProjectState:
    """Initialize the ProjectState for a fresh build with empty/default values."""

    return {
        "user_request": user_request,
        "project": {
            "project_id": "",
            "requirements": "",
            "architecture": "",
            "tasks": [],
            "acceptance_criteria": [],
            "workspace": {
                "database": {
                    "version": 0,
                    "schema": "",
                    "status": BuildStatus.PENDING.value
                },
                "contract": {
                    "version": 0,
                    "openapi_spec": "",
                    "status": BuildStatus.PENDING.value
                },
                "backend": {
                    "version": 0,
                    "files": {},
                    "status": BuildStatus.PENDING.value
                },
                "frontend": {
                    "version": 0,
                    "files": {},
                    "status": BuildStatus.PENDING.value
                },
                "docs": {},
                "release": {
                    "version": 0,
                    "package_manifest": {},
                    "status": BuildStatus.PENDING.value
                }
            }
        },
        "runtime": _base_runtime({
            "database": False,
            "contract": False,
            "backend": False,
            "frontend": False,
            "release": False
        }, mode="build")
    }


def initialize_update_state(project_id: str, change_request: str) -> ProjectState:
    """
    Initialize the ProjectState for revising an existing project: reloads its
    saved workspace (all previously generated files) so the Supervisor loop
    can patch it, instead of starting from scratch. Since update mode enters
    the graph directly at the Supervisor (route_entry skips Planner, the node
    that normally sets stage_status), stage_status must be reconstructed here
    from what's actually present in the saved snapshot.
    """

    snapshot = load_project_snapshot(project_id)
    if snapshot is None:
        raise ValueError(
            f"No saved project found for id '{project_id}'. "
            f"Run with --list-projects to see available projects."
        )

    metadata = snapshot["metadata"]
    workspace = snapshot["workspace"]

    saved_plan = metadata.get("execution_plan", {
        "database": bool(workspace.get("database", {}).get("schema")),
        "contract": bool(workspace.get("contract", {}).get("openapi_spec")),
        "backend": bool(workspace.get("backend", {}).get("files")),
        "frontend": bool(workspace.get("frontend", {}).get("files")),
        "release": True
    })

    # --update skips the full Planner node (no need to regenerate
    # requirements/tasks/architecture from scratch every time - the saved
    # ones are still valid) - but the saved execution_plan can be stale in
    # one specific way: if this new change_request needs a stage the
    # ORIGINAL build never did (e.g. the first build had no database, but
    # this update asks to add one), that stage would stay permanently
    # unreachable forever, since the Supervisor's routing is gated entirely
    # by execution_plan and nothing else ever revisits it on --update. Fix:
    # re-run just the lightweight plan-classification skill against the new
    # request and OR it into the saved plan - only ever ADDS a newly-needed
    # stage, never removes one already established by an earlier build.
    from skills.planning_skills import analyze_request_skill
    fresh_plan = analyze_request_skill(change_request)
    execution_plan = {key: saved_plan.get(key, False) or fresh_plan.get(key, False)
                      for key in ("database", "contract", "backend", "frontend", "release")}
    newly_added = [k for k in execution_plan if execution_plan[k] and not saved_plan.get(k, False)]
    if newly_added:
        get_logger().info(f"This change request needs stage(s) the original build didn't: {newly_added} "
                          f"- adding to the plan")

    saved_stage_status = metadata.get("stage_status") or {}
    # A stage that just got newly added to the plan has no prior status to
    # trust - force it to "pending" so _resume_status (below) doesn't fall
    # through to treating it as "skipped" or guessing from file presence.
    # newly_added uses execution_plan's own flag names (database/contract/
    # backend/frontend/release), which don't map 1:1 onto stage_status's
    # keys (database+contract both gate the single "database" stage,
    # release gates "deployment") - map explicitly rather than assume.
    _plan_flag_to_stage_status_key = {
        "database": "database", "contract": "database",
        "backend": "backend", "frontend": "frontend", "release": "deployment",
    }
    for flag in newly_added:
        saved_stage_status.pop(_plan_flag_to_stage_status_key[flag], None)

    # Bug fix: "newly_added" only covers a stage the ORIGINAL build never
    # needed at all. But every non-trivial --update also asks for more
    # backend/frontend/database work on a stage that was already part of
    # the plan from day one (e.g. "add a page", "add a column") - and
    # since that stage's saved_stage_status is already "done" from a PRIOR
    # run, _resume_status below just returns "done" again unchanged, so the
    # Supervisor never re-dispatches it and the update silently no-ops
    # straight to testing/deployment on unmodified code. Confirmed exactly
    # this happening: a large multi-page/schema update request came back
    # "PASSED" with zero of the requested files ever touched, because
    # backend/frontend were already "done" from the original build and
    # weren't "newly added" to the plan (they were already True in it).
    # Fix: ANY stage fresh_plan says is needed for THIS SPECIFIC request
    # gets forced to pending, not just ones new to the plan overall -
    # fresh_plan is already the LLM's classification of what stages this
    # change request actually touches, so it's the right signal to use.
    # Must be set to "pending" explicitly, not just popped: popping alone
    # doesn't work here because _resume_status's fallback for a missing
    # saved status is `"done" if has_files else "pending"` - and
    # has_backend/has_frontend are already True from the original build's
    # files, so a pop still silently resolved back to "done" (confirmed by
    # this exact bug still reproducing with only the pop in place).
    # "contract" is deliberately excluded here even though it's in
    # _plan_flag_to_stage_status_key (mapped to "database" for newly_added's
    # purposes) - the classifier's own rule makes contract=True almost
    # unconditionally ("contract: true if database OR backend OR frontend is
    # true"), so treating it as a signal here forced the database stage back
    # to pending on EVERY backend/frontend-only request, including a plain
    # import-name fix with zero schema involvement (confirmed reproducing:
    # stage_status showed database:pending for a request that only touched
    # frontend/src/pages/UserDetail.jsx). Only the specific "database" flag
    # is a real signal that the schema itself needs work.
    for flag, needed_now in fresh_plan.items():
        if flag == "contract":
            continue
        if needed_now and flag in _plan_flag_to_stage_status_key:
            saved_stage_status[_plan_flag_to_stage_status_key[flag]] = "pending"

    has_database = bool(workspace.get("database", {}).get("schema") or
                         workspace.get("contract", {}).get("openapi_spec"))
    has_backend = bool(workspace.get("backend", {}).get("files"))
    has_frontend = bool(workspace.get("frontend", {}).get("files"))

    def _resume_status(stage: str, needed: bool, has_files: bool) -> str:
        # A stage the plan doesn't need is genuinely skipped, full stop.
        if not needed:
            return "skipped"
        # Prefer the exact status the last run actually ended with (done vs.
        # failed vs. never-started) - it's more precise than guessing from
        # file presence alone (a rate limit can leave a stage with SOME
        # files but far from finished). Only fall back to the file-presence
        # heuristic for older snapshots saved before stage_status existed -
        # and there, a needed stage with zero files is "pending" (still
        # needs building), never "skipped" (the Supervisor's own routing
        # rule treats "skipped" as "never send work here").
        saved = saved_stage_status.get(stage)
        if saved in ("done", "failed", "pending"):
            return saved
        return "done" if has_files else "pending"

    stage_status = {
        "database": _resume_status("database", bool(execution_plan.get("database") or execution_plan.get("contract")), has_database),
        "backend": _resume_status("backend", bool(execution_plan.get("backend")), has_backend),
        "frontend": _resume_status("frontend", bool(execution_plan.get("frontend")), has_frontend),
        # Always re-verify after a change, regardless of prior state.
        "testing": "pending" if (has_backend or has_frontend) else "skipped",
        "deployment": "pending" if execution_plan.get("release", True) else "skipped",
    }

    return {
        "user_request": change_request,
        "project": {
            "project_id": project_id,
            "requirements": metadata.get("requirements", ""),
            "architecture": metadata.get("architecture", ""),
            "tasks": metadata.get("tasks", []),
            "acceptance_criteria": metadata.get("acceptance_criteria", []),
            "workspace": workspace
        },
        "runtime": _base_runtime(execution_plan, mode="update", stage_status=stage_status)
    }


def print_state_summary(state: ProjectState):
    """Print a human-readable summary of the final state."""

    logger = get_logger()

    if not state:
        logger.warning("No final state available")
        return

    logger.header("📊 EXECUTION SUMMARY")

    print(f"\n📝 User Request: {state.get('user_request', 'N/A')}")

    runtime = state.get("runtime", {})

    print(f"\n🗺️  Execution Plan:")
    for key, value in runtime.get("execution_plan", {}).items():
        print(f"   {'✅' if value else '⏭️ '} {key}")

    print(f"\n🧩 Completed Nodes: {', '.join(runtime.get('completed_nodes', [])) or 'none'}")

    if runtime.get("failed_nodes"):
        print(f"❌ Failed Nodes: {', '.join(runtime['failed_nodes'])}")

    quality_passed = runtime.get("quality_passed")
    print(f"\n{'✅' if quality_passed else '❌'} Quality Status: {'PASSED' if quality_passed else 'FAILED'}")

    if runtime.get("review_issues"):
        print(f"\n⚠️  Outstanding Issues: {len(runtime['review_issues'])}")
        for issue in runtime["review_issues"][:3]:
            print(f"   - [{issue['severity']}] {issue['file']}: {issue['description'][:60]}...")

    if runtime.get("final_project_path"):
        print(f"\n📂 Project Output: {runtime['final_project_path']}")

    logs = runtime.get("logs", [])
    if logs:
        print("\n📜 Execution Logs (most recent):")
        for log in logs[-10:]:
            print(f"   {log}")

    print("\n" + "=" * 80)


def _salvage_if_needed(final_state) -> None:
    """
    Agents write directly to disk as they go now (not just at a final
    packaging step), so ANY run that produced real files - whether it
    crashed, hit the supervisor's round backstop, or stalled on rate limits
    before ever reaching Deployment (the only node that otherwise saves a
    snapshot) - should still end up in the project registry, so a later
    `--update <project_id>` can pick up where it left off. Called after
    every run (success or failure); a no-op if Deployment already saved a
    snapshot (final_project_path is set) or nothing was actually generated.
    """
    if not final_state:
        return

    if final_state.get("runtime", {}).get("final_project_path"):
        return  # deployment already snapshotted this run - nothing to salvage

    project_id = final_state.get("project", {}).get("project_id")
    if not project_id:
        return  # crashed before Planner even assigned a project_id

    from skills.project_registry import project_dir_for, sync_workspace_from_disk, save_project_snapshot

    project_dir = project_dir_for(project_id)
    if not project_dir.exists():
        return

    workspace = sync_workspace_from_disk(project_dir, final_state.get("project", {}).get("workspace", {}))
    has_content = (
        any(workspace.get(k, {}).get("files") for k in ("backend", "frontend"))
        or workspace.get("database", {}).get("schema")
        or workspace.get("contract", {}).get("openapi_spec")
    )
    if not has_content:
        return

    logger = get_logger()
    logger.step("💾", "Registering generated files so far...")
    try:
        save_project_snapshot(
            project_dir=project_dir,
            project_id=project_id,
            user_request=final_state.get("user_request", ""),
            execution_plan=final_state.get("runtime", {}).get("execution_plan", {}),
            requirements=final_state.get("project", {}).get("requirements", ""),
            architecture=final_state.get("project", {}).get("architecture", ""),
            acceptance_criteria=final_state.get("project", {}).get("acceptance_criteria", []),
            tasks=final_state.get("project", {}).get("tasks", []),
            workspace=workspace,
            stage_status=final_state.get("runtime", {}).get("stage_status", {}),
        )
        logger.success(f"Registered project at {project_dir}", indent=1)
        logger.info(f"Resume later with: python main.py --update {project_id} \"<change request>\"", indent=1)
    except Exception as salvage_error:
        logger.error(f"Could not register project: {salvage_error}", indent=1)


def run_pipeline(user_request: str, thread_id: str = "default",
                 project_id: str = None, update: bool = False):
    """
    Run the pipeline for a user request - either a fresh build, or an update
    to a previously built project (update=True, project_id required).

    Args:
        user_request: The user's request string (fresh build) or change
                      request (update mode)
        thread_id: Thread ID for checkpointing (allows resume)
        project_id: Existing project id to update (required when update=True)
        update: Whether this is an update to an existing project

    Returns:
        Final ProjectState
    """

    logger = get_logger()
    logger.start()
    logger.start_run(project_id=project_id or "", mode="update" if update else "build")

    # Check for API key - any one configured credential is enough to start;
    # model_router's fallback chains handle the rest at call time.
    from core.model_config import configured_credentials
    if not configured_credentials() and not os.getenv("ANTHROPIC_API_KEY"):
        logger.header("❌ ERROR: No API Keys Found")
        print("\nPlease set at least one of these in .env:")
        print("  - GROQ_API_KEY_A1 & MISTRAL_API_KEY_A1 (Account 1)")
        print("  - Any other GROQ_API_KEY_A*/MISTRAL_API_KEY_A* (Accounts 2-4)")
        print("  - ANTHROPIC_API_KEY (Fallback)")
        logger.separator()
        sys.exit(1)

    if update:
        logger.header("🚀 LANGGRAPH PIPELINE - UPDATE PROJECT")
        print(f"📦 Project: {project_id}")
        print(f"📝 Change Request: {user_request}")
        initial_state = initialize_update_state(project_id, user_request)
        # Each --update invocation must be a genuinely fresh graph run, not a
        # continuation of whatever thread this project_id used last time.
        # completed_nodes/failed_nodes/issue_history/logs all use additive
        # reducers (operator.add) specifically because they're meant to
        # start EMPTY at the top of every invocation (initialize_update_state
        # builds a fresh RuntimeState) - that was true under MemorySaver
        # (wiped every process exit) but broke the moment the checkpointer
        # became disk-persistent (see core/graph.py): reusing thread_id=
        # project_id across separate --update calls let LangGraph merge this
        # run's fresh state into the PREVIOUS run's leftover checkpoint under
        # that same thread, so those "this run only" fields kept accumulating
        # across unrelated invocations instead of resetting (confirmed live -
        # a stage marked done by a run 2 invocations ago was still showing up
        # in "already addressed this run" on a brand new process). The
        # project-registry snapshot (.project.json), not this thread, is what
        # --update actually resumes from, so a unique thread per invocation
        # loses nothing - it still gets full per-node crash durability
        # WITHIN this one invocation, just doesn't blur into the next one.
        import time
        thread_id = f"{project_id}-update-{int(time.time() * 1000)}"
    else:
        logger.header("🚀 LANGGRAPH MULTI-CAPABILITY PIPELINE")
        print(f"📝 Request: {user_request}")
        initial_state = initialize_state(user_request)
        # A fresh build must get its own unique thread every invocation, not
        # a shared static "default" - now that the checkpointer is disk-
        # persistent (see core/graph.py), two different fresh builds sharing
        # one thread_id would have their checkpoint histories collide/merge
        # with each other, which never mattered when MemorySaver wiped
        # everything on process exit anyway. A deliberate RE-run of the exact
        # same request is handled via --update once a project_id exists, not
        # by silently reusing this thread.
        import time
        thread_id = f"build-{int(time.time() * 1000)}"

    print(f"🔖 Thread: {thread_id}")
    logger.separator()

    # Configure for graph execution
    config = {"configurable": {"thread_id": thread_id}}
    
    # Run the graph
    final_state = None
    step_count = 0

    try:
        prev_stage_status = None
        for state in app.stream(initial_state, config):
            step_count += 1
            
            # Display status after each node execution if status changed
            current_state = app.get_state(config).values
            if current_state:
                stage_status = current_state.get("runtime", {}).get("stage_status", {})
                current_stage = current_state.get("runtime", {}).get("current_stage", "")
                
                # Only display if status actually changed
                if stage_status != prev_stage_status:
                    StageTracker.display_status(stage_status, current_stage)
                    prev_stage_status = stage_status.copy()

        # app.stream() in the default "updates" mode yields only each node's
        # own partial return per step, not the accumulated state - pull the
        # real final state (after reducers merged every node's writes) from
        # the checkpointer instead.
        final_state = app.get_state(config).values

        final_stage_status = {}
        # Display final status
        if final_state:
            final_stage_status = final_state.get("runtime", {}).get("stage_status", {})
            logger.info("\n" + StageTracker.get_progress_summary(final_stage_status))

        logger.summary(step_count)
        if not logger.project_id:
            logger.project_id = (final_state or {}).get("project", {}).get("project_id", "")
        logger.pipeline_completed(final_stage_status)

        # Register the project even on a "successful" run that never actually
        # reached Deployment (e.g. the supervisor hit its round backstop after
        # rate limits stalled every agent) - otherwise real files sit on disk
        # under a project_id that --update/--list-projects can't see.
        # No-ops if Deployment already saved a snapshot for this run.
        _salvage_if_needed(final_state)

    except Exception as e:
        logger.header("❌ PIPELINE FAILED")
        logger.error(str(e))
        logger.separator()
        import traceback
        traceback.print_exc()
        final_state = app.get_state(config).values
        if not logger.project_id:
            logger.project_id = (final_state or {}).get("project", {}).get("project_id", "")
        logger.pipeline_failed(str(e), stage=(final_state or {}).get("runtime", {}).get("current_stage", ""),
                               stage_status=(final_state or {}).get("runtime", {}).get("stage_status", {}))
        _salvage_if_needed(final_state)
        if final_state:
            print("\n📊 Partial state before failure:")
            print_state_summary(final_state)
        sys.exit(1)
    
    # Print summary
    if final_state:
        print_state_summary(final_state)
    
    return final_state


def print_projects():
    """List previously built projects that can be targeted with --update."""
    projects = list_projects()
    if not projects:
        get_logger().info("No saved projects found in output/.")
        return

    print(f"\n📁 {len(projects)} saved project(s):\n")
    for p in projects:
        print(f"  📦 {p['project_id']}")
        print(f"     {p['user_request'][:80]}")
        print()


def run_simple_file_task(user_request: str):
    """
    Handle a lightweight request that just wants one or a few standalone
    files (e.g. "write me a .txt file explaining the API") - skips the
    full planning -> backend -> frontend -> quality pipeline entirely and
    generates the file(s) directly.
    """
    from skills.simple_task_skills import generate_simple_file_skill
    from skills.project_registry import slugify, save_project_snapshot
    from skills.release_skills import package_project_skill

    logger = get_logger()
    logger.header("📝 SIMPLE FILE TASK")
    print(f"📝 Request: {user_request}")

    files = generate_simple_file_skill(user_request)
    if not files:
        logger.error("Could not generate the requested file(s).")
        return None

    project_id = slugify(user_request)
    workspace = {"docs": files}
    project_path, manifest = package_project_skill(workspace, project_id)

    save_project_snapshot(
        project_dir=Path(project_path),
        project_id=project_id,
        user_request=user_request,
        execution_plan={"database": False, "contract": False, "backend": False,
                        "frontend": False, "release": False},
        requirements="",
        architecture="",
        tasks=[],
        workspace=workspace,
    )

    logger.success(f"Generated {len(files)} file(s) at {project_path}")
    for f in files:
        print(f"   📄 {f}")

    return {"project_id": project_id, "project_path": project_path, "files": files}


def dispatch_new_request(user_request: str):
    """
    Route a brand-new (non-update) request to whichever handler fits:
    a lightweight standalone file, or the full app-building pipeline.
    """
    from skills.simple_task_skills import classify_intent_skill

    intent = classify_intent_skill(user_request)
    if intent == "simple_file":
        return run_simple_file_task(user_request)

    return run_pipeline(user_request)


def _choose_project_interactively():
    """
    Show saved projects and ask whether this request is for a new project or
    an update to an existing one. Returns a project_id, or None for "new".
    """
    projects = list_projects()
    if not projects:
        return None

    print("\nIs this a new project, or an update to one of these?")
    print("  0. New project")
    for i, p in enumerate(projects, 1):
        print(f"  {i}. {p['project_id']} - {p['user_request'][:60]}")

    choice = input("Choose a number (default 0): ").strip()
    if not choice or choice == "0":
        return None

    try:
        idx = int(choice) - 1
    except ValueError:
        print("Not a number - assuming new project.")
        return None

    if 0 <= idx < len(projects):
        return projects[idx]["project_id"]

    print("Out of range - assuming new project.")
    return None


def interactive_loop():
    """
    Continuous prompt: type a request, it runs (new build or update to an
    existing project, chosen explicitly each time), then prompts again.
    """
    print("Interactive mode - type a request ('list' for saved projects, 'exit' to quit).\n")

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            return

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            print("Goodbye!")
            return
        if user_input.lower() in ("list", "projects", "--list-projects"):
            print_projects()
            continue

        try:
            project_id = _choose_project_interactively()
            if project_id:
                run_pipeline(user_input, project_id=project_id, update=True)
            else:
                dispatch_new_request(user_input)
        except SystemExit:
            # a failed run shouldn't kill the whole interactive session
            pass
        except Exception as e:
            get_logger().error(f"Unexpected error: {e}")


if __name__ == "__main__":
    args = sys.argv[1:]

    if args and args[0] in ("-h", "--help"):
        print("Usage:")
        print("  python main.py                              Interactive mode")
        print('  python main.py "<request>"                 Build a new project (no prompts)')
        print('  python main.py --update <project_id> "..."  Revise an existing project')
        print("  python main.py --list-projects              List saved projects")
        sys.exit(0)

    if not args:
        interactive_loop()
        sys.exit(0)

    if args[0] == "--list-projects":
        print_projects()
        sys.exit(0)

    if args[0] == "--update":
        if len(args) < 3:
            print('Usage: python main.py --update <project_id> "<change request>"')
            sys.exit(1)
        project_id = args[1]
        change_request = " ".join(args[2:])
        run_pipeline(change_request, project_id=project_id, update=True)
    else:
        user_request = " ".join(args)
        dispatch_new_request(user_request)
