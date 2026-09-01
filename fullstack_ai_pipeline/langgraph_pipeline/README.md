# Fullstack AI Pipeline

A LangGraph state machine that takes a single natural-language request and builds, tests, and
deploys a real full-stack application — PostgreSQL + FastAPI/SQLAlchemy backend + React/Vite
frontend, containerized with Docker — with zero human-written code. It also supports `--update`:
handing the same project a follow-up request (a bug fix, a new feature, a design change) and
having the pipeline modify only what's needed, without starting over.

This document is the single source of truth for how the pipeline works: the graph, every agent,
how they talk to each other, and the guardrails that keep an LLM-driven build loop from running
away with itself.

---

## 1. The mental model

```
Fresh build:   Planner → Database → Supervisor ⇄ {Backend, Frontend, Testing, Deployment} → done
--update mode:                      Supervisor ⇄ {Backend, Frontend, Testing, Deployment} → done
```

One user request drives one LangGraph `StateGraph` with **17 real work nodes** (19 counting the
graph's implicit start/end): a `planner`, a `supervisor`, and 5 **stages** — `database`, `backend`,
`frontend`, `e2e` (testing), `cicd` (deployment) — each internally split into three steps:

- **RUN** — generate or change code/config for this stage
- **UT** — deterministic, code-based self-check (does this even parse/build?)
- **VAL** — deterministic, code-based contract check (does this actually satisfy what the rest of
  the app needs from it?)

A stage that fails UT or VAL retries **up to 3 times**, feeding the specific failure back into the
next attempt's prompt, before it's marked `failed` and control returns to the Supervisor. The
Supervisor decides which stage runs next — it never writes code itself.

**On `--update`, the Planner is skipped entirely.** The graph enters straight at the Supervisor,
reusing the saved plan/architecture from `.project.json`, and only resets the stages the new
request actually touches.

---

## 2. Agents, in pipeline order

### Planner (`capabilities/planner.py`)
Runs once, only on a fresh build — never on `--update`. Makes 4 sequential plain-text/JSON LLM
calls (no tools, no file access):

1. **Analyze request** → an execution plan of 5 booleans (`database`, `contract`, `backend`,
   `frontend`, `release`) — e.g. "just a static landing page" sets `database=false`,
   `backend=false`.
2. **Extract requirements** → actors/features/user flows as text.
3. **Create task list** → a concrete JSON list of build tasks.
4. **Choose stack** — the model does **not** actually choose a stack. It's fixed: PostgreSQL +
   Python/FastAPI/SQLAlchemy + React/Vite, always. The LLM only maps the request onto that fixed
   stack (which tables, which pages) — a deliberate consistency decision, not a limitation.

Creates `output/<project_id>/` and sets every stage's starting `stage_status` (`pending` or
`skipped`) from the execution plan.

### Supervisor (`capabilities/supervisor.py`)
The router. Runs after every stage finishes (and is the entry point on every `--update`). Makes
one LLM call per round to decide "what's next," but the answer is heavily fenced by deterministic
guardrails:

- Won't let backend/frontend start before the database stage is done (if a database is needed).
- Won't let testing start before database/backend/frontend are all done.
- Won't let deployment start before testing is done.
- **Anti-loop guard**: if the LLM tries to re-send a stage that already passed *this run*, that's
  only honored if the stated reason names a genuinely still-open issue — otherwise it's
  overridden. This exists because stale LLM reasoning was previously causing infinite re-loops.
- **Hard backstops**: 20 supervisor rounds max; 3 consecutive total-provider-outage failures max.

### Database (`capabilities/database.py`)
Single-shot LLM generation (no tools). Produces `schema.sql` (idempotent
`CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` style — see §5 for why)
and `openapi.yaml`, the API contract both Backend and Frontend build against.

- **UT**: SQL syntax scan, foreign-key validity, duplicate-table detection.
- **VAL**: does every table have corresponding coverage in the API contract.

### Backend (`capabilities/backend.py`)
Generates the FastAPI app. **Single-shot batch generation** — one prompt, one response parsed for
`@@@FILE@@@` / `@@@DELETE@@@` blocks (`skills/batch_codegen.py`) — not tool-calling, not a
multi-turn agent (see §4 for why that matters).

- **UT**: `ast`-parse, undefined-name checks, cross-file import checks, `__all__`-export-list
  awareness, regex-literal-aware bracket balancing.
- **VAL**: does the code implement every route the OpenAPI contract declares.

### Frontend (`capabilities/frontend.py`)
Same single-shot batch-generation style as Backend, producing a React + Vite app (or plain static
HTML/CSS/JS if no backend is needed). On `--update`, uses **deterministic scope selection**
(`skills/incremental_codegen.py`) to decide which small subset of existing files a change actually
needs, instead of re-sending the whole project every time.

- **UT**: bracket-balance + cross-file import/export resolution, `package.json` dependency checks,
  duplicate-router-mount detection, build validation (real `npm`/`vite` build).
- **VAL**: multiple focused validators run together — contract matching (every API call matches a
  real backend route), navigation reachability (every page is actually linked to), auth-gating
  (a defined route guard is actually rendered, not just declared).

### Testing / E2E (`capabilities/e2e.py`)
The first stage that's a **real tool-calling ReAct agent** (`core/agent_runtime.py`,
`langgraph.prebuilt.create_react_agent`) — it reads files, runs static checks, gets a second LLM's
code review, and runs `docker compose up` as a real first boot attempt. **No write access** — it
verifies, it doesn't patch code.

- **UT**: static checks clean + boot succeeded.
- **VAL**: backend/frontend actually HTTP-reachable, no unresolved review findings.

### Deployment / CI-CD (`capabilities/cicd.py`)
Dockerfiles, `docker-compose.yml`, `.env` files, and port allocation are **100% deterministic
Python** (`skills/docker_skills.py`) — no LLM involved, so the parts of deployment that must be
exactly reproducible never depend on an LLM getting creative. Only the last step — actually
running `docker compose up --build -d`, running `docker compose ps`, and diagnosing failures from
real logs — is a tool-calling agent (read + run-command only, still no write access).

- **UT**: did `docker compose up --build -d` actually exit 0.
- **VAL**: every container running + healthy, real HTTP GET against backend and frontend ports.

**Deployment is local Docker Compose only** — this pipeline does not push to a registry or deploy
to any cloud target. "Deployment" means: containers built and running on your machine,
health-checked, reachable over HTTP.

---

## 3. What each stage writes, and why

| File | Written by | Why |
|---|---|---|
| `.project.json`, `.workspace_snapshot.json` | Every node, after every round | Full resumable snapshot — `--update` and crash-recovery both reload from this |
| `schema.sql`, `openapi.yaml` | Database stage | Source of truth both Backend and Frontend build against |
| `backend/*` | Backend stage | Implements the contract |
| `frontend/*` | Frontend stage | Consumes the contract + Backend's real routes |
| `backend/Dockerfile`, `frontend/Dockerfile`, `docker-compose.yml` | Pipeline code directly (`skills/docker_skills.py`), not the LLM | Deterministic templates keyed off the detected stack — no LLM variance in how containers are built |
| `start.sh` / `start.bat` | Pipeline code | Non-Docker fallback (`pip install ... && uvicorn ...`) |
| `README.md`, `API.md` (inside the generated project) | Deployment stage | Docs for the generated app, not this repo |

Neither `.project.json` nor `.workspace_snapshot.json` are part of the *generated app* — they're
the pipeline's own bookkeeping, invisible to the deployed product.

---

## 4. Architecture decisions worth understanding

### Why RUN → UT → VAL, with bounded retries
A single generate-and-hope step has no way to catch its own mistakes. Splitting every stage into
generate → deterministic self-check → deterministic contract-check means nothing moves forward
until it's actually been verified — by code, not by an LLM's opinion of its own output. Retries
are capped at 3 attempts per stage per round specifically because an uncapped retry loop against a
persistent failure burns cost indefinitely with no guarantee of ever converging; after 3 failures
the stage is flagged `failed` so the failure is visible instead of silently spinning forever.

### Why tool-calling was replaced with single-shot generation for Backend/Frontend
Backend and Frontend generation used to be `create_react_agent` tool-calling loops. LangGraph's
ReAct loop resends the **entire accumulated conversation** — system prompt, every prior tool call,
every prior tool result — on every internal step. For an N-step exchange, total token cost grows
roughly with N², not N. Measured live: a single round hit 330,000–460,000 input tokens against
~9,000 tokens of actual unique file content. The fix was architectural, not a bigger step budget:
Backend/Frontend generation is now **scope selection (free or one cheap call, paths only) → read
the selected files (a dict lookup, not a tool call) → one single generation call → deterministic
write**. Testing and Deployment deliberately keep the ReAct/tool-calling shape, because their
steps are genuinely unpredictable ahead of time (e.g. "check `docker compose ps`, then read logs
only for whichever service is unhealthy") — that's the one case tool-calling is the right shape
for.

### Why scope selection, and why it must never be silently empty
Once generation calls stopped seeing the whole project, something had to decide which few files a
given change actually needs (`skills/incremental_codegen.py`): a file named literally in
validator/UT feedback (free), a Project Index keyword match (free), or — only if both come up
empty — one cheap LLM call over file *paths only*, never content. If every signal comes up empty,
scope selection falls back to a small, bounded default (the smallest N files) rather than an empty
set — a generation call that runs with **zero visibility into the existing app** has nothing to
ground it and will invent structure from scratch, which has real, observed potential to overwrite
working code wholesale. This is why the emptiness check runs on the final selected-files dict, not
on an earlier candidate list that looked non-empty before path matching.

### Why `schema.sql` instead of Alembic-style migrations
This pipeline regenerates the database stage on nearly every request, and an LLM re-deriving a
*correct, consistent, numbered diff* against unknown migration history is a much harder and more
error-prone target than "rewrite this whole idempotent file to reflect the new requirement." One
whole-schema file with `CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` statements is
inherently safer to retry (worst case, a no-op) and doesn't depend on migration history staying in
sync. The tradeoff, openly: no rollback story, no per-change audit trail, and destructive changes
(safely renaming a column without data loss) are harder to express than Alembic's explicit
operations. For a long-lived, hand-maintained codebase with many engineers, Alembic would usually
be the better choice — this pipeline's shape (stateless whole-app regeneration) is a different
problem.

### Why multiple focused validators instead of one big check
A single pass/fail check can't distinguish "the API contract doesn't match" from "this page exists
but nothing links to it" from "an auth guard is defined but never rendered" — each needs different
detection logic and produces a different, specific fix. VAL runs several independent validators
(contract, navigation reachability, auth-gating, migration safety, container health, etc.), each
owning one concern, so failures are specific enough for the next generation attempt to actually
act on.

### Why snapshots and checkpoints
Every node persists a snapshot after every round (`.project.json`, `.workspace_snapshot.json`, and
a LangGraph `SqliteSaver` checkpoint DB at `output/.checkpoints.sqlite`). This is what makes
`--update` and crash-resume possible, and it's also the recovery path when a generation round
damages or deletes files it shouldn't have — the last known-good checkpoint can be restored rather
than the damage being permanent.

---

## 5. Model routing

Every skill is tagged with a `TaskTier` (`core/model_config.py`):

| Tier | Used by | Needs |
|---|---|---|
| `PLANNING` | Planner's 4 calls, Supervisor routing | Plain text/JSON, no tools |
| `CODING` | Backend/Frontend generation, scope selection | Reliable structured code output |
| `STRUCTURED` | SQL, OpenAPI, docs, code review | Templated/code-adjacent, no tools |
| `REASONING` | Testing/Deployment agents | Real tool-calling |
| `UTILITY` | Cheap routing/classification calls | Fast, low-stakes |

Azure OpenAI (`gpt-5.4-mini`) is primary for every skill. If it's unreachable, each skill falls
back automatically through every other configured credential — multiple Mistral and Groq accounts
— before a call is ever allowed to fail outright. See `.env.example` for the full credential setup
and the minimum configuration needed to run the pipeline at all.

---

## 6. Observability

Every LLM call's duration, input/output token counts, context size, and per-round summary are
printed live to the console as they happen (`core/logger.py`), not just written to a log file —
this is what makes it possible to watch a run's real cost as it happens instead of discovering it
after the fact. A structured JSONL event log per run is also written to `logs/runs/<run_id>/`, for
programmatic inspection of validator results, file changes, and LLM call metadata.

---

## 7. Repository layout

```
core/
  graph.py          — builds the StateGraph (17 real nodes: planner, supervisor, 5×(run/ut/val))
  stage_loop.py      — shared RUN→UT→VAL routing logic, existing_files_context(), checkpoint()
  state.py          — ProjectState / RuntimeState / Workspace shape and reducers
  model_router.py    — ModelRouter, provider clients
  model_config.py    — TaskTier, SKILL_MODEL_MAP, credential pool, fallback ordering
  agent_runtime.py   — run_tool_agent() (ReAct loop — Testing/Deployment only)
  logger.py         — StageTracker, live console + JSONL observability

capabilities/        — one file per graph stage
  planner.py, supervisor.py, database.py, backend.py, frontend.py, e2e.py, cicd.py

skills/              — the actual work each capability calls into
  planning_skills.py        — Planner's 4 LLM calls
  database_skills.py        — schema generation/validation
  contract_skills.py        — OpenAPI generation
  batch_codegen.py          — single-shot generation + @@@FILE@@@ parsing (Backend + Frontend)
  incremental_codegen.py    — deterministic scope selection for --update
  generation_strategy.py    — batch vs. incremental strategy decision
  project_index.py          — lightweight cross-file index + structure summary
  quality_skills.py         — shared static checks (bracket balance, imports, etc.)
  be_validators.py, fe_validators.py, db_validators.py, e2e_validators.py, cicd_validators.py
                             — the focused VAL-step validators for each stage
  be_startup_validator.py, db_postgres_validator.py, fe_build_validator.py, fe_browser_validator.py
                             — deeper runtime-level checks (real boot/build/browser checks)
  design_system.py          — pipeline-authored CSS (not LLM-generated)
  agent_tools.py            — tool definitions for the ReAct agents (Testing/Deployment)
  docker_skills.py          — Dockerfile/compose/start-script generation, container health checks
  e2e_skills.py             — E2E stage helpers, HTTP-reachability checks
  project_registry.py       — project snapshot save/load, workspace sync
  acceptance_test_generator.py — shared pytest-writing capability, reused across stages

tools/                — thin tool wrappers exposed to the ReAct agents
tests/                 — unit tests (fast, deterministic — see below)
streamlit_app.py       — UI for launching and monitoring pipeline runs
main.py               — CLI entry point (fresh build and --update)
verify_all.py          — sanity-checks the compiled graph's node/edge structure
visualize_graph.py     — renders the graph to a PNG
```

---

## 8. Running it

```bash
# one-time setup
pip install -r requirements.txt
cp .env.example .env   # fill in at least one Groq + Mistral account, or Azure OpenAI

# fresh build
python main.py "Build a full-stack task management app with user login, PostgreSQL, FastAPI, React"

# follow-up change to an existing project
python main.py --update <project_id> "Add a due-date field to tasks"

# or launch the Streamlit UI to run/monitor either of the above interactively
streamlit run streamlit_app.py
```

Generated projects land in `output/<project_id>/` — a complete, runnable app with its own
`docker-compose.yml`, `start.sh`/`start.bat`, and docs, independent of this pipeline's own code.

### Tests

```bash
# fast, deterministic unit tests
pytest tests/test_logger.py tests/test_incremental_codegen.py tests/test_quality_skills.py \
       tests/test_project_index.py tests/test_fe_validators.py

# NOT this — test_canonical_requests.py makes 5 real full-pipeline LLM builds and is slow
pytest tests/
```
