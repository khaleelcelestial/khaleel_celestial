"""
End-to-End Testing Agent - E2E_RUN / E2E_UT / E2E_VAL as three real
LangGraph nodes (see core/graph.py for the edges), matching the documented
architecture diagram.

REDESIGNED as a System Integration Validator (not a fourth copy of
Database/Backend/Frontend's own checks): DB_UT/VAL, BE_UT/VAL, and FE_UT/VAL
now each do real, deterministic, per-stage validation before anything ever
reaches here (real Postgres apply, real Docker boot+HTTP, real vite
build+ESLint+madge+Playwright). E2E's old static-check + LLM-review tool
calls (run_static_checks, review_code, an ad-hoc local pytest attempt) were
confirmed strictly redundant with that work and have been removed - see
Notes.md/session history for the before/after comparison. E2E's job is now
"does the COMPLETE system work together", not "is each piece individually
correct" (already proven upstream).

E2E_RUN is now lightweight - it only prepares the integration environment
(Docker assets + a real `docker compose up --build -d`) and does NOT tear
down afterward. E2E_UT and E2E_VAL then examine that SAME still-running
stack (unlike every other stage's self-contained boot-check-teardown-in-
one-call validators) since "can it start" (UT) and "does it work
correctly" (VAL) are different questions about the same live instance, not
two separate boots. teardown_stack() is called on any UT failure (nothing
more to check) and unconditionally at the end of VAL (pass or fail).
"""

from core.state import ProjectState
from core.stage_loop import MAX_STAGE_ATTEMPTS, checkpoint
from skills.project_registry import project_dir_for, sync_workspace_from_disk
from skills.text_utils import cap_report
from core.logger import get_logger

STAGE = "testing"  # kept as the logical stage_status/review_issues key used
                   # throughout the rest of the pipeline (supervisor.py's
                   # prompt, backend.py/frontend.py's context) - only the
                   # graph node names (e2e_run/e2e_ut/e2e_val) changed to
                   # match the diagram; the logical stage vocabulary didn't.


def _problems_to_review_issues(problems: list[str]) -> list[dict]:
    """Every finding skills/e2e_validators.py produces is tagged
    '[stage] message' - this turns that back into the SAME structured
    Issue shape Backend/Frontend/Database already populate, with a "file"
    value ('backend/e2e_integration' etc.) that capabilities/supervisor.py's
    own stage-matching logic (`f"{stage}/" in issue["file"]`) can actually
    match against. Without this, E2E finding a real integration bug had NO
    way to tell the Supervisor WHICH agent should fix it - confirmed as a
    real gap in this rewrite before this fix, caught by direct verification
    rather than assumption."""
    from skills.e2e_validators import parse_tag
    issues = []
    for p in problems:
        stage, message = parse_tag(p)
        issues.append({
            "severity": "high", "file": f"{stage}/e2e_integration",
            "description": message[:500],
            "suggested_fix": "See the E2E system integration finding for details.",
            "source": "e2e_integration",
        })
    return issues


class E2ECapability:
    def run(self, state: ProjectState) -> dict:
        """E2E_RUN: prepares the integration environment - Docker assets +
        a real `docker compose up --build -d`. No structural validation,
        no LLM call - that's Database/Backend/Frontend's own job, already
        done before this stage is ever reached."""
        logger = get_logger()
        logger.node_start("e2e_run")
        checkpoint(state)

        project_dir = project_dir_for(state["project"]["project_id"])
        workspace = sync_workspace_from_disk(project_dir, state["project"]["workspace"])

        plan = state["runtime"]["execution_plan"]
        has_backend = bool(workspace.get("backend", {}).get("files"))
        has_frontend = bool(workspace.get("frontend", {}).get("files"))
        has_database = bool(workspace.get("database", {}).get("schema"))

        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0) + 1
        logger.info(f"E2E_RUN attempt {attempts}/{MAX_STAGE_ATTEMPTS}")
        logger.stage_started(STAGE, attempts)

        e2e_boot = None
        if (plan.get("backend") or plan.get("frontend")) and (has_backend or has_frontend):
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
                from skills.e2e_skills import boot_stack

                import time as _time

                logger.info("Preparing Docker assets for system integration testing...")
                write_docker_assets(project_dir, workspace)
                host_ports = allocate_ports(state["project"]["project_id"])
                logger.info("Booting complete stack (docker compose up --build -d)...")
                boot_started_at = _time.time()
                e2e_boot = boot_stack(project_dir, host_ports, has_backend, has_frontend)
                e2e_boot["boot_elapsed_s"] = _time.time() - boot_started_at
                e2e_boot["host_ports"] = host_ports
                e2e_boot["has_backend"] = has_backend
                e2e_boot["has_frontend"] = has_frontend
                e2e_boot["has_database"] = has_database
                logger.success("Stack booted - staying up for E2E_UT/E2E_VAL to examine") if e2e_boot["up_ok"] \
                    else logger.warning(f"Stack failed to boot: {e2e_boot['up_detail']}")
            else:
                logger.info("Docker not available - skipping integration boot")

        logger.node_complete("e2e_run")
        return {
            "project": {"workspace": workspace},
            "runtime": {
                "stage_status": {"testing": "validating"},
                "test_results": {"e2e_boot": e2e_boot},
                "stage_attempts": {STAGE: attempts},
                "current_stage": "e2e_run",
                "completed_nodes": ["e2e_run"],
                "logs": [f"E2E_RUN: boot {'OK' if e2e_boot and e2e_boot['up_ok'] else 'skipped/failed'}"]
            }
        }

    def check_ut(self, state: ProjectState) -> dict:
        """E2E_UT: can the COMPLETE application stack start successfully?
        Fully deterministic - containers running, backend/frontend
        reachable, no crash loops, no fatal exceptions in logs. Tears the
        stack down on failure (nothing more to check); leaves it running
        on success for E2E_VAL to examine."""
        logger = get_logger()
        logger.node_start("e2e_ut")
        checkpoint(state)

        project_dir = project_dir_for(state["project"]["project_id"])
        e2e_boot = state["runtime"].get("test_results", {}).get("e2e_boot")

        if e2e_boot is None:
            # No backend/frontend to boot at all, or Docker unavailable -
            # nothing for this stage to verify; not a failure.
            passed, problems = True, []
        else:
            from skills.e2e_validators import run_e2e_ut_validators
            problems = run_e2e_ut_validators(project_dir, e2e_boot)
            passed = not problems

        if not passed and e2e_boot is not None:
            from skills.e2e_skills import teardown_stack
            teardown_stack(project_dir)

        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0)
        if passed:
            logger.success("E2E_UT passed: complete stack starts successfully")
        else:
            logger.warning(f"E2E_UT failed (attempt {attempts}/{MAX_STAGE_ATTEMPTS}): {len(problems)} problem(s)")
            for p in problems[:3]:
                logger.info(f"   - {p[:200]}")

        give_up = not passed and attempts >= MAX_STAGE_ATTEMPTS
        logger.node_complete("e2e_ut")
        return {
            "runtime": {
                "stage_status": {"testing": "failed"} if give_up else {},
                "stage_feedback": {STAGE: "" if passed else "\n".join(f"- {p}" for p in problems)},
                "quality_passed": False if give_up else None,
                # So the Supervisor (and Backend/Frontend's own RUN prompts,
                # which read testing_report directly) actually learn WHAT
                # broke and WHICH stage to route to - see
                # _problems_to_review_issues' docstring for why this wasn't
                # here before.
                "review_issues": _problems_to_review_issues(problems),
                "testing_report": "" if passed else cap_report(
                    "E2E_UT (can the complete stack start?) failed:\n" + "\n".join(f"- {p}" for p in problems)
                ),
                "current_stage": "e2e_ut",
                "failed_nodes": ["e2e_ut"] if give_up else [],
                "logs": [f"E2E_UT: {'passed' if passed else f'{len(problems)} problem(s)'}"]
            }
        }

    def check_val(self, state: ProjectState) -> dict:
        """E2E_VAL: does the COMPLETE system work correctly as one
        integrated whole? Runs the deterministic integration/security/
        performance validators (Phase 2) against the SAME still-live
        stack E2E_UT just proved boots cleanly, then tears it down
        unconditionally (pass or fail - this is a temporary verification
        boot, not the real deployment). Phases 3-4 add the Acceptance
        Test Runner and the one holistic LLM review here."""
        logger = get_logger()
        logger.node_start("e2e_val")
        checkpoint(state)

        project_dir = project_dir_for(state["project"]["project_id"])
        project_id = state["project"]["project_id"]
        workspace = sync_workspace_from_disk(project_dir, state["project"]["workspace"])
        e2e_boot = state["runtime"].get("test_results", {}).get("e2e_boot")

        # Defaults to the PREVIOUS round's stored outcomes, not {} - if the
        # acceptance suite is skipped this round (e.g. Phase 2 already
        # failed, so it never got the chance to run), the regression
        # baseline for the NEXT round must not be wiped out by an empty
        # result that doesn't mean "everything failed", it means "never ran".
        current_outcomes = state["runtime"].get("test_results", {}).get("e2e_acceptance_outcomes", {})
        if e2e_boot is not None:
            from skills.e2e_validators import run_e2e_val_validators
            problems = run_e2e_val_validators(project_dir, project_id, workspace, e2e_boot)

            # Acceptance Test Runner (Phase 3) - gated behind the cheaper
            # Phase 2 validators already passing, same cost-gating already
            # used for the Docker-based validators elsewhere (no point
            # running a full Playwright user-flow suite against a system
            # already known to be broken by checks that are free). Per the
            # spec: this only EXECUTES Frontend's already-generated suite,
            # never generates one itself.
            if not problems and e2e_boot.get("has_frontend"):
                spec_path = project_dir / "test_frontend.spec.cjs"
                if spec_path.exists():
                    from skills.e2e_validators import run_acceptance_suite, AcceptanceRegressionValidator
                    frontend_url = f"http://localhost:{e2e_boot['host_ports']['frontend']}/"
                    previous_outcomes = current_outcomes
                    suite_problems, current_outcomes = run_acceptance_suite(
                        project_dir, spec_path.read_text(encoding="utf-8"), frontend_url,
                    )
                    logger.validator_result(STAGE, "val", "AcceptanceSuite",
                                           "FAIL" if suite_problems else "PASS", issue_count=len(suite_problems))
                    problems += suite_problems
                    regression_problems = AcceptanceRegressionValidator().validate(previous_outcomes, current_outcomes)
                    logger.validator_result(STAGE, "val", "AcceptanceRegressionValidator",
                                           "FAIL" if regression_problems else "PASS", issue_count=len(regression_problems))
                    problems += regression_problems
        else:
            problems = []

        if e2e_boot is not None:
            from skills.e2e_skills import teardown_stack
            teardown_stack(project_dir)

        # Holistic LLM Review (Phase 4) - the ONE subjective step, gated
        # behind every deterministic check above already passing (no point
        # spending a model call scrutinizing an app already known broken).
        # Verification only - review_holistic_review is never given a
        # write tool, so it structurally cannot modify code.
        holistic_findings = []
        if not problems:
            from skills.e2e_validators import run_holistic_review
            blocking, holistic_findings = run_holistic_review(
                project_dir, workspace,
                state["project"].get("architecture", ""),
                state["project"].get("tasks", []),
                state["project"].get("acceptance_criteria", []),
                state["runtime"].get("issue_history", []),
            )
            # NOT run through the "[stage] message" tag convention - these
            # already carry their own REAL file path from review_code_skill
            # (e.g. "backend/main.py"), which review_issues below uses
            # directly instead of parse_tag (parse_tag's `\[(\w+)\]` regex
            # wouldn't match "[holistic review]" anyway - a space isn't a
            # word character - confirmed before choosing this formatting).
            logger.validator_result(STAGE, "val", "HolisticReview", "FAIL" if blocking else "PASS",
                                   issue_count=len(blocking))
            problems += [f"(holistic review) {i.get('file', '?')}: {i.get('description', '')}" for i in blocking]

        passed = not problems
        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0)
        if passed:
            logger.success("E2E_VAL passed: system integration checks clean")
        else:
            logger.warning(f"E2E_VAL failed (attempt {attempts}/{MAX_STAGE_ATTEMPTS}): {len(problems)} problem(s)")
            for p in problems[:3]:
                logger.info(f"   - {p[:200]}")

        give_up = not passed and attempts >= MAX_STAGE_ATTEMPTS
        new_status = {"testing": "done"} if passed else ({"testing": "failed"} if give_up else {})

        # review_issues (the CURRENT snapshot Supervisor reads to decide
        # which agent to route to - see _problems_to_review_issues'
        # docstring) combines the tagged deterministic findings with the
        # holistic review's own real per-file findings (kept separate from
        # the generic tag-parsing path - see the "(holistic review)" note
        # above for why).
        holistic_issues = [
            {"severity": str(i.get("severity", "high")), "file": i.get("file", "?"),
             "description": i.get("description", ""), "suggested_fix": i.get("suggested_fix", ""),
             "source": "holistic_review"}
            for i in holistic_findings
        ]
        tagged_problems = [p for p in problems if not p.startswith("(holistic review)")]

        logger.stage("testing", "done" if passed else ("failed" if give_up else "pending"))
        logger.node_complete("e2e_val")
        return {
            "project": {"workspace": workspace},
            "runtime": {
                "stage_status": new_status,
                "stage_feedback": {STAGE: "" if passed else "\n".join(f"- {p}" for p in problems)},
                "quality_passed": passed if (passed or give_up) else None,
                # This round's per-test acceptance outcomes - persisted so
                # the NEXT E2E_VAL run's AcceptanceRegressionValidator can
                # compare against it (see skills/e2e_validators.py).
                "test_results": {"e2e_acceptance_outcomes": current_outcomes},
                # So the Supervisor (and Backend/Frontend's own RUN prompts)
                # actually learn WHAT broke and WHICH stage to route to.
                "review_issues": _problems_to_review_issues(tagged_problems) + holistic_issues,
                "testing_report": "" if passed else cap_report(
                    "E2E_VAL (does the complete system work correctly?) failed:\n"
                    + "\n".join(f"- {p}" for p in problems)
                ),
                "issue_history": [
                    {"severity": str(i.get("severity", "high")), "file": i.get("file", "?"),
                     "description": i.get("description", ""), "suggested_fix": i.get("suggested_fix", ""),
                     "source": "holistic_review"}
                    for i in holistic_findings
                ],
                "current_stage": "e2e_val",
                "completed_nodes": ["e2e_val", "testing"] if passed else [],
                "failed_nodes": ["e2e_val"] if give_up else [],
                "logs": [f"E2E_VAL: {'passed' if passed else f'{len(problems)} problem(s)'}"]
            }
        }
