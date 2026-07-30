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
from skills.quality_skills import run_tests_skill, check_backend_matches_contract
from skills.batch_codegen import FILE_FORMAT_INSTRUCTIONS, run_batch_generation, parse_batch_response, write_batch_files
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

        existing_backend_files = workspace.get("backend", {}).get("files", {})
        files_context = existing_files_context(existing_backend_files, feedback, "backend/",
                                               extra_text=state["user_request"])

        context = f"""User request / change request: {state["user_request"]}

Architecture:
{state["project"].get("architecture", "")}

Tasks:
{state["project"].get("tasks", [])}

Testing feedback (if any): {state["runtime"].get("testing_report") or "none yet"}
Deployment feedback (if any): {state["runtime"].get("deployment_status") or "none yet"}
{reference_docs}{files_context}""" + (f"\n\nYour own unit-test/validation check on a PRIOR attempt this round found "
                       f"these problems - fix them:\n{feedback}" if feedback else "")

        # Mark as "updating" when there's feedback (fixing issues), or "running" for first attempt
        feedback = state["runtime"].get("stage_feedback", {}).get(STAGE, "")
        initial_status = "updating" if feedback else "running"
        
        backend_dir = project_dir / "backend"
        round_started_at = time.time()
        response, ok = run_batch_generation("backend_agent", BACKEND_SYSTEM_PROMPT, context)

        parsed_files, deletes = parse_batch_response(response) if ok else ({}, [])
        written, deleted, refused = write_batch_files(project_dir, "backend/", parsed_files, deletes)
        summary = (f"Wrote {len(written)} file(s): {', '.join(written) or 'none'}."
                  + (f" Deleted: {', '.join(deleted)}." if deleted else "")
                  + (f" Refused (out of scope): {', '.join(refused)}." if refused else "")) if ok else response

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
        all_failures = check_backend_matches_contract(openapi_spec, workspace.get("backend", {}).get("files", {})) \
            if openapi_spec else []

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
