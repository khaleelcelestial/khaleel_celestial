"""
ProjectState and all TypedDicts/Enums (Section 2 of spec)
This is the single source of truth for the graph's state schema.
"""

from typing import TypedDict, Annotated
from enum import Enum
import operator


# ---------- ENUMS ----------

class BuildStatus(Enum):
    PENDING = "pending"
    GENERATED = "generated"
    PATCHED = "patched"


class Severity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StageStatus(Enum):
    """Comprehensive stage status flags for granular tracking."""
    SKIPPED = "skipped"        # Not needed by execution plan
    PENDING = "pending"         # Needed but not started yet
    RUNNING = "running"         # Currently executing (RUN node)
    VALIDATING = "validating"   # In UT/VAL checks
    UPDATING = "updating"       # Fixing issues (RUN with feedback)
    DONE = "done"              # Passed all checks
    FAILED = "failed"          # Exhausted all attempts


# ---------- EXECUTION PLAN (typed, not a raw dict) ----------

class ExecutionPlan(TypedDict):
    database: bool
    contract: bool
    backend: bool
    frontend: bool
    release: bool


# ---------- STRUCTURED ISSUE OBJECT ----------

class Issue(TypedDict):
    severity: Severity
    file: str
    description: str
    suggested_fix: str


# ---------- REDUCERS ----------

def merge_dicts(left: dict, right: dict) -> dict:
    """Shallow-merge two dicts. Only safe for flat dicts (no nested sub-artifacts)."""
    return {**left, **right}


def _merge_workspace(left: dict, right: dict) -> dict:
    """
    Deep-merge two partial Workspace updates.

    LangGraph only applies the reducer declared on the top-level "project"
    channel; the Annotated[..., merge_dicts] hints on individual Workspace
    fields (backend/frontend/docs/...) are not separately honored. Without
    this, a node that returns e.g. {"workspace": {"docs": {...}}} would wipe
    out sibling keys like "backend"/"frontend" via the top-level shallow
    merge, since the whole "workspace" value gets replaced.
    """
    result = dict(left)
    for key in ("database", "contract", "backend", "frontend", "release"):
        if key in right:
            result[key] = {**result.get(key, {}), **right[key]}
    if "docs" in right:
        result["docs"] = {**result.get("docs", {}), **right["docs"]}
    return result


def merge_project(left: dict, right: dict) -> dict:
    """Reducer for the top-level "project" channel (see _merge_workspace)."""
    result = dict(left)
    for key in ("project_id", "requirements", "architecture"):
        if key in right:
            result[key] = right[key]
    if "tasks" in right:
        result["tasks"] = result.get("tasks", []) + right["tasks"]
    if "workspace" in right:
        result["workspace"] = _merge_workspace(result.get("workspace", {}), right["workspace"])
    return result


def merge_runtime(left: dict, right: dict) -> dict:
    """
    Reducer for the top-level "runtime" channel.

    Same problem as _merge_workspace: RuntimeState's per-field Annotated
    reducers (operator.add on logs/completed_nodes/failed_nodes/issue_history)
    are not honored by LangGraph on their own, so this reducer applies them
    explicitly. "review_issues" is intentionally overwritten each QA pass
    (it's the current snapshot, not an audit trail - see issue_history).
    """
    result = dict(left)
    for key in ("mode", "execution_plan", "quality_passed", "review_issues",
                "final_project_path", "current_stage", "retry_count",
                "next_agent", "supervisor_rounds", "deployment_status", "testing_report",
                "consecutive_agent_failures"):
        if key in right:
            result[key] = right[key]
    if "test_results" in right:
        result["test_results"] = {**result.get("test_results", {}), **right["test_results"]}
    if "stage_status" in right:
        # Shallow-merge by stage key so one node updating e.g. "backend" doesn't
        # wipe out "database"/"frontend" entries set by other nodes.
        result["stage_status"] = {**result.get("stage_status", {}), **right["stage_status"]}
    for key in ("issue_history", "logs", "completed_nodes", "failed_nodes"):
        if key in right:
            result[key] = result.get(key, []) + right[key]
    for key in ("stage_attempts", "stage_feedback", "stage_progress"):
        # Shallow-merge by stage key, same reasoning as stage_status: one
        # stage's RUN/UT/VAL node updating its own attempt count/feedback
        # must not wipe out another stage's entry.
        if key in right:
            result[key] = {**result.get(key, {}), **right[key]}
    return result


# ---------- WORKSPACE (typed sub-artifacts, each versioned) ----------

class DatabaseWorkspace(TypedDict):
    version: int
    schema: str
    status: BuildStatus


class ContractWorkspace(TypedDict):
    version: int
    openapi_spec: str
    status: BuildStatus


class BackendWorkspace(TypedDict):
    version: int
    files: dict[str, str]   # path -> file content
    status: BuildStatus


class FrontendWorkspace(TypedDict):
    version: int
    files: dict[str, str]
    status: BuildStatus


class ReleaseWorkspace(TypedDict):
    version: int
    package_manifest: dict
    status: BuildStatus


class Workspace(TypedDict):
    database: Annotated[DatabaseWorkspace, merge_dicts]
    contract: Annotated[ContractWorkspace, merge_dicts]
    backend: Annotated[BackendWorkspace, merge_dicts]
    frontend: Annotated[FrontendWorkspace, merge_dicts]
    docs: Annotated[dict, merge_dicts]
    release: Annotated[ReleaseWorkspace, merge_dicts]


# ---------- DOMAIN STATE ----------

class ProjectArtifacts(TypedDict):
    project_id: str  # stable identifier - directory name under output/, same across update runs
    requirements: str
    architecture: str
    tasks: Annotated[list[str], operator.add]
    workspace: Workspace


# ---------- RUNTIME STATE ----------

class RuntimeState(TypedDict):
    mode: str  # "build" (fresh project) or "update" (revise an existing project_id)
    execution_plan: ExecutionPlan
    quality_passed: bool | None  # None means "Testing hasn't run yet" - distinct from a real False
                                # (Testing ran and found a failure). See main.py's _base_runtime().
    review_issues: list[Issue]                          # CURRENT snapshot — overwrite each QA pass
    issue_history: Annotated[list[Issue], operator.add]  # AUDIT TRAIL — accumulates across all QA passes
    test_results: Annotated[dict, merge_dicts]
    logs: Annotated[list[str], operator.add]
    final_project_path: str

    # checkpoint / recovery metadata
    current_stage: str
    completed_nodes: Annotated[list[str], operator.add]
    failed_nodes: Annotated[list[str], operator.add]
    retry_count: int

    # supervisor workflow
    next_agent: str        # supervisor's routing decision: which agent runs next, or "done"
    supervisor_rounds: int  # absolute backstop counter - the supervisor decides when to
                           # stop, but this prevents a true infinite loop if it never does
    deployment_status: str  # last deployment agent report, so the supervisor can see
                           # whether a redeploy is still needed
    testing_report: str     # last testing agent's full free-text findings (static checks +
                           # LLM review + any real test run) - fed to the supervisor AND
                           # back to backend/frontend as fix feedback, not just a log line
    stage_status: dict[str, str]  # explicit per-stage tracking: "database"/"backend"/
                           # "frontend"/"testing"/"deployment" -> StageStatus enum values:
                           # "skipped"/"pending"/"running"/"validating"/"updating"/"done"/"failed"
                           # Set by Planner (using execution_plan), updated by each stage's RUN/UT/VAL
                           # nodes to show real-time progress, so users and supervisor can see exactly
                           # where the pipeline is at any moment
    consecutive_agent_failures: int  # how many agent calls IN A ROW returned ok=False (every
                           # provider/fallback exhausted) - reset to 0 by any successful agent
                           # call. Lets the Supervisor recognize "we're in a quota outage" and
                           # stop burning rounds retrying the same doomed call, instead of only
                           # noticing after MAX_SUPERVISOR_ROUNDS.
    stage_attempts: dict[str, int]  # per-stage RUN/UT/VAL retry counter (e.g. "backend" -> 2) -
                           # each stage's own RUN node increments its entry; UT/VAL nodes read
                           # it to decide "loop back to RUN again" vs "give up and report failed
                           # to the Supervisor" once MAX_STAGE_ATTEMPTS is hit. Reset to 0 by the
                           # Supervisor every time it freshly routes to that stage (a NEW attempt
                           # at the current request, not a continuation of an old one).
    stage_feedback: dict[str, str]  # per-stage self-heal feedback text (e.g. "backend" -> "main.py:11
                           # undefined name 'os'") - UT/VAL nodes write here on failure so the next
                           # RUN node (a SEPARATE graph step now, with no shared Python variable)
                           # can see exactly what to fix. Cleared by the Supervisor on a fresh route.
    stage_progress: dict[str, bool]  # per-stage "did THIS RUN attempt actually write/change anything?"
                           # - a RUN node (backend/frontend) sets this every time it runs; the
                           # conditional edge right after RUN reads it to bail straight back to
                           # the Supervisor (no point running UT/VAL on an attempt that made zero
                           # real changes) instead of looping.


# ---------- TOP-LEVEL STATE ----------

class ProjectState(TypedDict):
    user_request: str
    project: Annotated[ProjectArtifacts, merge_project]
    runtime: Annotated[RuntimeState, merge_runtime]
