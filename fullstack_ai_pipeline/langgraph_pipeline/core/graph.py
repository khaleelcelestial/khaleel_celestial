"""
LangGraph State Graph wiring - Supervisor workflow.

Planner runs once, deterministically, right at the start. From there, a
Supervisor node routes between 5 stages - Database, Backend, Frontend, E2E
Testing, CI/CD Deployment - each now a real RUN -> UT -> VAL sub-graph (three
nodes with real conditional edges, not a Python for-loop hidden inside one
node), matching the documented architecture diagram exactly. The Supervisor
only ever routes to a stage's RUN node; the UT/VAL retry loop is internal to
that stage and only hands control back to the Supervisor once it passes or
exhausts MAX_STAGE_ATTEMPTS (see core/stage_loop.py). Update mode (revising
an existing project) skips straight to the Supervisor loop.
"""

import sqlite3
from pathlib import Path

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from core.state import ProjectState
from core.stage_loop import route_after_run, route_after_ut, route_after_val

from capabilities.planner import PlannerCapability
from capabilities.database import DatabaseCapability
from capabilities.supervisor import SupervisorCapability
from capabilities.backend import BackendCapability
from capabilities.frontend import FrontendCapability
from capabilities.e2e import E2ECapability
from capabilities.cicd import CICDCapability

# The Supervisor reasons in logical stage names (matching stage_status keys:
# "database"/"backend"/"frontend"/"testing"/"deployment" - unchanged from
# before, so supervisor.py's prompt/rules needed no rewrite at all) but the
# graph itself is entered at each stage's RUN node under the diagram's own
# naming. This is the only place that needs to know both vocabularies.
STAGE_ENTRY_NODE = {
    "database": "database_run",
    "backend": "backend_run",
    "frontend": "frontend_run",
    "testing": "e2e_run",
    "deployment": "cicd_run",
}

# stage name -> (run_node, ut_node, val_node)
STAGES = {
    "database": ("database_run", "database_ut", "database_val"),
    "backend": ("backend_run", "backend_ut", "backend_val"),
    "frontend": ("frontend_run", "frontend_ut", "frontend_val"),
    "testing": ("e2e_run", "e2e_ut", "e2e_val"),
    "deployment": ("cicd_run", "cicd_ut", "cicd_val"),
}


def route_entry(state: ProjectState) -> str:
    """Fresh build starts at Planner; revising an existing project skips straight to the Supervisor loop."""
    return "supervisor" if state["runtime"].get("mode") == "update" else "planner"


def route_after_supervisor(state: ProjectState) -> str:
    """The Supervisor's own decision drives control flow - not a fixed set of conditional edges."""
    next_agent = state["runtime"].get("next_agent", "done")
    if next_agent == "done":
        return END
    return STAGE_ENTRY_NODE.get(next_agent, END)


def _make_run_router(stage, ut_node):
    return lambda state: route_after_run(state, stage, ut_node)


def _make_ut_router(stage, run_node, val_node):
    return lambda state: route_after_ut(state, stage, run_node, val_node)


def _make_val_router(stage, run_node):
    return lambda state: route_after_val(state, stage, run_node)


def build_graph() -> StateGraph:
    """Build and compile the LangGraph StateGraph."""

    graph = StateGraph(ProjectState)

    planner = PlannerCapability()
    database = DatabaseCapability()
    supervisor = SupervisorCapability()
    backend = BackendCapability()
    frontend = FrontendCapability()
    e2e = E2ECapability()
    cicd = CICDCapability()

    graph.add_node("planner", planner.run)
    graph.add_node("supervisor", supervisor.run)

    graph.add_node("database_run", database.run)
    graph.add_node("database_ut", database.check_ut)
    graph.add_node("database_val", database.check_val)

    graph.add_node("backend_run", backend.run)
    graph.add_node("backend_ut", backend.check_ut)
    graph.add_node("backend_val", backend.check_val)

    graph.add_node("frontend_run", frontend.run)
    graph.add_node("frontend_ut", frontend.check_ut)
    graph.add_node("frontend_val", frontend.check_val)

    graph.add_node("e2e_run", e2e.run)
    graph.add_node("e2e_ut", e2e.check_ut)
    graph.add_node("e2e_val", e2e.check_val)

    graph.add_node("cicd_run", cicd.run)
    graph.add_node("cicd_ut", cicd.check_ut)
    graph.add_node("cicd_val", cicd.check_val)

    # Fresh build: Planner -> Supervisor, which routes to Database (or
    # straight to Backend/Frontend if the plan needs no database) like any
    # other stage - Planner does NOT call database_run directly. Update
    # mode: straight to Supervisor (see route_entry).
    graph.set_conditional_entry_point(route_entry, {"planner": "planner", "supervisor": "supervisor"})
    graph.add_edge("planner", "supervisor")

    # Every stage follows the same RUN -> UT -> VAL shape:
    # RUN -> (no progress) -> supervisor ; RUN -> (progress) -> UT
    # UT -> (pass) -> VAL ; UT -> (fail, attempts left) -> RUN ; UT -> (fail, exhausted) -> supervisor
    # VAL -> (pass) -> supervisor (done) ; VAL -> (fail, attempts left) -> RUN ; VAL -> (fail, exhausted) -> supervisor
    for stage, (run_node, ut_node, val_node) in STAGES.items():
        graph.add_conditional_edges(
            run_node, _make_run_router(stage, ut_node),
            {ut_node: ut_node, "supervisor": "supervisor"},
        )
        graph.add_conditional_edges(
            ut_node, _make_ut_router(stage, run_node, val_node),
            {val_node: val_node, run_node: run_node, "supervisor": "supervisor"},
        )
        graph.add_conditional_edges(
            val_node, _make_val_router(stage, run_node),
            {run_node: run_node, "supervisor": "supervisor"},
        )

    graph.add_conditional_edges("supervisor", route_after_supervisor, {
        **{node: node for node in STAGE_ENTRY_NODE.values()},
        END: END,
    })

    # Disk-backed checkpointer, not MemorySaver: LangGraph checkpoints state
    # after EVERY node execution regardless (that's what a checkpointer is
    # for), but MemorySaver only ever held that in RAM - a killed/crashed
    # process lost it all, confirmed today by direct testing (a run that had
    # a fully-passing database and frontend, killed before deployment, left
    # nothing --update could resume from). SQLite makes every one of those
    # per-node checkpoints durable across a crash or a hard kill, with no
    # per-node code needed anywhere else - it's automatic, built into
    # graph.compile(checkpointer=...).
    checkpoint_db = Path(__file__).resolve().parent.parent / "output" / ".checkpoints.sqlite"
    checkpoint_db.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(checkpoint_db), check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    checkpointer.setup()
    app = graph.compile(checkpointer=checkpointer)

    return app


# Export the compiled app
app = build_graph()
