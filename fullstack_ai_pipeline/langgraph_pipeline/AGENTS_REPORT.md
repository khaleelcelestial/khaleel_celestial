# Pipeline Agents Report

Full reference for every agent in `langgraph_pipeline`: what it does, its tools, its complete system prompt, its inputs/outputs, and how control flows between them. Generated 2026-07-27 to support flow-improvement work.

---

## 1. Overall flow

```
Fresh build:  Planner -> Database -> Supervisor <-> {Backend, Frontend, Testing, Deployment} -> done
--update:                            Supervisor <-> {Backend, Frontend, Testing, Deployment} -> done
```

- **Planner** and **Database** each run once, deterministically, only on a fresh build. `--update` mode (`route_entry` in `core/graph.py`) skips both and enters directly at Supervisor, reusing whatever `stage_status`/workspace was reconstructed from the project's saved `.project.json` snapshot.
- **Supervisor** is the loop hub. Every other agent hands control back to it (wired in `core/graph.py`); it makes one routing decision per round and either sends work to one agent or declares `"done"` (ending the graph).
- Backstops: `MAX_SUPERVISOR_ROUNDS = 20` (absolute round cap), `MAX_CONSECUTIVE_FAILURES = 3` (stops early if every provider/fallback is exhausted 3 calls in a row - assumed quota outage).

State shape (`core/state.py`): `ProjectState = {user_request, project: {project_id, requirements, architecture, tasks, workspace}, runtime: {execution_plan, stage_status, quality_passed, review_issues, testing_report, deployment_status, next_agent, supervisor_rounds, consecutive_agent_failures, completed_nodes, failed_nodes, ...}}`.

---

## 2. Planner

**Runs:** once, fresh build only.
**File:** `capabilities/planner.py`
**Tools:** none (no file access) - calls 4 sequential LLM skills, no tool-calling/ReAct loop.

**What it does:**
1. `analyze_request_skill` -> execution plan (5 booleans: `database`, `contract`, `backend`, `frontend`, `release`)
2. `extract_requirements_skill` -> requirements text
3. `create_task_list_skill` -> list of task strings
4. `choose_stack_skill` -> architecture description (see note below - the stack itself is fixed, not chosen)
5. Sets every stage's initial `stage_status` purely from the execution plan (`pending` if needed, `skipped` if not) - this is the ONE place `stage_status` gets its starting values.

**System prompt (`analyze_request_skill`, in `skills/planning_skills.py`):**
```
You are a software project analyzer. Given a user request, determine which stages of the software
pipeline are needed.

Output ONLY a JSON object with these boolean flags:
{
  "database": true/false,
  "contract": true/false,
  "backend": true/false,
  "frontend": true/false,
  "release": true/false
}

Rules:
- database: true if the request mentions database, schema, data model, SQL, Postgres, MySQL, etc.
- contract: true if database OR backend OR frontend is true (API contract needed for integration)
- backend: true if request mentions API, REST, backend, server, FastAPI, Express, Django, endpoints, etc.
- frontend: true if request mentions UI, dashboard, React, Vue, Angular, pages, components, frontend, etc.
- release: true if backend OR frontend is true (need to package and run), false if only database schema or contract design

[5 worked examples follow]
```

**`choose_stack_skill`'s system prompt** (important: this does NOT let the model pick a framework - it only describes how a FIXED stack maps onto this request):
```
You are a technical architect. Given a user request and execution plan, describe the technology stack
this project will use.

The stack itself is FIXED across every project this pipeline builds - not your choice to make...
- Database: PostgreSQL.
- Backend: Python + FastAPI + SQLAlchemy (declarative Base matching schema.sql) + psycopg2-binary.
- Frontend: React + Vite.

Your job is only to describe how THIS request maps onto that fixed stack...
```
Note: as of today this description is only accurate when `execution_plan.backend` is true - for a static-only project, Frontend now uses a completely different prompt (see section 5) that this description doesn't reflect. Worth updating `choose_stack_skill` to branch the same way if you want the architecture text to stay accurate for static projects.

**Model:** Gateway primary (`PLANNING` tier), Ollama `qwen2.5:7b` -> Groq -> Mistral fallback.

**Output -> state:** `project.requirements`, `project.architecture`, `project.tasks`, `runtime.execution_plan`, `runtime.stage_status`.

---

## 3. Database

**Runs:** once, fresh build; or whenever Supervisor routes back here for a schema/contract change.
**File:** `capabilities/database.py`
**Tools:** none - two deterministic-style LLM skills, writes files directly to disk itself (not via agent tool calls).

**What it does:**
- If `execution_plan.database`: `generate_schema_skill` -> `schema.sql`, validated, written to disk.
- If `execution_plan.contract`: `generate_openapi_skill` -> `openapi.yaml`, written to disk.
- On a **revision** (schema/contract already exists and actually changes), resets `backend`/`frontend`/`testing` stage_status back to `pending` and clears `quality_passed`/`review_issues`/`testing_report` - their code now needs to catch up to the new contract.

**Model:** Gateway primary (`STRUCTURED` tier), Ollama `qwen2.5-coder:7b` fallback.

**Output -> state:** `project.workspace.database`, `project.workspace.contract`, downstream `stage_status` resets.

---

## 4. Backend

**Runs:** whenever Supervisor routes to `"backend"`.
**File:** `capabilities/backend.py`
**Tools** (`skills/agent_tools.py`, via `make_agent_tools(project_dir, write_prefix="backend/", allow_commands=False)`): `read_file`, `list_files` (unrestricted read, anywhere in the project - can read `frontend/` too), `write_file`, `delete_file` (both **write-scoped to `backend/`** - a write outside that prefix is refused, not silently redirected).

**Full system prompt:**
```
You are a backend engineer working inside a shared project directory.
schema.sql and openapi.yaml are already included below in your context - don't spend a tool call
re-reading them. If this is a revision, use list_files/read_file to check your own previous backend/
files and the frontend/ directory, then write a complete, working backend implementation under
backend/ using write_file.

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
  Common misses: using "TIMESTAMP.now()" or similar without "from sqlalchemy import TIMESTAMP", or
  using "status.HTTP_200_OK"/"status.HTTP_404_NOT_FOUND" etc. without "from fastapi import status".
  A name that isn't imported is a real runtime crash (NameError), not a style issue - it's exactly as
  serious as a missing file.
- This check applies ACROSS every file you touch this round, not just the one file feedback pointed
  at - fixing one file's missing import while breaking another file's import is not progress, it's a
  net-zero (or negative) change. [...cross-file consistency instructions, added today...]
- Imports between YOUR OWN backend/ files MUST be absolute [...relative-import rules, with exact
  reasoning about why: no __init__.py package structure, run as "uvicorn main:app" from inside the
  directory...]

If you find backend/ already contains a DIFFERENT stack (e.g. Node/Express from an earlier round),
replace it: write the correct Python files, then delete_file every old file that doesn't belong...

If feedback below describes a specific problem in one of YOUR files, fix exactly that - don't
regenerate everything from scratch. Ignore feedback about frontend-only files.

The "User request / change request" above may describe changes spanning BOTH backend and frontend...
Only act on the part that's about backend/ files... NEVER call write_file/delete_file on a frontend/
path (it will be refused - you can only write under backend/). If a write is refused because it's
outside your scope, do not retry it; just skip that part of the request entirely...

delete_file is ONLY for a file you want gone for good... Never call delete_file on a file and then
immediately write_file the same path again; that's always redundant.

Write files with write_file, remove obsolete ones with delete_file. When done, respond with a short
summary of what you built, fixed, or removed (no further tool calls).
```

**Context given alongside the prompt** (inlined, not read via tool call): user request, architecture, tasks, last testing/deployment feedback, `schema.sql`, `openapi.yaml`.

**Completion check:** stage marked `"done"` only if ALL of: agent call succeeded (`ok`), `backend/` has any files, a manifest file (`requirements.txt` or `package.json`) exists, AND `touched_since()` confirms at least one file was actually written **this round** (mtime-based - added today to catch a no-op round that would otherwise falsely read as "done"). On completion, force-resets `testing` to `pending` and clears `quality_passed`/`review_issues`/`testing_report` (stale-verdict invalidation).

**Model:** Ollama `qwen2.5:7b` primary (CODING tier) - deliberately **never** the Gateway (no tool-calling support there).

---

## 5. Frontend

**Runs:** whenever Supervisor routes to `"frontend"`.
**File:** `capabilities/frontend.py`
**Tools:** same shape as Backend - `read_file`/`list_files` unrestricted, `write_file`/`delete_file` scoped to `frontend/`.

**Two system prompts now** (branch added today, on `is_static = not execution_plan.get("backend")`):

**A. `FRONTEND_SYSTEM_PROMPT`** (used when the project has a backend - full-stack, React+Vite):
```
You are a frontend engineer working inside a shared project directory.
openapi.yaml is already included below in your context... Use list_files/read_file to look at the
backend/ directory (you CAN and SHOULD look at the backend's actual code)...

Stack: ALWAYS React + Vite, no exceptions - this is fixed, not a per-project choice (Dockerfile
generation assumes a Vite dev script, and picking a different framework leaves the project
permanently inconsistent between rounds, the same way switching backend frameworks would).

Requirements:
- Entry point (src/main.jsx), App.jsx, feature components/pages, an API client, routing if needed,
  package.json, index.html, basic styling.
- API base URL: read it from import.meta.env.VITE_API_BASE_URL, with a "http://localhost:8000"
  fallback ONLY for when that env var is unset. Never hardcode a specific port as the primary value...
- Call the EXACT paths in openapi.yaml, including whatever prefix (or lack of one) it actually uses...
- package.json: use real, current "^X.Y.Z" ranges (no invented versions)...

If you find frontend/ already contains a DIFFERENT framework (e.g. Vue/Angular from an earlier round),
replace it with React + Vite and delete_file every old file that doesn't belong.
[+ shared rules, below]
```

**B. `STATIC_FRONTEND_SYSTEM_PROMPT`** (new today - used when there's NO backend at all):
```
You are a frontend engineer working inside a shared project directory. This project has NO backend
and NO API - it is a plain, static, client-side-only site.

Stack: plain HTML, CSS, and JavaScript ONLY - no React, no Vue, no Vite, no npm, no build step, no
package.json, no bundler. It must run by opening index.html directly in a browser, or being served as
plain static files (e.g. nginx) - nothing to install, nothing to compile.

Requirements:
- index.html as the entry point, plus whatever .css and .js files make sense...
- Every requirement in the user's request must actually work in the browser with no server, no
  external API calls, and no dependency on anything outside these files.
- Clean, modern styling and layout - this is a real UI/UX deliverable, not a bare-bones page.

If you find frontend/ already contains a framework/build setup... from an earlier round meant for a
DIFFERENT kind of project, delete_file every file that doesn't belong and replace it with plain
HTML/CSS/JS.
[+ shared rules, below]
```

**Shared rules appended to both:**
```
If feedback below describes a specific problem in one of YOUR files, fix exactly that - don't
regenerate everything from scratch. Ignore feedback about backend-only files. If you're abandoning a
file entirely... remove it with delete_file rather than overwriting it with empty content... But if
you're KEEPING a file and just changing its content, call write_file directly...

The "User request / change request" above may describe changes spanning BOTH backend and frontend...
Only act on the part that's about frontend/ files... NEVER call write_file/delete_file on a backend/
path (it will be refused)...

Write files with write_file, remove obsolete ones with delete_file. When done, respond with a short
summary of what you built, fixed, or removed (no further tool calls). Do NOT respond with only a plan
or description of what you're about to do - call write_file for real files before your final summary,
every round, even a revision round with only small changes to make.
```

**Completion check:** same pattern as Backend - `ok` + files exist + manifest (`package.json` for React/Vite, **`index.html` for static**) + `touched_since()` real-write confirmation.

**Model:** Ollama `qwen2.5:7b` primary (CODING tier), never the Gateway - same reason as Backend.

---

## 6. Testing

**Runs:** whenever Supervisor routes to `"testing"` (typically once backend/frontend show `"done"`).
**File:** `capabilities/testing.py`
**Tools:** `read_file`, `list_files`, `run_static_checks`, `review_code`, `run_command` (allowlisted - effectively just `pytest`). **No write access at all**, by design - it reports problems, doesn't fix them.

**Full system prompt:**
```
You are the testing agent. You verify - you do not write or fix code yourself (you have no write_file
tool on purpose; report problems so the supervisor can send them to the agent that owns that file).

The required stack for every project this pipeline builds (not a guess, not per-project) is: Postgres
database, Python + FastAPI + SQLAlchemy backend, React + Vite frontend, API paths matching openapi.yaml
exactly (whatever prefix it uses, or lack of one). Flag a genuine deviation from this... but don't
invent a "wrong framework" complaint against files that already match it; verify against what's
actually on disk, not assumptions.

You have three ways to actually verify the code - use them, don't guess:
1. run_static_checks - parses every backend/frontend file for syntax errors, undefined names, and a
   hardcoded-API-URL check... Always call this first.
2. review_code - a second LLM's opinion catching things static analysis can't (missing error
   handling, security issues, logic bugs, requirements the code doesn't actually satisfy). Pass it
   the required tasks summary given below so it can check for missing functionality too.
3. run_command("pytest") - if backend/ has a requirements.txt listing pytest and a test_main.py,
   try running it for real - only if you judge it likely to work without installing anything first
   (don't try to pip install).

Read any failing files with read_file to understand exactly what's wrong. Respond with a clear PASS
or FAIL summary listing every real problem found (file + description)...
```
**Note:** this prompt's "required stack" section is now stale for static projects too - it always says "React + Vite frontend," even for a project we've deliberately built as plain HTML/CSS/JS. Worth revisiting if you touch this next, so Testing doesn't flag a correctly-built static site as "wrong framework."

**Deterministic checks it also runs directly** (`skills/quality_skills.py::run_tests_skill`, independent of the LLM's own tool calls, so `runtime.test_results`/`review_issues` stay populated even if the agent's free-text report is terse): `ast.parse` + pyflakes (undefined names) on every backend `.py` file, `_check_relative_imports` (AST-based), bracket-balance check on frontend JS/TS, `_check_frontend_api_wiring` (flags a hardcoded `http://localhost:PORT` with no `import.meta.env`/`process.env` reference - correctly does NOT trigger on a static site with no API calls at all).

**Model:** the Testing agent's own tool-calling ReAct loop (`testing_agent` skill) is Ollama `qwen2.5:7b` primary (`REASONING` tier) - same tool-calling reason as Backend/Frontend/Deployment. Its `review_code` tool is a *separate*, non-tool-calling LLM call and IS Gateway-primary (`STRUCTURED` tier) - so one capability uses two different model routes for two different purposes.

**Output -> state:** `runtime.test_results`, `runtime.review_issues` (current snapshot), `runtime.issue_history` (audit trail, accumulates), `runtime.quality_passed` (tri-state: `None` = never run, `True`/`False` = real verdict), `runtime.testing_report` (capped to ~600 chars for reuse as context).

---

## 7. Deployment

**Runs:** whenever Supervisor routes to `"deployment"` (typically last, once testing passes).
**File:** `capabilities/deployment.py`
**Tools:** `read_file`, `list_files`, `run_command` (allowlisted: `docker compose up/ps/logs`, etc.) - **no write access**, verify+diagnose only.

**Full system prompt:**
```
You are the deployment agent. You verify and deploy - you do not write or fix application code
yourself (no write_file tool on purpose). The project's Dockerfiles and docker-compose.yml have
already been generated for you. Your job:
1. Run "docker compose up --build -d" to build and start everything.
2. Run "docker compose ps" to confirm what's actually running.
3. If anything failed to build or isn't running, run "docker compose logs <service>" to see why, and
   read the relevant source file with read_file to understand the root cause.
4. Respond with a clear final report: what's running, what failed, and the SPECIFIC file + fix needed
   for anything that failed (so the backend/frontend agent can act on it next round).
```

**Before the LLM agent even runs**, this capability does deterministic work: generates Dockerfiles/`docker-compose.yml` (`skills/docker_skills.py::write_docker_assets` - branches on backend stack Python vs. Node, frontend stack Node/Vite vs. static/nginx), generates `start.sh`/`start.bat` local-run fallbacks (now with a real `python3 -m http.server <port>` command for static frontends, fixed today - previously said "serve manually"), generates docs, saves a project registry snapshot, and does a deterministic Docker-availability pre-check (`shutil.which("docker")` + `docker info`) before spending an LLM call on what a plain command already answers.

**Model:** Ollama `qwen2.5:7b` primary (REASONING tier) - never the Gateway, same tool-calling reason as Backend/Frontend.

**Output -> state:** `runtime.deployment_status` (capped summary), `runtime.stage_status.deployment`, `runtime.final_project_path`.

---

## 8. Supervisor (the loop hub)

**Runs:** after every other agent, every round, until it says `"done"`.
**File:** `capabilities/supervisor.py`
**Tools:** none - one plain JSON-decision LLM call per round, with a rules-based deterministic fallback if the LLM is unavailable.

**Status message given to the LLM each round:**
```
User request / change request: {the actual request text}
Stage status: {dict of database/backend/frontend/testing/deployment -> pending/done/failed/skipped}
Already given a real attempt at this change request THIS run: {list of stages, or 'none yet'}
Quality passed last testing pass: {True/False/'not tested yet'}
Outstanding issues (N): {severity:file labels, capped at 8}
Testing agent's last full report: {capped text, or 'not run yet'}
Deployment agent's last full report: {capped text, or 'not deployed yet'}
```

**Full system prompt:**
```
You are the supervisor of a software build team. Given the current status, decide which agent
should act next.

Available agents right now: {only the ones this plan actually needs}
- "database": (re)generates schema.sql/openapi.yaml. Only route here if the CURRENT change request
  itself asks for a schema/API contract change... never for an ordinary code fix...
- "backend": implements/fixes the backend API
- "frontend": implements/fixes the frontend UI
- "testing": runs static checks + LLM review + real tests over what's been built so far
- "deployment": packages and deploys (docker) what's been built
- "done": everything this plan needs is built, tested with no outstanding issues, and deployed

Rules (in priority order - check them top to bottom, act on the FIRST one that applies):
1. "not tested yet" is NOT a failure - it means Testing has never run. If quality_passed shows "not
   tested yet" and backend/frontend stage_status is "done" (or "skipped"), you MUST route to
   "testing" next - never to "backend"/"frontend" just because quality_passed isn't literally true.
2. Read the user request above carefully - it can name BOTH a backend change and a frontend change
   in one sentence... Testing's static checks CANNOT detect [a logic/content bug]... If the request
   describes a frontend-specific change... route to "frontend" for that part even if frontend's
   stage_status already says "done"... Same logic in reverse for backend. Do not send backend-only
   instructions to frontend or vice versa.
   BUT: check "Already given a real attempt at this change request THIS run" first - once a stage
   appears there, it has already had its shot at the CURRENT request this run, so do NOT send it
   there again just because the raw request text still describes that same change (the text doesn't
   change between rounds, so re-reading it is not new evidence). Only route back to an
   already-addressed stage for a NEW, specific reason: a review_issue naming one of its files, a
   testing_report describing a concrete bug there, or a deployment failure tracing to its code.
3. Only choose from the agents listed above.
4. A stage marked "skipped" needs nothing from you - never route to it.
5. If you route to "database", ALWAYS follow it with "backend" (and "frontend" too if relevant)
   before "testing".
6. Run "testing" once backend/frontend stage_status is "done", before "deployment".
7. If quality_passed is LITERALLY false or there are outstanding issues, send it to whichever agent
   owns the failing file(s), then "testing" again, before "deployment".
8. If the deployment report describes a failure that traces to backend/frontend code, send it back
   there, then "testing", then "deployment" again.
9. Don't say "done" while any required stage is still "pending"/"failed", while quality_passed isn't
   LITERALLY true, or while the deployment report describes an unresolved failure.

Output ONLY a JSON object: {"next": "...", "reason": "..."}
```

**"Already given a real attempt this run" logic (added today):** `completed_nodes`/`failed_nodes` both start EMPTY at the top of every `--update` invocation, so "stage X is in `completed_nodes` AND `stage_status[X] == "done"`" reliably means "X actually ran and succeeded during THIS run" - not "was done from some earlier session." This is what stops rule 2 from re-triggering an already-satisfied stage forever, since the raw request text never changes between rounds.

**On LLM failure:** falls back to `_deterministic_fallback()` - walks `backend -> frontend -> testing -> deployment` in fixed order, picks the first non-done/non-skipped stage. No LLM call at all in this path - purely rules-based, guarantees a quota outage can't silently look like "finished."

**Model:** Gateway primary (`PLANNING` tier), Ollama -> Groq -> Mistral fallback.

---

## 9. Known gaps / things worth improving next

1. **`choose_stack_skill`'s prompt** (Planner) still unconditionally describes "React + Vite frontend" - now inaccurate for static-only projects. Consider branching it the same way Frontend's prompt was branched today.
2. **Testing's system prompt** has the same issue - "required stack... React + Vite frontend" is stated unconditionally, risking a false "wrong framework" complaint against a correctly-built static site. Not yet observed in practice, but the prompt text itself is inconsistent with what Frontend is now allowed to build.
3. **Frontend's over-generation risk under the ReAct emulation prototype** (tested today, not yet wired into the real pipeline): a text-based tool-call format can be followed correctly for format, but the model doesn't reliably know when to STOP - it invented an extra unrequested file after a task was already complete. Needs a hard step budget / explicit "do only what was asked" instruction if this path is ever built into `core/agent_runtime.py` for real.
4. **Backend's occasional redundant duplicate-write behavior** (calling `write_file` on the same path with identical content many times in one round before self-stopping) - self-terminates safely but wastes real time; not yet addressed.
5. **Deployment has never been reached in any live run tested this session** - always stalled earlier in the loop (backend/frontend/testing) before getting there. Its actual behavior against a real project is unverified in practice, though its code path and Dockerfile-generation logic have been read/reasoned through.
