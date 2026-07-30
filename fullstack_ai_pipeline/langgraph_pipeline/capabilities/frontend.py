"""
Frontend Agent - FE_RUN / FE_UT / FE_VAL as three real LangGraph nodes (see
core/graph.py for the edges), matching the documented architecture diagram.
File access is scoped to frontend/, but READ access covers the whole project
including backend/ - this is the fix for the frontend hardcoding a port that
didn't match the backend's actual one: it can open the backend's real code
instead of only seeing an OpenAPI spec.
"""

import time

from core.state import ProjectState
from core.stage_loop import MAX_STAGE_ATTEMPTS, checkpoint, existing_files_context
from skills.project_registry import project_dir_for, sync_workspace_from_disk
from skills.agent_tools import has_marker_file, touched_since
from skills.quality_skills import run_tests_skill, check_frontend_matches_backend
from skills.batch_codegen import FILE_FORMAT_INSTRUCTIONS, run_batch_generation, parse_batch_response, write_batch_files
from core.logger import get_logger

STAGE = "frontend"
MAX_REVIEW_REPEATS = 3  # same threshold/reasoning as database.py's schema/contract leniency

# A React+Vite frontend with no package.json anywhere can't be npm
# installed/run, no matter how many components got written. A STATIC
# frontend (no backend/no build step at all) has no package.json by design -
# index.html is its equivalent minimum-viability marker instead.
_FRONTEND_MARKER_FILES = ("package.json",)
_STATIC_FRONTEND_MARKER_FILES = ("index.html",)

_SHARED_FRONTEND_RULES = """
Design quality - reject generic/templated output. Before writing any UI, decide the page's single job
and let the actual subject matter suggest distinctive choices (colors, structure, imagery) - avoid the
same generic gradient-hero/big-number-stat pattern that makes most AI-generated pages look alike.
- Typography: pick a deliberate display/body font pairing for THIS project, not a default reached for
  on any project - use real scale, weight, and spacing as part of the design, not an afterthought.
- Structural elements (numbering, dividers, section labels) should encode something true about the
  content, not just decorate it - only number things if the order actually matters.
- Motion: animate only where it serves a real purpose (load/scroll/hover feedback) - restraint reads
  as more intentional than decoration; excess animation is often what makes a UI look AI-generated.
- Match the complexity of your execution to the complexity of the design direction - a minimal
  design needs precise spacing/type/detail, not just less effort.

If feedback below describes a specific problem in one of YOUR files, output an @@@FILE@@@ block for
exactly that file - don't regenerate everything from scratch. Ignore feedback about backend-only files.

CRITICAL - When feedback is provided:
1. IDENTIFY the exact file and the specific line/import causing the error, from what's already shown
   to you above (it's either inlined already, or the error message itself tells you what's wrong).
2. FIX it by outputting a corrected @@@FILE@@@ block with that file's complete new content.
3. DO NOT respond with only analysis and no @@@FILE@@@ blocks - if feedback exists, something IS broken.
4. If feedback says "undefined name X" or "hardcoded URL", you MUST fix it.
5. Your response is ONLY valid if it contains at least one @@@FILE@@@ block that fixes the issue.

If you're abandoning a file entirely (not just editing it), output an @@@DELETE@@@ block rather than
overwriting it with empty content - an empty file still exists and can break the build or confuse the
next review pass. But if you're KEEPING a file and just changing its content, output an @@@FILE@@@ block
directly - it already replaces whatever was there. Never emit both a @@@DELETE@@@ and an @@@FILE@@@
block for the same path; that's always contradictory.

The "User request / change request" above may describe changes spanning BOTH backend and frontend in
one sentence. Only act on the part that's about frontend/ files. If part of it describes a backend-only
change (API routes, database models, server logic, etc.), that is NOT your job - don't attempt it, and
NEVER output an @@@FILE@@@/@@@DELETE@@@ block for a backend/ path (it will be silently refused - you
can only write under frontend/). Just skip that part of the request entirely - a different round will
send that work to the backend agent.
""" + FILE_FORMAT_INSTRUCTIONS + """
Output ONLY the @@@FILE@@@/@@@DELETE@@@ blocks above - no summary or commentary needed, and don't respond
with only a plan or description of what you're about to do - output real @@@FILE@@@ blocks every round,
even a revision round with only small changes to make."""

FRONTEND_SYSTEM_PROMPT = """You are a frontend engineer working inside a shared project directory.
openapi.yaml, the full current content of backend/ (so you can check its ACTUAL port/routes, not just
the contract), and (on a revision) the full current content of every file already on disk under
frontend/, are all already included below in your context - there is no follow-up turn to inspect
anything else, reason entirely from what's given here and write the complete, working frontend
implementation under frontend/ in this one response.

Stack: ALWAYS React + Vite, no exceptions - this is fixed, not a per-project choice (Dockerfile
generation assumes a Vite dev script, and picking a different framework leaves the project
permanently inconsistent between rounds, the same way switching backend frameworks would).

Requirements:
- Entry point (src/main.jsx), App.jsx, feature components/pages, an API client, routing if needed,
  package.json, index.html, basic styling.
- vite.config.js is REQUIRED, every time, with "import react from '@vitejs/plugin-react'" and
  "plugins: [react()]" - without it, Vite falls back to esbuild's default JSX handling, which uses the
  CLASSIC JSX runtime (compiles JSX to bare "React.createElement(...)" calls) instead of the automatic
  runtime - and the classic runtime requires "React" to be imported and in scope in EVERY file that uses
  JSX, which plain "import { useState } from 'react'"-style named imports do NOT provide. Real, observed
  failure: a project with no vite.config.js crashed immediately on load with "React is not defined",
  blanking the entire page, because no file explicitly imported the React default export. The
  @vitejs/plugin-react plugin is what enables the automatic runtime (no React import needed per file) -
  it must actually be wired into vite.config.js, not just listed in package.json's devDependencies.
- API base URL: read it from import.meta.env.VITE_API_BASE_URL, with a "http://localhost:8000"
  fallback ONLY for when that env var is unset. Never hardcode a specific port as the primary value -
  the backend's real port is injected via that env var at deploy time and is not always the same number.
- Call the EXACT paths in openapi.yaml, including whatever prefix (or lack of one) it actually uses -
  match it exactly rather than assuming a convention.
- package.json: use real, current "^X.Y.Z" ranges (no invented versions), include a "dev" script (Vite's
  default) since that's what gets run to serve the app, only list dependencies you use.

If you find frontend/ already contains a DIFFERENT framework (e.g. Vue/Angular from an earlier round),
replace it with React + Vite and @@@DELETE@@@ every old file that doesn't belong.
""" + _SHARED_FRONTEND_RULES

# Used when execution_plan.backend is False - there's no API to call, so
# forcing React+Vite/an OpenAPI contract/a build step onto the model is
# actively wrong context, not just unnecessary - it was observed causing the
# agent to respond with only a rambling text plan and zero real writes
# instead of acting, on a plain "just HTML/CSS/JS, no backend" request.
STATIC_FRONTEND_SYSTEM_PROMPT = """You are a frontend engineer working inside a shared project
directory. This project has NO backend and NO API - it is a plain, static, client-side-only site.

Stack: plain HTML, CSS, and JavaScript ONLY - no React, no Vue, no Vite, no npm, no build step, no
package.json, no bundler. It must run by opening index.html directly in a browser, or being served as
plain static files (e.g. nginx) - nothing to install, nothing to compile.

Requirements:
- index.html as the entry point, plus whatever .css and .js files make sense (inline is also fine for
  something this small, but separate files are usually cleaner).
- Every requirement in the user's request must actually work in the browser with no server, no
  external API calls, and no dependency on anything outside these files.
- Clean, modern styling and layout - this is a real UI/UX deliverable, not a bare-bones page.

If you find frontend/ already contains a framework/build setup (package.json, src/, vite.config, etc.)
from an earlier round meant for a DIFFERENT kind of project, @@@DELETE@@@ every file that doesn't belong
and replace it with plain HTML/CSS/JS.
""" + _SHARED_FRONTEND_RULES


class FrontendCapability:
    def run(self, state: ProjectState) -> dict:
        """FE_RUN: writes frontend/ files - one real attempt per graph step."""
        logger = get_logger()
        logger.node_start("frontend_run")
        checkpoint(state)

        project_dir = project_dir_for(state["project"]["project_id"])
        logger.info("Single-shot batch generation (no tool-calling loop) - write scoped to frontend/")

        is_static = not state["runtime"]["execution_plan"].get("backend")

        workspace = state["project"].get("workspace", {})
        openapi_spec = workspace.get("contract", {}).get("openapi_spec", "")
        reference_docs = ""
        if openapi_spec and not is_static:
            reference_docs = f"\n\nopenapi.yaml (already provided, no need to read it):\n{openapi_spec}"
        if not is_static:
            # Previously satisfied by a live read_file against backend/ (the
            # fix for a hardcoded port mismatch - checking the backend's
            # REAL code, not just the contract). Inlined up front instead,
            # same size-budget logic as existing_files_context, so no read
            # access is needed for the common case.
            backend_context = existing_files_context(workspace.get("backend", {}).get("files", {}), "", "backend/")
            reference_docs += backend_context

        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0) + 1
        feedback = state["runtime"].get("stage_feedback", {}).get(STAGE, "")
        logger.info(f"FE_RUN attempt {attempts}/{MAX_STAGE_ATTEMPTS}")

        existing_frontend_files = workspace.get("frontend", {}).get("files", {})
        files_context = existing_files_context(existing_frontend_files, feedback, "frontend/",
                                               extra_text=state["user_request"])

        base_context = f"""User request / change request: {state["user_request"]}

Architecture:
{state["project"].get("architecture", "")}

Tasks:
{state["project"].get("tasks", [])}

Testing feedback (if any): {state["runtime"].get("testing_report") or "none yet"}
Deployment feedback (if any): {state["runtime"].get("deployment_status") or "none yet"}
{reference_docs}{files_context}""" + (f"\n\nYour own unit-test/validation check on a PRIOR attempt this round found "
                       f"these problems - fix them:\n{feedback}" if feedback else "")

        system_prompt = STATIC_FRONTEND_SYSTEM_PROMPT if is_static else FRONTEND_SYSTEM_PROMPT
        marker_files = _STATIC_FRONTEND_MARKER_FILES if is_static else _FRONTEND_MARKER_FILES

        frontend_dir = project_dir / "frontend"
        round_started_at = time.time()
        response, ok = run_batch_generation("frontend_agent", system_prompt, base_context)

        parsed_files, deletes = parse_batch_response(response) if ok else ({}, [])
        written, deleted, refused = write_batch_files(project_dir, "frontend/", parsed_files, deletes)
        summary = (f"Wrote {len(written)} file(s): {', '.join(written) or 'none'}."
                  + (f" Deleted: {', '.join(deleted)}." if deleted else "")
                  + (f" Refused (out of scope): {', '.join(refused)}." if refused else "")) if ok else response

        wrote_files = any(frontend_dir.rglob("*")) if frontend_dir.exists() else False
        has_manifest = has_marker_file(frontend_dir, marker_files)
        made_progress = touched_since(frontend_dir, round_started_at) if frontend_dir.exists() else False
        
        prior_failures = state["runtime"].get("consecutive_agent_failures", 0)
        consecutive_failures = 0 if ok else prior_failures + 1

        # CRITICAL FIX: If agent didn't touch files, check if they're actually correct before failing
        # This prevents infinite loop when files are already correct but status is "failed"
        if ok and wrote_files and has_manifest and not made_progress:
            logger.info("No files changed - checking if existing code is already correct...")
            workspace = sync_workspace_from_disk(project_dir, state["project"].get("workspace", {}))
            from skills.quality_skills import run_tests_skill
            test_results = run_tests_skill(workspace)
            frontend_failures = [f["error"] for f in test_results["failures"] if f["file"].startswith("frontend/")]
            
            if not frontend_failures:
                logger.success("Frontend files are already correct - marking as done")
                proceed = True
                made_progress = True  # Override - files are correct so this counts as success
            else:
                logger.warning(f"Frontend has {len(frontend_failures)} issues - agent must fix them")
                # DON'T mark as failed here - instead, treat this as progress so UT/VAL can run
                # UT will see the failures and loop back to RUN with proper feedback
                proceed = True
                made_progress = True  # Allow UT/VAL to run and provide feedback
        else:
            proceed = ok and wrote_files and has_manifest and made_progress

        if not proceed:
            summary = (f"Agent finished but made no real progress this attempt (files written: {wrote_files}, "
                      f"manifest present: {has_manifest}, touched this attempt: {made_progress}). "
                      f"Raw response: {summary[:200]}")
            logger.warning(f"Frontend agent: {summary[:200]}")
            logger.stage("frontend", "failed")
            logger.node_complete("frontend_run")
            return {
                "runtime": {
                    "stage_status": {"frontend": "failed", "testing": "pending"},
                    "stage_attempts": {STAGE: attempts},
                    "stage_progress": {STAGE: False},
                    "quality_passed": None,
                    "review_issues": [],
                    "testing_report": "",
                    "current_stage": "frontend_run",
                    "completed_nodes": ["frontend_run"],
                    "failed_nodes": ["frontend_run"],
                    "consecutive_agent_failures": consecutive_failures,
                    "logs": [f"Frontend Agent: {summary[:300]}"]
                }
            }

        logger.info(f"Frontend agent: {summary[:200]}")
        logger.node_complete("frontend_run")
        return {
            "runtime": {
                "stage_status": {"frontend": "validating", "testing": "pending"},
                "quality_passed": None,
                "review_issues": [],
                "testing_report": "",
                "stage_attempts": {STAGE: attempts},
                "stage_progress": {STAGE: True},
                "current_stage": "frontend_run",
                "completed_nodes": ["frontend_run"],
                "consecutive_agent_failures": consecutive_failures,
                "logs": [f"Frontend Agent: {summary[:300]}"]
            }
        }

    def check_ut(self, state: ProjectState) -> dict:
        """FE_UT: static checks pass? Bracket-balance + import checks, same
        ones the later E2E stage would eventually run anyway."""
        logger = get_logger()
        logger.node_start("frontend_ut")
        checkpoint(state)

        project_dir = project_dir_for(state["project"]["project_id"])
        workspace = sync_workspace_from_disk(project_dir, state["project"].get("workspace", {}))
        test_results = run_tests_skill(workspace)
        failures = [f["error"] for f in test_results["failures"] if f["file"].startswith("frontend/")]

        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0)
        passed = not failures
        if passed:
            logger.success("FE_UT passed: static checks clean")
        else:
            logger.warning(f"FE_UT failed (attempt {attempts}/{MAX_STAGE_ATTEMPTS}): {len(failures)} problem(s)")
            # Log the actual errors so we can see them in console
            for i, error in enumerate(failures[:3], 1):
                logger.info(f"   Error {i}: {error[:200]}")
            if len(failures) > 3:
                logger.info(f"   ... and {len(failures) - 3} more errors")

        give_up = not passed and attempts >= MAX_STAGE_ATTEMPTS
        logger.node_complete("frontend_ut")
        return {
            "project": {"workspace": workspace},
            "runtime": {
                "stage_status": {"frontend": "failed"} if give_up else {},
                "stage_feedback": {STAGE: "" if passed else "\n".join(f"- {e}" for e in failures)},
                "current_stage": "frontend_ut",
                "failed_nodes": ["frontend_ut"] if give_up else [],
                "logs": [f"FE_UT: {'passed' if passed else f'{len(failures)} problem(s)'}"]
            }
        }

    def check_val(self, state: ProjectState) -> dict:
        """FE_VAL: routes match backend? Does every API call the frontend
        actually makes correspond to a real backend route - the exact bug
        class ("frontend calls a URL the backend never implemented") no
        syntax check can ever catch. A no-op for a static (no-backend)
        project - nothing to compare against, so nothing gets flagged."""
        logger = get_logger()
        logger.node_start("frontend_val")
        checkpoint(state)

        project_dir = project_dir_for(state["project"]["project_id"])
        workspace = sync_workspace_from_disk(project_dir, state["project"].get("workspace", {}))
        openapi_spec = workspace.get("contract", {}).get("openapi_spec", "")
        all_failures = check_frontend_matches_backend(
            openapi_spec, workspace.get("backend", {}).get("files", {}), workspace.get("frontend", {}).get("files", {})
        )

        # Same leniency as backend.py/database.py: a gap that keeps recurring
        # across rounds despite Frontend's own self-heal attempts is often a
        # contract or backend-side naming inconsistency Frontend has no power
        # to fix (it can only write under frontend/) - retrying Frontend
        # forever can't resolve a defect that isn't Frontend's to fix.
        history = state["runtime"].get("issue_history", [])
        repeat_counts = {}
        for past_issue in history:
            if past_issue.get("source") != "contract_match":
                continue
            repeat_counts[past_issue.get("description")] = repeat_counts.get(past_issue.get("description"), 0) + 1

        failures = []
        for f in all_failures:
            if repeat_counts.get(f, 0) >= MAX_REVIEW_REPEATS:
                logger.info(f"Contract-match gap has recurred {repeat_counts[f]}+ times across rounds without "
                           f"resolving - no longer blocking: {f}")
            else:
                failures.append(f)

        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0)
        passed = not failures
        if passed:
            logger.success("FE_VAL passed: routes match backend")
        else:
            logger.warning(f"FE_VAL failed (attempt {attempts}/{MAX_STAGE_ATTEMPTS}): {len(failures)} gap(s)")

        give_up = not passed and attempts >= MAX_STAGE_ATTEMPTS
        stage_status = {"frontend": "done"} if passed else ({"frontend": "failed"} if give_up else {})

        logger.stage("frontend", "done" if passed else ("failed" if give_up else "pending"))
        logger.node_complete("frontend_val")
        return {
            "project": {"workspace": workspace},
            "runtime": {
                "stage_status": stage_status,
                "stage_feedback": {STAGE: "" if passed else "\n".join(f"- {e}" for e in failures)},
                "issue_history": [
                    {"severity": "medium", "file": "frontend/", "description": f,
                     "suggested_fix": "Fix the calling route, or the backend/contract if the path name is wrong.",
                     "source": "contract_match"}
                    for f in all_failures
                ],
                "current_stage": "frontend_val",
                "completed_nodes": ["frontend_val", "frontend"] if passed else [],
                "failed_nodes": ["frontend_val"] if give_up else [],
                "logs": [f"FE_VAL: {'passed' if passed else f'{len(failures)} gap(s)'}"]
            }
        }
