"""
Project registry - lets a later pipeline run find and resume a previously
built project by id, so "update project X" doesn't have to start from
scratch or guess where its files live.

Each project's output directory (output/<project_id>/) carries two small
metadata files alongside the actual generated code:
  - .project.json          project_id, original user_request, execution_plan, timestamps
  - .workspace_snapshot.json   the full ProjectArtifacts["workspace"] dict, so a later
                                "update" run can reload every generated file.
"""

import json
import re
from pathlib import Path

# Same list/reasoning as skills/agent_tools.py's _EXCLUDED_DIRS (duplicated
# rather than imported to avoid coupling this module to agent_tools) - a
# venv/node_modules/etc. left inside backend/ or frontend/ must never be
# synced in as if it were generated project source, or static checks end up
# "finding" hundreds of errors inside someone else's third-party library.
_EXCLUDED_DIRS = ("node_modules", "venv", ".venv", "env", "__pycache__", ".git", ".pytest_cache")

# Anchored to this package (langgraph_pipeline/output), NOT the process's
# current working directory - a relative Path("output") would silently
# resolve to a different folder depending on where the entry point (CLI vs
# Streamlit, which may have a different launch cwd) was started from.
OUTPUT_BASE = Path(__file__).resolve().parent.parent / "output"

# Every project's docker-compose used to hardcode the same backend/frontend/db
# host ports, so only one project's containers could run at a time. This file
# assigns each project_id its own stable ports (kept across rebuilds/updates)
# so multiple projects can run simultaneously.
PORT_ALLOCATIONS_FILE = OUTPUT_BASE / ".port_allocations.json"
BASE_PORTS = {"backend": 8000, "frontend": 5173, "db": 5432}


def allocate_ports(project_id: str) -> dict:
    """Return {"backend": port, "frontend": port, "db": port} for this project_id, assigning new ones if needed."""
    OUTPUT_BASE.mkdir(exist_ok=True)

    allocations = {}
    if PORT_ALLOCATIONS_FILE.exists():
        try:
            allocations = json.loads(PORT_ALLOCATIONS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            allocations = {}

    if project_id in allocations:
        return allocations[project_id]

    used = {service: {p[service] for p in allocations.values()} for service in BASE_PORTS}
    ports = {}
    for service, base in BASE_PORTS.items():
        port = base
        while port in used[service]:
            port += 1
        ports[service] = port

    allocations[project_id] = ports
    PORT_ALLOCATIONS_FILE.write_text(json.dumps(allocations, indent=2), encoding="utf-8")
    return ports


def db_name_for(project_id: str) -> str:
    """A valid, unique Postgres database name derived from project_id."""
    sanitized = re.sub(r"[^a-z0-9_]", "_", project_id.lower()).strip("_") or "app"
    return f"db_{sanitized}"[:63]  # Postgres identifier length limit


def slugify(text: str, max_len: int = 50) -> str:
    slug = text[:max_len].strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")
    return slug or "project"


def project_dir_for(project_id: str) -> Path:
    return OUTPUT_BASE / project_id


def save_project_snapshot(project_dir: Path, project_id: str, user_request: str,
                          execution_plan: dict, requirements: str, architecture: str,
                          tasks: list, workspace: dict, stage_status: dict = None) -> None:
    import time

    project_dir = Path(project_dir)
    meta_path = project_dir / ".project.json"

    created_at = time.time()
    if meta_path.exists():
        try:
            existing = json.loads(meta_path.read_text(encoding="utf-8"))
            created_at = existing.get("created_at", created_at)
        except (json.JSONDecodeError, OSError):
            pass

    metadata = {
        "project_id": project_id,
        "user_request": user_request,
        "execution_plan": execution_plan,
        "requirements": requirements,
        "architecture": architecture,
        "tasks": tasks,
        "created_at": created_at,
        "updated_at": time.time(),
        # The exact per-stage status this run ended with (done/failed/
        # pending/skipped) - lets a later --update tell "built" apart from
        # "started but interrupted" instead of guessing from file presence
        # alone (a stage can have SOME files on disk and still be far from
        # done, e.g. a rate limit killed it mid-generation).
        "stage_status": stage_status or {},
    }

    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (project_dir / ".workspace_snapshot.json").write_text(
        json.dumps(workspace, indent=2, default=str), encoding="utf-8"
    )


def load_project_snapshot(project_id: str) -> dict | None:
    """Returns {"metadata": {...}, "workspace": {...}} or None if not found."""
    project_dir = project_dir_for(project_id)
    meta_path = project_dir / ".project.json"
    workspace_path = project_dir / ".workspace_snapshot.json"

    if not meta_path.exists() or not workspace_path.exists():
        return None

    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        workspace = json.loads(workspace_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    return {"metadata": metadata, "workspace": workspace}


def sync_workspace_from_disk(project_dir: Path, workspace: dict) -> dict:
    """
    Rebuild the in-memory workspace dict's file-backed fields from what's
    actually on disk. The supervisor workflow's agents write directly to
    disk via their file tools (so testing/deployment can run real commands
    against real files) - disk is the source of truth there, not the
    in-memory dict. This bridges back to it for anything that still expects
    a workspace dict: the project registry snapshot, docker_skills,
    generate_docs_skill, the Streamlit "Explore Files" tab.
    """
    project_dir = Path(project_dir)
    result = {k: (dict(v) if isinstance(v, dict) else v) for k, v in workspace.items()}

    for artifact_type in ("backend", "frontend"):
        artifact_dir = project_dir / artifact_type
        files = {}
        if artifact_dir.exists():
            for p in artifact_dir.rglob("*"):
                if p.is_file() and not any(part in _EXCLUDED_DIRS for part in p.parts) and not p.name.startswith("."):
                    try:
                        files[str(p.relative_to(artifact_dir)).replace("\\", "/")] = p.read_text(encoding="utf-8")
                    except (UnicodeDecodeError, OSError):
                        continue
        existing = result.get(artifact_type, {})
        result[artifact_type] = {
            "version": existing.get("version", 0) + (1 if files else 0),
            "files": files,
            "status": "generated" if files else existing.get("status", "pending"),
        }

    schema_file = project_dir / "schema.sql"
    if schema_file.exists():
        result["database"] = {
            "version": result.get("database", {}).get("version", 0) + 1,
            "schema": schema_file.read_text(encoding="utf-8"),
            "status": "generated",
        }

    contract_file = project_dir / "openapi.yaml"
    if contract_file.exists():
        result["contract"] = {
            "version": result.get("contract", {}).get("version", 0) + 1,
            "openapi_spec": contract_file.read_text(encoding="utf-8"),
            "status": "generated",
        }

    result.setdefault("docs", {})
    result.setdefault("release", {})
    return result


def list_projects() -> list[dict]:
    if not OUTPUT_BASE.exists():
        return []

    projects = []
    for entry in sorted(OUTPUT_BASE.iterdir()):
        if not entry.is_dir():
            continue
        meta_path = entry / ".project.json"
        if not meta_path.exists():
            continue
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        projects.append(metadata)

    return projects
