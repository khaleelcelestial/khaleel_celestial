# Fullstack AI Pipeline - Supervisor Workflow

A LangGraph application that takes a one-line request and builds, tests, and deploys a real
full-stack project (or just a backend, just a frontend, or even a single standalone file) -
generating PostgreSQL/SQLAlchemy + FastAPI + React by default, containerized with Docker.

## Architecture

```
user -> Planner -> Database -> Supervisor <-> {Backend, Frontend, Testing, Deployment}
```

**Planner** and **Database** run once, deterministically, right at the start ("build around the
database"): Planner classifies the request into an execution plan and extracts
requirements/tasks/architecture; Database generates the PostgreSQL schema and OpenAPI contract and
writes both straight to disk.

From there, the **Supervisor** is an LLM router - not a fixed sequence of conditional edges - that
looks at real state (`stage_status`, the Testing agent's report, the Deployment agent's report) and
decides which agent runs next, including sending work *back* to an earlier agent when something's
wrong. It loops until it decides everything the plan needs is built, tested with no outstanding
issues, and deployed (or a 20-round hard backstop kicks in).

**Backend**, **Frontend**, **Testing**, and **Deployment** are real LangGraph tool-calling agents
(`langgraph.prebuilt.create_react_agent`, wrapped in `core/agent_runtime.py`) - not single LLM calls:
- **Backend**/**Frontend** get scoped file read/write tools (`skills/agent_tools.py`) - write access
  is restricted to their own subdirectory, but read access covers the whole project, so Frontend can
  open Backend's actual code instead of only seeing an OpenAPI spec (the fix for a real bug: the
  frontend used to hardcode a port that didn't match the backend's actual one).
- **Testing**/**Deployment** additionally get an allowlisted terminal (`pytest`, `npm test`,
  `docker compose ...` - nothing else, no shell chaining) and deliberately have **no write access** -
  they verify and deploy, they don't silently patch code. If they find a problem, it goes back
  through state (`testing_report`, `deployment_status`) and the Supervisor routes the fix to whichever
  agent owns that file.

Every agent's real actions - reads, writes, static checks, LLM review, commands run - are printed
live to the console as they happen, not just summarized at the end.

## Model assignment

Two accounts, four real model tiers each (Groq `llama-3.1-8b-instant`/`llama-3.3-70b-versatile`,
Mistral `mistral-small-latest`/`mistral-large-latest`) - see `core/model_config.py`. Assignment is by
workload, staggered across accounts for agents that run close together in time (so a single
project's run naturally spreads load across both quota pools):

| Agent | Model | Why |
|---|---|---|
| Planner (4 calls) | staggered across both accounts/providers | spreads one planning phase's load |
| Database | `small` | structured/templated SQL+OpenAPI generation |
| Supervisor | `versatile` | runs every round, needs consistent reasoning |
| Backend / Frontend | `versatile` | heaviest, most iterative code-gen agents |
| Testing / Deployment | `versatile` | diagnostic reasoning over real command output |
| `review_code` (Testing's tool) | `large` | a genuinely different second opinion |
| `classify_intent` (simple-file fast path) | `instant` | cheap/fast, high frequency |

## Installation

```bash
cd langgraph_pipeline
.\venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Fill in `.env` (copy `.env.example`):
- `GROQ_API_KEY_A1`/`MISTRAL_API_KEY_A1`, `GROQ_API_KEY_A2`/`MISTRAL_API_KEY_A2` - at minimum one
  Groq + one Mistral key
- `POSTGRES_USER`/`POSTGRES_PASSWORD` - reused as the login for every generated project's own,
  separate Postgres database (name + port are auto-assigned per project, never shared)

Run `python verify_all.py` to confirm everything's wired up correctly.

## Usage

```bash
python main.py                                    # interactive mode - prompts each time
python main.py "Build a full-stack todo app"       # one-shot build
python main.py --update <project_id> "add X"       # revise an existing project in place
python main.py --list-projects                     # see what's in output/
streamlit run streamlit_app.py                     # web UI - same entry points, same registry
```

A request that's just "write me a .txt file explaining X" is detected (`classify_intent`) and
handled directly, skipping the whole agent workflow.

## State (`core/state.py`)

Cross-agent coordination happens entirely through `ProjectState` - no side channel:

- `project`: `project_id`, `requirements`, `architecture`, `tasks`, `workspace` (database/contract/
  backend/frontend/docs/release - backend/frontend `files` dicts are a disk-synced mirror, since
  agents write directly to `output/<project_id>/` via their file tools, not just to this dict)
- `runtime.stage_status`: explicit per-stage tracking (`"skipped"`/`"pending"`/`"done"`/`"failed"`
  for database/backend/frontend/testing/deployment) - set right after Planner from the execution
  plan alone, so "this project has no database" is visible in state from step one
- `runtime.testing_report` / `runtime.deployment_status`: the Testing/Deployment agents' full
  free-text findings, fed back to the Supervisor and to Backend/Frontend as fix feedback
  `runtime.next_agent` / `runtime.supervisor_rounds`: the Supervisor's routing decision and loop
  counter

## Project structure

```
langgraph_pipeline/
├── main.py                   # CLI entry point (build/update/list-projects/interactive)
├── streamlit_app.py          # web UI - same entry points, same registry
├── verify_all.py             # environment/wiring sanity check
├── visualize_graph.py        # regenerates graph_builder_image/langgraph_pipeline.png
├── core/                     # shared infrastructure every other package depends on
│   ├── state.py               # ProjectState schema + reducers (single source of truth)
│   ├── graph.py                # StateGraph wiring - planner -> database -> supervisor loop
│   ├── agent_runtime.py        # create_react_agent wrapper with provider-fallback resilience
│   ├── model_router.py / model_config.py   # LangChain chat model routing (Groq/Mistral, 2 accounts)
│   ├── logger.py               # console logging (node/stage/tool-call visibility)
│   └── load_env.py             # .env loading + Windows UTF-8 console fix
├── capabilities/             # one real agent per file
│   ├── planner.py / database.py        # deterministic; database also re-enters the Supervisor
│   │                                    # loop on demand for schema/contract revisions
│   ├── supervisor.py                   # LLM router + the loop
│   └── backend.py / frontend.py / testing.py / deployment.py   # tool-calling agents
├── skills/
│   ├── agent_tools.py         # scoped file tools (incl. delete_file) + allowlisted terminal + static checks
│   ├── planning_skills.py / database_skills.py / contract_skills.py / quality_skills.py
│   ├── docker_skills.py       # Dockerfile/compose generation (deployment itself is agentic)
│   ├── project_registry.py    # project_id/ports/db-name allocation, disk<->state sync, snapshots
│   ├── simple_task_skills.py  # the "just write a file" fast path
│   └── release_skills.py      # packaging for the simple-file fast path
├── tools/                    # thin typed wrappers around the planning/database/contract skills
├── tests/test_canonical_requests.py
├── graph_builder_image/      # architecture diagram(s) for docs
└── output/                   # generated projects (each with its own Dockerfiles, .env, etc.)
```

## Verification

```bash
python verify_all.py                              # imports, graph structure, keys, Docker, Postgres
pytest tests/test_canonical_requests.py -v         # 5 canonical request shapes (real LLM calls)
```
