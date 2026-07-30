"""
End-to-End Testing Agent - E2E_RUN / E2E_UT / E2E_VAL as three real
LangGraph nodes (see core/graph.py for the edges), matching the documented
architecture diagram. Replaces the old capabilities/testing.py (renamed -
same underlying checks, split into 3 nodes instead of one).

E2E_RUN does the actual verification work (static checks, LLM code review,
and - the diagram's "boots full stack together" - a real `docker compose up`
+ reachability check). E2E_UT/E2E_VAL are deliberately thin: they just read
what RUN already computed and decide pass/fail/retry, matching the diagram's
separate diamonds without re-running expensive checks twice.
"""

from core.state import ProjectState
from core.stage_loop import MAX_STAGE_ATTEMPTS, checkpoint, known_issue_files_context
from skills.project_registry import project_dir_for, sync_workspace_from_disk
from skills.agent_tools import make_agent_tools
from skills.text_utils import cap_report
from core.agent_runtime import run_tool_agent
from core.logger import get_logger

STAGE = "testing"  # kept as the logical stage_status/review_issues key used
                   # throughout the rest of the pipeline (supervisor.py's
                   # prompt, backend.py/frontend.py's context) - only the
                   # graph node names (e2e_run/e2e_ut/e2e_val) changed to
                   # match the diagram; the logical stage vocabulary didn't.

MAX_REVIEW_REPEATS = 3  # same reasoning as the old testing.py: a review_code
                       # finding that keeps getting re-flagged in the same
                       # file this many times without resolving is a subjective
                       # nitpick the model can't converge on, not a real defect.

E2E_SYSTEM_PROMPT = """You are the end-to-end testing agent. You verify - you do not write or fix code
yourself (you have no write_file tool on purpose; report problems so the supervisor can send them to
the agent that owns that file). Minimize tool calls - call run_static_checks and review_code ONCE each,
then read_file ONLY files that have specific errors. Don't list_files or read files without errors.

The required stack for every project this pipeline builds (not a guess, not per-project) is: Postgres
database, Python + FastAPI + SQLAlchemy backend, React + Vite frontend, API paths matching openapi.yaml
exactly (whatever prefix it uses, or lack of one). Flag a genuine deviation from this (e.g. a different
backend language/framework, a different ORM) as a real issue - but don't invent a "wrong framework"
complaint against files that already match it; verify against what's actually on disk, not assumptions.

You have three ways to actually verify the code - use them, don't guess:
1. run_static_checks - parses every backend/frontend file for syntax errors, undefined names, and a
   hardcoded-API-URL check (the frontend must read the backend's address from an env var, not a
   hardcoded port, or the two halves of the app can't actually talk to each other). Always call this
   first.
2. review_code - a second LLM's opinion catching things static analysis can't (missing error
   handling, security issues, logic bugs, requirements the code doesn't actually satisfy). Pass it
   the required tasks summary given below so it can check for missing functionality too.
3. run_command("pytest") - if backend/ has a requirements.txt listing pytest and a test_main.py,
   try running it for real - only if you judge it likely to work without installing anything first
   (don't try to pip install).

Any file a PREVIOUS round's review/static-check pass already flagged is inlined directly below your
context (if any) - do NOT read_file those again, the content shown IS current. Only read_file a file
that's genuinely not already shown to you (e.g. one run_static_checks/review_code just flagged for the
first time this call). run_static_checks and review_code's own output already states what's wrong -
that description, plus what's already inlined below, is normally enough; don't re-read a file just to
restate what the tool already told you. Respond with a clear PASS or FAIL summary listing every real
problem found (file + description) so the supervisor and other agents know exactly what to fix - don't
say "looks fine" without having actually run the checks.

After your own report, a separate deterministic step automatically boots the full stack for real
(docker compose up) and checks it's actually reachable - you do NOT need to attempt this yourself via
run_command; that would just be redundant with what already happens next."""


def _review_findings(project_dir, workspace, tasks) -> tuple:
    """Structured, severity-tagged review_code findings, direct from the
    skill (deterministic, cache-backed) - not parsed out of an agent's
    free-text report. Returns (blocking_issues, low_severity_issues)."""
    from skills.quality_skills import review_code_skill
    from skills.review_cache import (
        load_review_cache, save_review_cache, split_changed_files, rebuild_cache
    )

    all_files = {}
    for artifact_type in ("backend", "frontend"):
        for path, content in workspace.get(artifact_type, {}).get("files", {}).items():
            all_files[f"{artifact_type}/{path}"] = content

    if not all_files:
        return [], []

    cache = load_review_cache(project_dir)
    changed_files, current_hashes, cached_issues = split_changed_files(all_files, cache)
    new_issues_by_file = {}
    if changed_files:
        new_issues = review_code_skill(changed_files, tasks)
        for issue in new_issues:
            new_issues_by_file.setdefault(issue.get("file", ""), []).append(issue)
    updated_cache = rebuild_cache(all_files, current_hashes, cache, set(changed_files), new_issues_by_file)
    save_review_cache(project_dir, updated_cache)
    review_findings = cached_issues + [i for issues in new_issues_by_file.values() for i in issues]

    return (
        [i for i in review_findings if str(i.get("severity", "medium")).lower() == "high"],
        [i for i in review_findings if str(i.get("severity", "medium")).lower() != "high"],
    )


class E2ECapability:
    def run(self, state: ProjectState) -> dict:
        """E2E_RUN: static checks + LLM code review + boots the full stack
        together for real (docker compose up + reachability poll)."""
        logger = get_logger()
        logger.node_start("e2e_run")
        checkpoint(state)

        project_dir = project_dir_for(state["project"]["project_id"])
        tools = make_agent_tools(project_dir, write_prefix="", allow_commands=True,
                                 include_static_checks=True, include_write=False)
        logger.info(f"Tools granted: {[t.name for t in tools]} (no write access)")

        tasks = state["project"].get("tasks", [])

        # Pre-sync so both the pre-built context below AND the post-agent
        # analysis further down use the same up-to-date file content -
        # previously this sync only happened AFTER the agent ran, so the
        # agent had no choice but to read_file every flagged file itself
        # every round, even ones whose content hadn't changed since the last
        # E2E pass.
        workspace = sync_workspace_from_disk(project_dir, state["project"]["workspace"])
        prior_issues = state["runtime"].get("review_issues", [])
        known_files = {}
        for issue in prior_issues:
            f = issue.get("file", "")
            if "/" not in f:
                continue
            artifact, rel_path = f.split("/", 1)
            content = workspace.get(artifact, {}).get("files", {}).get(rel_path)
            if content is not None:
                known_files[f] = content
        files_context = known_issue_files_context(known_files)

        context = f"""Verify the backend and frontend that have been built so far.

Required tasks (pass this to review_code): {tasks}{files_context}"""

        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0) + 1
        logger.info(f"E2E_RUN attempt {attempts}/{MAX_STAGE_ATTEMPTS}")

        summary, ok = run_tool_agent("testing_agent", E2E_SYSTEM_PROMPT, context, tools)
        logger.info(f"E2E agent report: {summary[:300]}")

        from skills.quality_skills import run_tests_skill
        workspace = sync_workspace_from_disk(project_dir, workspace)
        test_results = run_tests_skill(workspace)
        logger.info(f"Static checks: {test_results['passed']}/{test_results['total']} passed, "
                   f"{test_results['failed']} failed")

        candidate_review_issues, low_severity_issues = _review_findings(project_dir, workspace, tasks)

        # Recurrence check: a review_code finding re-flagged in the same
        # file/severity across MAX_REVIEW_REPEATS E2E passes this run,
        # despite Backend/Frontend already getting that many self-heal shots
        # at it, stops blocking - it's a sign the model can't converge on a
        # subjective nitpick, not that the pipeline is broken.
        history = state["runtime"].get("issue_history", [])
        repeat_counts = {}
        for past_issue in history:
            if past_issue.get("source") != "review_code":
                continue
            key = (past_issue.get("file"), str(past_issue.get("severity", "medium")).lower())
            repeat_counts[key] = repeat_counts.get(key, 0) + 1

        blocking_review_issues = []
        for issue in candidate_review_issues:
            key = (issue.get("file"), str(issue.get("severity", "medium")).lower())
            if repeat_counts.get(key, 0) >= MAX_REVIEW_REPEATS:
                logger.info(f"review_code issue in {issue.get('file')} ({issue.get('severity')}) has recurred "
                           f"{repeat_counts[key]}+ times this run without resolving - no longer blocking")
                low_severity_issues.append(issue)
            else:
                blocking_review_issues.append({**issue, "source": "review_code"})

        static_clean = test_results["failed"] == 0
        no_blocking_review = not blocking_review_issues

        # Boots the full stack for real (only if static+review already look
        # clean - no point booting a stack we already know is broken).
        e2e_boot = None
        e2e_report = ""
        plan = state["runtime"]["execution_plan"]
        has_backend = bool(workspace.get("backend", {}).get("files"))
        has_frontend = bool(workspace.get("frontend", {}).get("files"))
        if ok and static_clean and no_blocking_review and (plan.get("backend") or plan.get("frontend")) \
                and (has_backend or has_frontend):
            import shutil
            import subprocess as _subprocess

            docker_available = shutil.which("docker") is not None
            if docker_available:
                try:
                    docker_info = _subprocess.run(
                        ["docker", "info"], capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=15,
                    )
                    docker_available = docker_info.returncode == 0
                except (_subprocess.TimeoutExpired, OSError):
                    docker_available = False

            if docker_available:
                from skills.docker_skills import write_docker_assets
                from skills.project_registry import allocate_ports
                from skills.e2e_skills import run_e2e_skill

                logger.info("Generating Docker assets for E2E boot check...")
                write_docker_assets(project_dir, workspace)
                host_ports = allocate_ports(state["project"]["project_id"])
                logger.info("Booting full stack for E2E verification (docker compose up --build -d)...")
                e2e_boot = run_e2e_skill(project_dir, host_ports, has_backend, has_frontend)

                check_lines = "\n".join(
                    f"- {c['name']}: {'OK' if c['ok'] else 'FAIL'} ({c['detail']})" for c in e2e_boot["checks"]
                )
                e2e_report = f"\n\nE2E boot check: {'PASS' if e2e_boot['passed'] else 'FAIL'}\n{check_lines}"
                logger.success("E2E check: stack booted and reachable") if e2e_boot["passed"] \
                    else logger.warning(f"E2E check FAILED:\n{check_lines}")
            else:
                e2e_report = "\n\nE2E boot check: skipped (Docker not available)"
                logger.info("Docker not available - skipping E2E boot check")

        summary += e2e_report
        capped_summary = cap_report(summary)

        prior_failures = state["runtime"].get("consecutive_agent_failures", 0)
        consecutive_failures = 0 if ok else prior_failures + 1

        issues = [
            {"severity": "high", "file": f["file"], "description": f["error"],
             "suggested_fix": "Fix the reported error.", "source": "static_check"}
            for f in test_results.get("failures", [])
        ] + blocking_review_issues

        logger.node_complete("e2e_run")
        return {
            "project": {"workspace": workspace},
            "runtime": {
                "stage_status": {"testing": "validating"},
                "test_results": {**test_results, "e2e_boot": e2e_boot, "ok": ok},
                "review_issues": issues,
                "issue_history": issues,
                "testing_report": capped_summary,
                "stage_attempts": {STAGE: attempts},
                "current_stage": "e2e_run",
                "completed_nodes": ["e2e_run"],
                "consecutive_agent_failures": consecutive_failures,
                "logs": [f"E2E Agent: {summary[:300]}"]
            }
        }

    def check_ut(self, state: ProjectState) -> dict:
        """E2E_UT: stack starts clean? Static checks passed, and (if a boot
        was attempted) docker compose up itself succeeded."""
        logger = get_logger()
        logger.node_start("e2e_ut")
        checkpoint(state)

        test_results = state["runtime"].get("test_results", {})
        static_clean = test_results.get("failed", 1) == 0 and test_results.get("ok", False)

        e2e_boot = test_results.get("e2e_boot")
        boot_clean = True
        if e2e_boot is not None:
            up_check = next((c for c in e2e_boot["checks"] if c["name"] == "docker compose up"), None)
            boot_clean = up_check["ok"] if up_check else False

        passed = static_clean and boot_clean
        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0)
        if passed:
            logger.success("E2E_UT passed: stack starts clean")
        else:
            logger.warning(f"E2E_UT failed (attempt {attempts}/{MAX_STAGE_ATTEMPTS}): "
                          f"static_clean={static_clean}, boot_clean={boot_clean}")

        give_up = not passed and attempts >= MAX_STAGE_ATTEMPTS
        detail = "" if passed else f"stack did not start clean (static_clean={static_clean}, boot_clean={boot_clean})"
        logger.node_complete("e2e_ut")
        return {
            "runtime": {
                "stage_status": {"testing": "failed"} if give_up else {},
                "stage_feedback": {STAGE: detail},
                "quality_passed": False if give_up else None,
                "current_stage": "e2e_ut",
                "failed_nodes": ["e2e_ut"] if give_up else [],
                "logs": [f"E2E_UT: {'passed' if passed else 'failed'}"]
            }
        }

    def check_val(self, state: ProjectState) -> dict:
        """E2E_VAL: real user flows pass? Backend/frontend actually
        reachable over HTTP, and no blocking review_code issues left."""
        logger = get_logger()
        logger.node_start("e2e_val")
        checkpoint(state)

        test_results = state["runtime"].get("test_results", {})
        e2e_boot = test_results.get("e2e_boot")
        reachable = True
        if e2e_boot is not None:
            reachable = all(
                c["ok"] for c in e2e_boot["checks"] if c["name"] in ("backend reachable", "frontend reachable")
            )

        review_issues = state["runtime"].get("review_issues", [])
        no_blocking_review = not any(i.get("source") == "review_code" for i in review_issues)
        # Static-check-sourced issues (real syntax/undefined-name/etc bugs)
        # must also block VAL - only review_code's subjective findings get
        # the leniency above.
        no_static_failures = not any(i.get("source") == "static_check" for i in review_issues)

        passed = reachable and no_blocking_review and no_static_failures
        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0)
        if passed:
            logger.success("E2E_VAL passed: real user flows pass")
        else:
            logger.warning(f"E2E_VAL failed (attempt {attempts}/{MAX_STAGE_ATTEMPTS}): "
                          f"reachable={reachable}, outstanding_issues={len(review_issues)}")

        give_up = not passed and attempts >= MAX_STAGE_ATTEMPTS
        new_status = {"testing": "done"} if passed else ({"testing": "failed"} if give_up else {})
        detail = "" if passed else f"not reachable/outstanding issues (reachable={reachable}, issues={len(review_issues)})"

        logger.stage("testing", "done" if passed else ("failed" if give_up else "pending"))
        logger.node_complete("e2e_val")
        return {
            "runtime": {
                "stage_status": new_status,
                "stage_feedback": {STAGE: detail},
                "quality_passed": passed if (passed or give_up) else None,
                "current_stage": "e2e_val",
                "completed_nodes": ["e2e_val", "testing"] if passed else [],
                "failed_nodes": ["e2e_val"] if give_up else [],
                "logs": [f"E2E_VAL: {'passed' if passed else 'failed'}"]
            }
        }
