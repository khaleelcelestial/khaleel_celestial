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
from skills.quality_skills import run_tests_skill
from skills.fe_build_validator import run_frontend_build_checks
from skills.fe_validators import run_fe_val_validators
from skills.batch_codegen import FILE_FORMAT_INSTRUCTIONS, run_batch_generation, parse_batch_response, write_batch_files
from skills.planning_skills import mark_tasks_complete_skill
from skills.acceptance_test_generator import generate_acceptance_tests_skill
from skills.generation_strategy import decide_strategy, decide_strategy_verbose, Strategy
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
DESIGN SYSTEM - a file at src/styles/design-system.css (styles/design-system.css for a static
project) already exists on disk, written by the pipeline itself, NOT by you - it defines every color,
spacing value, font, radius, shadow, and a full set of reusable component classes (.btn/.btn-primary,
.card/.card-grid, .table/.table-wrapper, .badge/.badge-success etc., .form-group/.form-input,
.page-header/.page-title, .modal-overlay/.modal, .chart-container, .app-shell layout classes for the
sidebar/header/main shell). You do not need to read its full content to use it - these variable/class
names ARE the contract. This exists because letting every generation round invent its own colors/
spacing/fonts from scratch is exactly what produced inconsistent styling, mismatched card heights, and
sidebar-overlapping-content bugs in the past - there was no persisted source of truth to stay faithful
to across rounds.

MANDATORY - use it instead of inventing new ones:
- Import it once (main.jsx: `import './styles/design-system.css'` - or a <link> tag in index.html for
  a static project) - never re-declare these variables or duplicate these class definitions elsewhere.
- Colors: use var(--color-primary), var(--color-danger), var(--color-text-muted), etc. - never a
  hardcoded hex value for anything this system already names. The ONLY exception: you MAY override
  --color-primary and --color-primary-hover in your own CSS to a domain-appropriate hue for THIS
  project - do not touch any other variable.
- Spacing/radius/shadow: use var(--space-4), var(--radius-md), var(--shadow-sm), etc. - never an
  arbitrary literal value like "13px" or "0.4rem".
- Buttons/cards/tables/badges/forms/modals: use the provided classes (.btn-primary, .card, .table,
  .badge-success, .form-input, ...) as the base, adding a page-specific class alongside for anything
  page-unique - don't reimplement what the system already provides.
- Layout shell (sidebar + header + main content): use the .app-shell/.app-shell__sidebar/
  .app-shell__header/.app-shell__main grid classes for the authenticated layout. Do NOT use
  position: absolute for the sidebar - that is exactly what causes it to overlap page content; the
  provided Grid layout reserves its track so overlap is structurally impossible. Every navigation
  component (Sidebar/TopBar/Nav) that gets created MUST actually be rendered in the JSX tree
  (imported AND used as a tag) somewhere reachable from every authenticated route - a nav component
  that's only defined/exported and never rendered is a real, confirmed bug class, not a style nitpick.
- Charts: give the container class="chart-container" (a real, non-zero responsive box) - a chart
  library sizing to its default 0x0 is a real, confirmed failure mode ("empty or poorly sized charts").

You still have real creative room: which pages/sections exist, what content goes where, layout
decisions specific to this project's actual data, and (within the one allowed override above) an
accent color - just not the foundational tokens consistency actually depends on.

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
- src/styles/design-system.css already exists on disk (written by the pipeline, see DESIGN SYSTEM
  below) - src/main.jsx MUST import it (e.g. `import './styles/design-system.css'`) as the first
  stylesheet import, before any of your own page-specific CSS.
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
- styles/design-system.css already exists on disk (written by the pipeline, see DESIGN SYSTEM below) -
  index.html MUST link it (`<link rel="stylesheet" href="styles/design-system.css">`) in <head>,
  before any of your own page-specific <link>/<style> content.

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
        logger.stage_started(STAGE, attempts)

        existing_frontend_files = workspace.get("frontend", {}).get("files", {})

        # Generation Strategy Engine (skills/generation_strategy.py) - same
        # decision Backend's own run() makes, applied here to frontend/'s
        # file set. A static site naturally stays BATCH_ADAPTIVE (it's
        # rarely large enough to cross the incremental threshold) without
        # any special-casing needed.
        strategy, strategy_reason, strategy_meta = decide_strategy_verbose(
            not existing_frontend_files, existing_frontend_files, state["user_request"], feedback)
        logger.info(f"Generation strategy: {strategy.value} ({strategy_reason})")
        logger.generation_strategy(STAGE, strategy.value, strategy_reason, strategy_meta)

        system_prompt = STATIC_FRONTEND_SYSTEM_PROMPT if is_static else FRONTEND_SYSTEM_PROMPT
        marker_files = _STATIC_FRONTEND_MARKER_FILES if is_static else _FRONTEND_MARKER_FILES

        frontend_dir = project_dir / "frontend"
        round_started_at = time.time()
        _llm_before = dict(logger._llm_by_agent.get("frontend_agent", {}))

        # BATCH and INCREMENTAL are now the SAME generation pipeline - see
        # capabilities/backend.py's own run() for the full rationale (a
        # prior ReAct tool-calling implementation of INCREMENTAL was
        # removed after being confirmed to cause quadratic token growth).
        #
        # Project Index/structure summary kept fresh regardless of
        # strategy - see capabilities/backend.py's own run() for why the
        # stale "Architecture" text alone isn't enough (a real, observed
        # regression: App.jsx got rewritten with routes silently dropped
        # because nothing told the model what routes currently exist).
        from skills.project_index import update_project_index, summarize_project_structure
        project_index = update_project_index(project_dir, workspace)
        structure_summary = summarize_project_structure(project_index)

        if strategy == Strategy.INCREMENTAL:
            from skills.incremental_codegen import select_generation_scope
            files_for_context = select_generation_scope(
                existing_frontend_files, state["user_request"], feedback,
                project_index=project_index, extra_text=state["user_request"],
                path_prefix="frontend/",
            )
        else:
            files_for_context = existing_frontend_files

        files_context = existing_files_context(files_for_context, feedback, "frontend/",
                                               extra_text=state["user_request"])

        base_context = f"""User request / change request: {state["user_request"]}

Architecture:
{state["project"].get("architecture", "")}""" + (f"""

{structure_summary}""" if structure_summary else "") + f"""

Tasks:
{state["project"].get("tasks", [])}

Testing feedback (if any): {state["runtime"].get("testing_report") or "none yet"}
Deployment feedback (if any): {state["runtime"].get("deployment_status") or "none yet"}
{reference_docs}{files_context}""" + (f"\n\nYour own unit-test/validation check on a PRIOR attempt this round found "
                       f"these problems - fix them:\n{feedback}" if feedback else "")

        logger.context_size(STAGE, strategy.value, len(files_for_context), base_context)
        response, ok = run_batch_generation("frontend_agent", system_prompt, base_context)

        parsed_files, deletes = parse_batch_response(response) if ok else ({}, [])
        written, deleted, refused = write_batch_files(project_dir, "frontend/", parsed_files, deletes)
        summary = (f"Wrote {len(written)} file(s): {', '.join(written) or 'none'}."
                  + (f" Deleted: {', '.join(deleted)}." if deleted else "")
                  + (f" Refused (out of scope): {', '.join(refused)}." if refused else "")) if ok else response

        # Deterministic, pipeline-authored design system - written AFTER the
        # model's own output so it always wins over anything the model wrote
        # at this exact path, and unconditionally every round so consistency
        # never depends on the model remembering to preserve it on a
        # revision. See skills/design_system.py's module docstring for why
        # this exists (LLM-invented-per-project styling was the confirmed
        # root cause of the inconsistent colors/spacing/radius/shadows and
        # sidebar-overlap complaints this closes). Plain CSS custom
        # properties work identically with or without a build step, so this
        # applies to the static HTML/CSS/JS path too, just at a different
        # path (no src/ directory convention there) - the prompt tells each
        # path how to actually reference it (JS import vs a <link> tag).
        if ok:
            from skills.design_system import DESIGN_SYSTEM_CSS
            styles_dir = frontend_dir / ("src/styles" if not is_static else "styles")
            styles_dir.mkdir(parents=True, exist_ok=True)
            (styles_dir / "design-system.css").write_text(DESIGN_SYSTEM_CSS, encoding="utf-8")

        # Generation Strategy Comparison record - same reasoning as
        # capabilities/backend.py's own post-generation snapshot diff.
        _llm_after = logger._llm_by_agent.get("frontend_agent", {})
        _calls_this_round = _llm_after.get("calls", 0) - _llm_before.get("calls", 0)
        _in_tok = _llm_after.get("input_tokens", 0) - _llm_before.get("input_tokens", 0)
        _out_tok = _llm_after.get("output_tokens", 0) - _llm_before.get("output_tokens", 0)
        logger.generation_round(
            STAGE, strategy.value, _calls_this_round,
            _in_tok if _llm_after else None, _out_tok if _llm_after else None,
            len(files_for_context), len(written), (time.time() - round_started_at) * 1000,
            state["runtime"].get("stage_attempts", {}).get(STAGE, 0),
            "written" if written else ("no_op" if ok else "failed"),
        )

        # Keep the Project Index fresh regardless of which generation mode
        # ran, same reasoning as capabilities/backend.py's own post-write
        # refresh - cheap (only re-indexes genuinely changed files) and
        # keeps the NEXT incremental round's cache up to date.
        if ok and written:
            from skills.project_index import update_project_index
            fresh_workspace = sync_workspace_from_disk(project_dir, state["project"].get("workspace", {}))
            update_project_index(project_dir, fresh_workspace)

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

        # Self-report which of Planner's tasks this round's write satisfies -
        # same reasoning as backend.py/database.py.
        task_status_update = {}
        if written:
            tasks = state["project"].get("tasks", [])
            architecture = state["project"].get("architecture", "")
            task_summary = f"Wrote/changed these frontend files: {', '.join(written)}"
            for i in mark_tasks_complete_skill(tasks, architecture, "frontend", task_summary):
                task_status_update[i] = True

            # Frontend's own call into the SHARED Acceptance Test Generator
            # (skills/acceptance_test_generator.py, Database's the first
            # caller) - only regenerated when frontend/ actually changed
            # this round, same "no point re-asking on a no-op round"
            # reasoning as database.py/backend.py. Skipped entirely for a
            # static (no-backend) site - no React components/build step for
            # Vitest/React Testing Library to exercise there. test_frontend.
            # test.jsx becomes another real FE_RUN artifact; FE_UT's real
            # `vitest run` (skills/fe_build_validator.py) executes it for
            # real against the actually-generated components.
            if not is_static:
                acceptance_criteria = state["project"].get("acceptance_criteria", [])
                if acceptance_criteria:
                    test_guidance = """This tests real React components with Vitest + React Testing Library -
NOT snapshot tests (brittle, not meaningful) and NOT tests of implementation details (internal state,
prop names). Import and render real components/pages from this project using `render` from
'@testing-library/react', query them via `screen` (getByRole/getByLabelText/getByText - prefer
accessible queries over test IDs), and simulate real interaction via `fireEvent` or `@testing-library/
user-event` (click a real button, type into a real input, submit a real form). Assert on real,
user-observable outcomes matching the acceptance criteria below - text that actually appears/disappears,
an input's actual value after typing, a callback that's actually called. Mock any network calls
(fetch/axios) with `vi.fn()`/`vi.mock()` - never make a real HTTP request in a unit test, that's a
different test layer's job. This test file will be placed at src/test_frontend.test.jsx - write its
import paths to real components/pages relative to that exact location (e.g. `import Login from
'./pages/Login'` if pages live at src/pages/), based on the actual file paths shown to you below."""
                    fresh_workspace = sync_workspace_from_disk(project_dir, state["project"].get("workspace", {}))
                    stage_context = (f"Backend openapi.yaml (for context on what the UI talks to):\n{openapi_spec}\n\n"
                                     f"Frontend files written this round: {', '.join(written)}\n\n"
                                     + existing_files_context(fresh_workspace.get("frontend", {}).get("files", {}),
                                                              "", "frontend/"))
                    tests_code = generate_acceptance_tests_skill(
                        "frontend", stage_context, acceptance_criteria, architecture, tasks, test_guidance,
                        output_format="Vitest + React Testing Library test", code_lang="jsx",
                    )
                    if tests_code:
                        (project_dir / "test_frontend.test.jsx").write_text(tests_code, encoding="utf-8")
                        logger.success(f"Generated test_frontend.test.jsx ({len(tests_code)} chars)")

                    # Phase 5 - the SAME shared generator's Playwright output,
                    # a full-user-flow companion to the Vitest component
                    # tests above. Executed for real by FE_VAL's Browser
                    # Validator (skills/fe_browser_validator.py) against the
                    # really-booted stack, not another static pass.
                    e2e_guidance = """This tests COMPLETE USER WORKFLOWS with Playwright (@playwright/test) against
a REAL, running, already-deployed instance of this app - NOT a mocked/stubbed backend, real HTTP traffic hits a
real API and a real database. Use CommonJS `const { test, expect } = require('@playwright/test');` at the top
(NOT `import` - this file is executed as CommonJS regardless of this project's own package.json settings);
navigate via `page.goto('/path')` (baseURL is already configured - use relative paths, never hardcode a
host/port); interact via real `page.click(...)`/`page.fill(...)`/`page.getByRole(...)`; assert on real,
user-visible outcomes (a new row actually appears in a list after creating it, a form validation message
actually appears for bad input, a protected page actually redirects to login when logged out). Cover full
workflows end to end (e.g. log in -> navigate to a page -> create/edit/delete something -> see the result),
not isolated component behavior - that's the Vitest suite's job, not this one's. This test file will be placed
at test_frontend.spec.cjs at the project root - write real page paths/selectors based on the actual
routes/components shown to you below."""
                    e2e_tests_code = generate_acceptance_tests_skill(
                        "frontend", stage_context, acceptance_criteria, architecture, tasks, e2e_guidance,
                        output_format="Playwright (@playwright/test) end-to-end test", code_lang="javascript",
                    )
                    if e2e_tests_code:
                        (project_dir / "test_frontend.spec.cjs").write_text(e2e_tests_code, encoding="utf-8")
                        logger.success(f"Generated test_frontend.spec.cjs ({len(e2e_tests_code)} chars)")

        logger.node_complete("frontend_run")
        return {
            "runtime": {
                "stage_status": {"frontend": "validating", "testing": "pending"},
                "quality_passed": None,
                "review_issues": [],
                "testing_report": "",
                "stage_attempts": {STAGE: attempts},
                "stage_progress": {STAGE: True},
                "task_status": task_status_update,
                # What frontend/ looked like BEFORE this round's write -
                # FE_VAL's advisory-only route-regression check (see
                # skills/fe_validators.py's RouteRegressionValidator) diffs
                # this against the new state.
                "previous_frontend_files": existing_frontend_files,
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
        logger.validator_result(STAGE, "ut", "StaticChecks", "FAIL" if failures else "PASS",
                               issue_count=len(failures))

        # Real Node-based tooling beyond the regex-based checks above - a
        # real `vite build` (proves syntax/import/dependency integrity for
        # free), real ESLint (hooks-rules + static accessibility gaps), and
        # real circular-import detection (madge). Gated behind the cheap
        # regex checks already passing, same cost-gating pattern as the
        # Docker-based validators (no point spending a real npm
        # install+build on code already known to be broken by a free check).
        # See skills/fe_build_validator.py for what's deliberately NOT
        # checked here (TypeScript - not applicable, this pipeline never
        # generates it; Prettier - style-only, no correctness value).
        if not failures:
            # The shared Acceptance Test Generator's output (see this
            # class's run()), if FE_RUN wrote one this round - executed for
            # real by run_frontend_build_checks' Vitest runner.
            test_path = project_dir / "test_frontend.test.jsx"
            test_file_content = test_path.read_text(encoding="utf-8") if test_path.exists() else ""
            build_failures = run_frontend_build_checks(
                workspace.get("frontend", {}).get("files", {}), project_dir, test_file_content,
            )
            logger.validator_result(STAGE, "ut", "BuildValidator", "FAIL" if build_failures else "PASS",
                                   issue_count=len(build_failures))
            failures += build_failures

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
        """FE_VAL: does the frontend correctly implement the API contract,
        navigation, and auth gating it's supposed to? Method-aware API
        contract matching (does every call correspond to a real backend
        route/operation, not just a matching path), navigation self-
        consistency (every route reachable, every nav link real), and
        conditional auth-gating (a defined-but-unused route guard). A
        near no-op for a static (no-backend) project - nothing to compare
        API calls against, so only navigation/auth checks can fire."""
        logger = get_logger()
        logger.node_start("frontend_val")
        checkpoint(state)

        project_dir = project_dir_for(state["project"]["project_id"])
        workspace = sync_workspace_from_disk(project_dir, state["project"].get("workspace", {}))
        openapi_spec = workspace.get("contract", {}).get("openapi_spec", "")
        all_failures = run_fe_val_validators(
            workspace.get("frontend", {}).get("files", {}), openapi_spec, workspace.get("backend", {}).get("files", {})
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

        # Real-browser layer (Phase 4) - a really-booted stack driven by a
        # real Chromium browser (Runtime/console/Responsive/Accessibility/
        # auth-bypass checks). Gated behind the static checks above already
        # passing - booting Docker + launching a browser costs real
        # wall-clock time, so there's no point paying that cost to
        # re-confirm a frontend already known to be broken by checks that
        # are free. No leniency/repeat-tracking applied here (unlike the
        # contract-match gaps above) - a runtime/accessibility/responsive
        # defect is always within Frontend's own power to fix, unlike a
        # contract-side naming mismatch it can't touch.
        if not failures:
            from skills.fe_browser_validator import validate_frontend_in_browser
            # The shared Acceptance Test Generator's Playwright output (see
            # this class's run()), if FE_RUN wrote one this round - executed
            # for real by validate_frontend_in_browser's Requirement Test
            # Runner against the same already-booted stack.
            spec_path = project_dir / "test_frontend.spec.cjs"
            spec_content = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
            browser_failures = validate_frontend_in_browser(project_dir, workspace, spec_content)
            logger.validator_result(STAGE, "val", "BrowserValidator", "FAIL" if browser_failures else "PASS",
                                   issue_count=len(browser_failures))
            failures += browser_failures

        # Update Validation (advisory only - see fe_validators.py's
        # RouteRegressionValidator docstring for why this never blocks
        # FE_VAL's pass/fail, only feeds into feedback text so it's visible
        # on the next round without risking an unfixable infinite retry
        # loop over what might just be an intentional redesign).
        from skills.fe_validators import check_route_regressions
        previous_frontend_files = state["runtime"].get("previous_frontend_files", {})
        regression_notes = check_route_regressions(previous_frontend_files, workspace.get("frontend", {}).get("files", {}))
        for note in regression_notes:
            logger.info(f"Update Validation (advisory, not blocking): {note}")

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
        feedback_text = "\n".join(f"- {e}" for e in failures)
        if regression_notes and not passed:
            # Only riding along on an ALREADY-failing round (never added to
            # a clean pass) - stage_feedback's normal contract is "problems
            # the next RUN attempt must fix", and a passing round's
            # stage_feedback is deliberately cleared to "" elsewhere in this
            # pipeline; polluting that on a genuinely clean pass would make
            # a future, unrelated FE_RUN attempt misread stale advisory
            # text as a real blocking problem.
            feedback_text += "\n\nUpdate Validation (advisory, not blocking this round, but worth checking):\n" \
                + "\n".join(f"- {n}" for n in regression_notes)
        return {
            "project": {"workspace": workspace},
            "runtime": {
                "stage_status": stage_status,
                "stage_feedback": {STAGE: feedback_text},
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
