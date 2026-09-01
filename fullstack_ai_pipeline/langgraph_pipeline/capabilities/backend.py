"""
Backend Agent - BE_RUN / BE_UT / BE_VAL as three real LangGraph nodes (see
core/graph.py for the edges), matching the documented architecture diagram.
The retry loop (UT/VAL fail -> back to RUN) is a real graph edge now, not a
Python for-loop inside one node - see core/stage_loop.py for the shared
routing logic every stage's split uses.
"""

import time

from core.state import ProjectState
from core.stage_loop import MAX_STAGE_ATTEMPTS, checkpoint, existing_files_context
from skills.project_registry import project_dir_for, sync_workspace_from_disk
from skills.agent_tools import has_marker_file, touched_since
from skills.quality_skills import run_tests_skill
from skills.be_validators import run_be_ut_validators, run_be_val_validators
from skills.batch_codegen import FILE_FORMAT_INSTRUCTIONS, run_batch_generation, parse_batch_response, write_batch_files
from skills.planning_skills import mark_tasks_complete_skill
from skills.acceptance_test_generator import generate_acceptance_tests_skill
from skills.generation_strategy import decide_strategy, decide_strategy_verbose, Strategy
from core.logger import get_logger

STAGE = "backend"
MAX_REVIEW_REPEATS = 3  # same threshold/reasoning as database.py's schema/contract leniency

# A backend with zero manifest file can't actually be installed/run, no
# matter how many other files exist under it.
_BACKEND_MARKER_FILES = ("requirements.txt", "package.json")

BACKEND_SYSTEM_PROMPT = """You are a backend engineer working inside a shared project directory. You have
no tools - everything you need to know is already given to you below (schema.sql, openapi.yaml, and on a
revision, the full current content of every file already on disk under backend/). There is no follow-up
turn to inspect something you weren't given: reason entirely from what's in this prompt and produce the
complete, final content of every file you need to write or change, in this one response.

CRITICAL - Check ALL Python files including subdirectories, not just the one feedback happens to name:
- A syntax error in routers/__init__.py is just as critical as one in main.py - check EVERYTHING that
  was given to you above, not just the file a vague feedback message points at.
- If feedback mentions a SyntaxError but doesn't name the file, check every Python file shown to you.

Stack: ALWAYS Python + FastAPI + SQLAlchemy, no exceptions - this is fixed, not a per-project choice
(the rest of this pipeline - Dockerfile generation, the schema/ORM contract - assumes it, and picking
a different language/framework leaves the project permanently inconsistent between rounds).

Requirements:
- Entry point (main.py), route handlers implementing openapi.yaml EXACTLY (same paths, including
  whatever prefix - or lack of one - it actually uses; match it exactly rather than assuming a
  convention), database connection setup, requirements.txt, basic error handling.
- Database: PostgreSQL via the DATABASE_URL environment variable (os.environ["DATABASE_URL"], with a
  localhost fallback only for outside Docker). Never hardcode host/user/password/dbname. Models MUST
  use SQLAlchemy matching schema.sql (declarative Base + one class per table), with psycopg2-binary as
  the driver - don't pick a different ORM or raw SQL.
- CORS: add permissive CORS middleware (allow_origins=["*"]) since the frontend runs on a different
  origin/port and calls this API from a browser - without it every request is silently blocked.
- requirements.txt: do NOT pin exact versions you're not sure exist (a hallucinated version number
  fails the whole Docker build) - bare names or conservative ">=" ranges only. Only list packages you
  actually use.
- Include a test_main.py using pytest + FastAPI's TestClient with a couple of real smoke tests.
- Before finishing, re-check every name you used is actually imported at the top of that same file.
  Common miss: using "status.HTTP_200_OK"/"status.HTTP_404_NOT_FOUND" etc. without "from fastapi
  import status". A name that isn't imported is a real runtime crash (NameError), not a style issue -
  it's exactly as serious as a missing file.
- For a column default timestamp, use "from sqlalchemy.sql import func" then "default=func.now()" -
  NEVER "TIMESTAMP.now()". TIMESTAMP is a column TYPE (from sqlalchemy), not a value - it has no
  .now() method at all, imported or not, and calling it crashes the app at import time (an
  AttributeError, not a missing-import NameError) before the server ever binds a port. This is a real,
  observed failure, not a hypothetical one - check every column default for this specific mistake.
- This check applies ACROSS every file you touch this round, not just the one file feedback pointed
  at - fixing one file's missing import while breaking another file's import is not progress, it's a
  net-zero (or negative) change. Concretely: if you add/rename a model class (e.g. in models.py),
  re-check every OTHER file that imports it (routers, main.py, test_main.py) uses the same name and
  import path. If you add a column/relationship needing "ForeignKey" or "relationship", confirm THAT
  file imports them from sqlalchemy. Before your final response, mentally re-read every file you wrote
  or touched this round together, as if you were the one importing them, not each file in isolation.
- Imports between YOUR OWN backend/ files MUST be absolute (e.g. "from database import get_db",
  "from routers import notes"), NEVER relative (no "from .database import", no "from ..main import").
  backend/ has no __init__.py package structure - it's copied flat into the Docker image and run as
  "uvicorn main:app" from inside that directory, so a relative import fails immediately at startup
  with "attempted relative import with no known parent package", crashing the container before it
  ever binds a port. This applies to every file, including test_main.py and files inside routers/ -
  a file in routers/ still imports its sibling backend/ modules as "from database import ...", not
  "from .database import ..." (which would incorrectly look inside routers/ itself) or "from ..database
  import ..." (which goes too far up). If you add a routers/ or similar subpackage, also write an empty
  routers/__init__.py so "from routers import notes" resolves.

If you find backend/ already contains a DIFFERENT stack (e.g. Node/Express from an earlier round),
replace it: output the correct Python files as @@@FILE@@@ blocks, then @@@DELETE@@@ every old file that
doesn't belong (package.json, knexfile.js, src/app.js, etc.) - don't just overwrite them with empty
content, actually delete them, and don't leave a mix of two stacks behind.

If feedback below (or the user request/change request itself) describes a specific problem in one of
YOUR files, output an @@@FILE@@@ block for exactly that file - don't regenerate every other file from
scratch just because you were called this round. A request naming one file (e.g. "fix the syntax error
in backend/routers/__init__.py") means touch THAT file - rewriting main.py/models.py/schemas.py/etc.
that weren't mentioned and have no reported problem is not "being thorough," it's unnecessary work and
unnecessary risk of breaking something that already worked. Ignore feedback about frontend-only files.

CRITICAL - When feedback is provided:
1. IDENTIFY the exact file and the specific line/import causing the error, from what's already shown
   to you above (it's either inlined already, or the error message itself tells you what's wrong).
2. FIX it by outputting a corrected @@@FILE@@@ block with that file's complete new content.
3. DO NOT respond with only analysis and no @@@FILE@@@ blocks - if feedback exists, something IS broken.
4. If feedback says "undefined name X", you MUST add the missing import or fix the typo.
5. Your response is ONLY valid if it contains at least one @@@FILE@@@ block that fixes the issue.

The "User request / change request" above may describe changes spanning BOTH backend and frontend in
one sentence. Only act on the part that's about backend/ files. If part of it describes a frontend-only
change (UI, pages, components, React, "Home.jsx", styling, etc.), that is NOT your job - don't attempt
it, and NEVER output an @@@FILE@@@/@@@DELETE@@@ block for a frontend/ path (it will be silently refused
- you can only write under backend/). Just skip that part of the request entirely - a different round
will send that work to the frontend agent.

@@@DELETE@@@ is ONLY for a file you want gone for good (e.g. cleaning up a different stack's leftovers,
per above). If you're keeping a file and changing its content, just output an @@@FILE@@@ block with the
new content directly - it already replaces whatever was there. Never emit both a @@@DELETE@@@ and an
@@@FILE@@@ block for the same path; that's always contradictory.
""" + FILE_FORMAT_INSTRUCTIONS + """
Output ONLY the @@@FILE@@@/@@@DELETE@@@ blocks above - no summary or commentary needed, the pipeline
logs what you wrote automatically."""


class BackendCapability:
    def run(self, state: ProjectState) -> dict:
        """BE_RUN: writes backend/ files - one real attempt per graph step."""
        logger = get_logger()
        logger.node_start("backend_run")
        checkpoint(state)

        project_dir = project_dir_for(state["project"]["project_id"])
        logger.info("Single-shot batch generation (no tool-calling loop) - write scoped to backend/")

        workspace = state["project"].get("workspace", {})
        schema = workspace.get("database", {}).get("schema", "")
        openapi_spec = workspace.get("contract", {}).get("openapi_spec", "")
        reference_docs = ""
        if schema:
            reference_docs += f"\n\nschema.sql (already provided, no need to read it):\n{schema}"
        if openapi_spec:
            reference_docs += f"\n\nopenapi.yaml (already provided, no need to read it):\n{openapi_spec}"

        # Mark as "updating" when there's feedback (fixing issues), or "running" for first attempt
        feedback = state["runtime"].get("stage_feedback", {}).get(STAGE, "")
        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0) + 1
        logger.info(f"BE_RUN attempt {attempts}/{MAX_STAGE_ATTEMPTS}")
        logger.stage_started(STAGE, attempts)

        existing_backend_files = workspace.get("backend", {}).get("files", {})

        # Generation Strategy Engine (skills/generation_strategy.py) - a
        # fresh project or a small/medium update stays on the proven,
        # full-context batch path; only a genuinely large backend with a
        # LOW cross-module-impact change switches to incremental tool-
        # calling (skills/incremental_codegen.py). Correctness always
        # wins ties - see that module's docstring for exactly why.
        strategy, strategy_reason, strategy_meta = decide_strategy_verbose(
            not existing_backend_files, existing_backend_files, state["user_request"], feedback)
        logger.info(f"Generation strategy: {strategy.value} ({strategy_reason})")
        logger.generation_strategy(STAGE, strategy.value, strategy_reason, strategy_meta)

        backend_dir = project_dir / "backend"
        round_started_at = time.time()
        # Snapshot before this round's generation call so the delta after
        # tells us exactly what THIS round cost - the Generation Strategy
        # Comparison the observability spec asks for (batch vs incremental).
        _llm_before = dict(logger._llm_by_agent.get("backend_agent", {}))

        # BATCH and INCREMENTAL are now the SAME generation pipeline -
        # existing_files_context() -> run_batch_generation() ->
        # parse_batch_response() -> write_batch_files() - differing only
        # in how many of backend/'s existing files get inlined into the
        # one generation call. INCREMENTAL narrows that to a deterministically
        # selected subset (skills/incremental_codegen.py); BATCH inlines
        # everything (existing_files_context's own size-based fallback
        # already handles "too big to fully inline" by matching feedback).
        # A prior ReAct tool-calling implementation of INCREMENTAL was
        # removed after being confirmed to cause quadratic token growth.
        #
        # Project Index kept fresh (and the structure summary built from
        # it) regardless of strategy - the "Architecture" text below is
        # frozen from the project's FIRST build and never reflects routes/
        # endpoints added by later updates. Confirmed real harm from that
        # staleness alone (not just an incremental-mode issue): a
        # generation call relying only on stale architecture text has no
        # way to know what currently exists, which is exactly the gap that
        # let a routing file get silently rewritten with routes missing.
        from skills.project_index import update_project_index, summarize_project_structure
        project_index = update_project_index(project_dir, workspace)
        structure_summary = summarize_project_structure(project_index)

        if strategy == Strategy.INCREMENTAL:
            from skills.incremental_codegen import select_generation_scope
            files_for_context = select_generation_scope(
                existing_backend_files, state["user_request"], feedback,
                project_index=project_index, extra_text=state["user_request"],
                path_prefix="backend/",
            )
        else:
            files_for_context = existing_backend_files

        files_context = existing_files_context(files_for_context, feedback, "backend/",
                                               extra_text=state["user_request"])

        context = f"""User request / change request: {state["user_request"]}

Architecture:
{state["project"].get("architecture", "")}""" + (f"""

{structure_summary}""" if structure_summary else "") + f"""

Tasks:
{state["project"].get("tasks", [])}

Testing feedback (if any): {state["runtime"].get("testing_report") or "none yet"}
Deployment feedback (if any): {state["runtime"].get("deployment_status") or "none yet"}
{reference_docs}{files_context}""" + (f"\n\nYour own unit-test/validation check on a PRIOR attempt this round found "
                       f"these problems - fix them:\n{feedback}" if feedback else "")

        logger.context_size(STAGE, strategy.value, len(files_for_context), context)
        response, ok = run_batch_generation("backend_agent", BACKEND_SYSTEM_PROMPT, context)

        parsed_files, deletes = parse_batch_response(response) if ok else ({}, [])
        written, deleted, refused = write_batch_files(project_dir, "backend/", parsed_files, deletes)
        summary = (f"Wrote {len(written)} file(s): {', '.join(written) or 'none'}."
                  + (f" Deleted: {', '.join(deleted)}." if deleted else "")
                  + (f" Refused (out of scope): {', '.join(refused)}." if refused else "")) if ok else response

        # Generation Strategy Comparison record (observability spec) - this
        # round's own cost, correlating strategy with LLM calls/tokens/
        # files/duration/result so batch vs incremental can be judged
        # objectively across a run, not pieced together by hand afterward.
        _llm_after = logger._llm_by_agent.get("backend_agent", {})
        _calls_this_round = _llm_after.get("calls", 0) - _llm_before.get("calls", 0)
        _in_tok_before, _out_tok_before = _llm_before.get("input_tokens", 0), _llm_before.get("output_tokens", 0)
        _in_tok = _llm_after.get("input_tokens", 0) - _in_tok_before
        _out_tok = _llm_after.get("output_tokens", 0) - _out_tok_before
        logger.generation_round(
            STAGE, strategy.value, _calls_this_round,
            _in_tok if _llm_after else None, _out_tok if _llm_after else None,
            len(files_for_context), len(written), (time.time() - round_started_at) * 1000,
            state["runtime"].get("stage_attempts", {}).get(STAGE, 0),
            "written" if written else ("no_op" if ok else "failed"),
        )

        # Keep the Project Index fresh regardless of which generation mode
        # ran (batch generation never touches it mid-round the way the
        # incremental path just did above) - cheap (only re-indexes files
        # that actually changed, see update_project_index) and means the
        # NEXT incremental round always has an up-to-date cache to query.
        if ok and written:
            from skills.project_index import update_project_index
            fresh_workspace = sync_workspace_from_disk(project_dir, state["project"].get("workspace", {}))
            update_project_index(project_dir, fresh_workspace)

        wrote_files = any(backend_dir.rglob("*")) if backend_dir.exists() else False
        has_manifest = has_marker_file(backend_dir, _BACKEND_MARKER_FILES)
        made_progress = touched_since(backend_dir, round_started_at) if backend_dir.exists() else False
        
        prior_failures = state["runtime"].get("consecutive_agent_failures", 0)
        consecutive_failures = 0 if ok else prior_failures + 1

        # CRITICAL FIX: If agent didn't touch files, check if they're actually correct before failing
        # This prevents infinite loop when files are already correct but status is "failed"
        if ok and wrote_files and has_manifest and not made_progress:
            logger.info("No files changed - checking if existing code is already correct...")
            workspace = sync_workspace_from_disk(project_dir, state["project"].get("workspace", {}))
            from skills.quality_skills import run_tests_skill
            test_results = run_tests_skill(workspace)
            backend_failures = [f["error"] for f in test_results["failures"] if f["file"].startswith("backend/")]
            
            if not backend_failures:
                logger.success("Backend files are already correct - marking as done")
                proceed = True
                made_progress = True  # Override - files are correct so this counts as success
            else:
                logger.warning(f"Backend has {len(backend_failures)} issues - agent must fix them")
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
            logger.warning(f"Backend agent: {summary[:200]}")
            logger.stage("backend", "failed")
            logger.node_complete("backend_run")
            return {
                "runtime": {
                    "stage_status": {"backend": "failed", "testing": "pending"},
                    "stage_attempts": {STAGE: attempts},
                    "stage_progress": {STAGE: False},
                    "quality_passed": None,
                    "review_issues": [],
                    "testing_report": "",
                    "current_stage": "backend_run",
                    "completed_nodes": ["backend_run"],
                    "failed_nodes": ["backend_run"],
                    "consecutive_agent_failures": consecutive_failures,
                    "logs": [f"Backend Agent: {summary[:300]}"]
                }
            }

        logger.info(f"Backend agent: {summary[:200]}")

        # Self-report which of Planner's tasks this round's write satisfies -
        # only when files were genuinely written this attempt (written is
        # non-empty), matching database.py's "only ask when something
        # actually changed" reasoning.
        task_status_update = {}
        if written:
            tasks = state["project"].get("tasks", [])
            architecture = state["project"].get("architecture", "")
            task_summary = f"Wrote/changed these backend files: {', '.join(written)}"
            for i in mark_tasks_complete_skill(tasks, architecture, "backend", task_summary):
                task_status_update[i] = True

            # Backend's own call into the SHARED Acceptance Test Generator
            # (skills/acceptance_test_generator.py, Database's the first
            # caller - see database.py's run()) - only regenerated when
            # backend/ actually changed this round, same "no point re-asking
            # on a no-op round" reasoning. test_backend.py becomes another
            # real BE_RUN artifact; BE_VAL's Startup Validator executes it
            # for real, over real HTTP, against the really-booted container
            # (see skills/be_startup_validator.py).
            acceptance_criteria = state["project"].get("acceptance_criteria", [])
            if acceptance_criteria:
                test_guidance = """This tests a real, running FastAPI backend over real HTTP - NOT an
in-process TestClient. Tests MUST make real HTTP requests using the `requests` library against
os.environ["BASE_URL"] (e.g. requests.post(f"{BASE_URL}/auth/token", ...)) - never import the FastAPI
app directly, it isn't importable from this test process. If the API uses token-based authentication,
obtain a real token first via the actual login/token endpoint and include it as a Bearer Authorization
header on subsequent requests - do not assume any user/token already exists; create whatever fixture
data each test needs itself through the API's own endpoints (e.g. register before logging in). Assert
on real HTTP status codes and real response body content matching the acceptance criteria below (e.g. a
rejected visitor really returns a 4xx and really doesn't appear in a later list call) - never a trivial
assertion with no real check behind it."""
                stage_context = (f"openapi.yaml:\n{openapi_spec}\n\n"
                                 f"Backend files written this round: {', '.join(written)}")
                tests_code = generate_acceptance_tests_skill(
                    "backend", stage_context, acceptance_criteria, architecture, tasks, test_guidance,
                )
                if tests_code:
                    (project_dir / "test_backend.py").write_text(tests_code, encoding="utf-8")
                    logger.success(f"Generated test_backend.py ({len(tests_code)} chars)")

        logger.node_complete("backend_run")
        return {
            "runtime": {
                # Whatever Testing found before this round is now unverified
                # against the code just written - stale review_issues/
                # quality_passed would otherwise sit frozen. Force a re-verify.
                "stage_status": {"backend": "validating", "testing": "pending"},
                "quality_passed": None,
                "review_issues": [],
                "testing_report": "",
                "stage_attempts": {STAGE: attempts},
                "stage_progress": {STAGE: True},
                "task_status": task_status_update,
                # What backend/ and openapi.yaml looked like BEFORE this
                # round's write - BE_VAL's Contract/CRUD validator diffs this
                # against the new state to tell a genuine REGRESSION (an
                # operation that worked before and is still required, now
                # silently gone) apart from ordinary in-progress work (a
                # newly-required operation nobody has built yet), same
                # reasoning as database.py's previous_schema.
                "previous_backend_files": existing_backend_files,
                "previous_openapi_for_backend": openapi_spec,
                "current_stage": "backend_run",
                "completed_nodes": ["backend_run"],
                "consecutive_agent_failures": consecutive_failures,
                "logs": [f"Backend Agent: {summary[:300]}"]
            }
        }

    def check_ut(self, state: ProjectState) -> dict:
        """BE_UT: pytest + imports pass? Deterministic ast/pyflakes/relative-
        import checks - the same ones the later E2E stage would eventually
        run anyway, caught here costs one graph step instead of a full
        Supervisor -> e2e -> Supervisor -> Backend round-trip."""
        logger = get_logger()
        logger.node_start("backend_ut")
        checkpoint(state)

        project_dir = project_dir_for(state["project"]["project_id"])
        workspace = sync_workspace_from_disk(project_dir, state["project"].get("workspace", {}))
        test_results = run_tests_skill(workspace)
        failures = [f["error"] for f in test_results["failures"] if f["file"].startswith("backend/")]

        # Structural checks beyond syntax/imports - router registration and
        # SQLAlchemy-model-vs-schema.sql column consistency (see
        # skills/be_validators.py for why these two specifically, and why
        # a Depends()-resolution checker was deliberately NOT added here -
        # confirmed already redundant with the existing cross-file-import
        # check above).
        backend_files = workspace.get("backend", {}).get("files", {})
        schema = workspace.get("database", {}).get("schema", "")
        failures += run_be_ut_validators(backend_files, schema)

        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0)
        passed = not failures
        if passed:
            logger.success("BE_UT passed: pytest + imports clean")
        else:
            logger.warning(f"BE_UT failed (attempt {attempts}/{MAX_STAGE_ATTEMPTS}): {len(failures)} problem(s)")
            # Log the actual errors so we can see them in console
            for i, error in enumerate(failures[:3], 1):
                logger.info(f"   Error {i}: {error[:200]}")
            if len(failures) > 3:
                logger.info(f"   ... and {len(failures) - 3} more errors")

        give_up = not passed and attempts >= MAX_STAGE_ATTEMPTS
        logger.node_complete("backend_ut")
        return {
            "project": {"workspace": workspace},
            "runtime": {
                "stage_status": {"backend": "failed"} if give_up else {},
                "stage_feedback": {STAGE: "" if passed else "\n".join(f"- {e}" for e in failures)},
                "current_stage": "backend_ut",
                "failed_nodes": ["backend_ut"] if give_up else [],
                "logs": [f"BE_UT: {'passed' if passed else f'{len(failures)} problem(s)'}"]
            }
        }

    def check_val(self, state: ProjectState) -> dict:
        """BE_VAL: matches openapi.yaml? Does the code actually implement
        what the contract declares, not just "no syntax errors"."""
        logger = get_logger()
        logger.node_start("backend_val")
        checkpoint(state)

        project_dir = project_dir_for(state["project"]["project_id"])
        workspace = sync_workspace_from_disk(project_dir, state["project"].get("workspace", {}))
        openapi_spec = workspace.get("contract", {}).get("openapi_spec", "")
        previous_backend_files = state["runtime"].get("previous_backend_files", {})
        previous_openapi_for_backend = state["runtime"].get("previous_openapi_for_backend", "")

        # The shared Acceptance Test Generator's output (see backend.py's
        # run()), if BE_RUN wrote one this round - executed for real by the
        # Startup Validator's Requirement Test Runner against the really-
        # booted container (see skills/be_startup_validator.py).
        test_path = project_dir / "test_backend.py"
        test_file_content = test_path.read_text(encoding="utf-8") if test_path.exists() else ""

        all_failures = run_be_val_validators(
            workspace.get("backend", {}).get("files", {}), openapi_spec,
            previous_backend_files, previous_openapi_for_backend,
            test_file_content=test_file_content,
        ) if openapi_spec else []

        # A contract-match gap that keeps recurring across MAX_REVIEW_REPEATS
        # OUTER rounds is often NOT a backend defect at all - it can be the
        # CONTRACT itself being wrong (confirmed live: openapi.yaml declared
        # "/notes" - a generic placeholder name the model hallucinated -
        # while Backend correctly implemented "/tasks" matching the real
        # requirements). Backend has no ability to edit openapi.yaml (it can
        # only write under backend/), so retrying Backend forever can never
        # fix a contract-side defect - this is the same leniency already
        # applied to Database's own recurring gaps.
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
                           f"resolving (likely a contract-side issue Backend can't fix) - no longer blocking: {f}")
            else:
                failures.append(f)

        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0)
        passed = not failures
        if passed:
            logger.success("BE_VAL passed: matches openapi.yaml")
        else:
            logger.warning(f"BE_VAL failed (attempt {attempts}/{MAX_STAGE_ATTEMPTS}): {len(failures)} gap(s)")

        give_up = not passed and attempts >= MAX_STAGE_ATTEMPTS
        stage_status = {"backend": "done"} if passed else ({"backend": "failed"} if give_up else {})

        logger.stage("backend", "done" if passed else ("failed" if give_up else "pending"))
        logger.node_complete("backend_val")
        return {
            "project": {"workspace": workspace},
            "runtime": {
                "stage_status": stage_status,
                "stage_feedback": {STAGE: "" if passed else "\n".join(f"- {e}" for e in failures)},
                "issue_history": [
                    {"severity": "medium", "file": "backend/", "description": f,
                     "suggested_fix": "Implement the missing route, or fix openapi.yaml if the path name is wrong.",
                     "source": "contract_match"}
                    for f in all_failures
                ],
                "current_stage": "backend_val",
                "completed_nodes": ["backend_val", "backend"] if passed else [],
                "failed_nodes": ["backend_val"] if give_up else [],
                "logs": [f"BE_VAL: {'passed' if passed else f'{len(failures)} gap(s)'}"]
            }
        }
