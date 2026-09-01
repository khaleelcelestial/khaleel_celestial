"""
CI/CD deployment-quality helpers - Rollback, artifact pre-flight
validation, and deployment metadata. Added to STRENGTHEN the existing,
already-correct CICD_RUN/UT/VAL lifecycle (capabilities/cicd.py) - not a
redesign. See that module's docstring for what's deliberately left
untouched (schema-apply-to-live-db, container health polling, dynamic
expected-services detection - all already correct before this file
existed).

Deliberately NOT built here (confirmed not needed before writing any
code):
- Smarter endpoint derivation (OpenAPI-driven /health paths) - the
  existing http_reachable() already treats any non-5xx response as "the
  process is alive and responding", which reliably distinguishes a
  crashed/unreachable service from a working one without needing to guess
  the exact right path. Marginal value, real complexity - skipped.
- Source-code/static/pytest/Vitest/Playwright re-checks of any kind - all
  already done earlier (DB/BE/FE/E2E's own UT/VAL) - re-running them here
  would be exactly the duplicated work this enhancement is scoped to avoid.
"""

import subprocess
from pathlib import Path

from core.logger import get_logger


# ============================================================================
# Rollback - Docker image tagging, not database rollback (see module
# docstring and DATABASE ROLLBACK SAFETY section below). Fast (no rebuild)
# because it just re-points the ":latest" tag at the image that was already
# running before this round's build, then brings THAT image back up.
# ============================================================================

_ROLLBACK_TAG = "rollback"


def _image_name(project_dir: Path, service: str) -> str:
    # Matches Docker Compose's own default build-image naming convention
    # (confirmed against real `docker compose ps --format json` output
    # during this session's E2E work: "Image": "<project>-<service>").
    return f"{project_dir.name}-{service}"


def snapshot_images_for_rollback(project_dir: Path, services: list) -> list:
    """
    Before rebuilding, tags each service's CURRENTLY-running image (if one
    exists - a fresh first-ever deploy has nothing to snapshot) as
    ":rollback", a separate reference the upcoming `docker compose
    up --build` won't touch. Returns the list of services that were
    genuinely snapshotted (i.e., this really is an update to something
    already deployed, not a first deploy) - rollback is only ever
    attempted later for services in this list.
    """
    snapshotted = []
    for service in services:
        image = _image_name(project_dir, service)
        try:
            inspect = subprocess.run(
                ["docker", "image", "inspect", f"{image}:latest"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
            )
            if inspect.returncode != 0:
                continue  # no existing image for this service - nothing to snapshot (first deploy)
            tag = subprocess.run(
                ["docker", "tag", f"{image}:latest", f"{image}:{_ROLLBACK_TAG}"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
            )
            if tag.returncode == 0:
                snapshotted.append(service)
        except (subprocess.TimeoutExpired, OSError):
            continue
    return snapshotted


def rollback_to_previous(project_dir: Path, snapshotted_services: list) -> tuple:
    """
    Restores each snapshotted service's previous image and brings the
    stack back up - NO rebuild (the old image already exists locally), so
    this is fast. Does NOT touch the database's data/schema at all (see
    DATABASE ROLLBACK SAFETY - container/image rollback is not the same
    thing as database rollback, and this pipeline's schema changes are
    additive-only by construction, so there is nothing destructive to
    reverse there).

    Returns (rollback_ok, detail_message).
    """
    logger = get_logger()
    if not snapshotted_services:
        return False, "no previous deployment was recorded to roll back to (this looks like a first deploy)"

    try:
        down = subprocess.run(
            ["docker", "compose", "stop"] + snapshotted_services, cwd=str(project_dir),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        if down.returncode != 0:
            logger.warning(f"Rollback: could not stop current containers cleanly: {down.stderr[-300:]}")
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"could not stop the current (broken) containers: {str(e)[:200]}"

    for service in snapshotted_services:
        image = _image_name(project_dir, service)
        try:
            retag = subprocess.run(
                ["docker", "tag", f"{image}:{_ROLLBACK_TAG}", f"{image}:latest"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
            )
            if retag.returncode != 0:
                return False, f"could not restore the previous image for '{service}': {retag.stderr[-300:]}"
        except (subprocess.TimeoutExpired, OSError) as e:
            return False, f"could not restore the previous image for '{service}': {str(e)[:200]}"

    try:
        up = subprocess.run(
            ["docker", "compose", "up", "-d", "--no-build"] + snapshotted_services, cwd=str(project_dir),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        if up.returncode != 0:
            return False, f"restored the previous image(s) but failed to start them: {up.stderr[-300:]}"
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"restored the previous image(s) but failed to start them: {str(e)[:200]}"

    from skills.docker_skills import check_containers_healthy
    health = check_containers_healthy(project_dir, snapshotted_services, timeout_s=60)
    if not health["passed"]:
        return False, f"restored the previous image(s) but they didn't come back healthy: {health['detail']}"

    return True, f"rolled back and verified healthy: {', '.join(snapshotted_services)}"


# ============================================================================
# Deployment artifact pre-flight validation - fail fast with a clear
# message instead of a raw, cryptic `docker compose up` error partway
# through an expensive build. Only checks artifacts APPLICABLE to this
# specific project (backend-only, frontend-only, full-stack, etc.).
# ============================================================================

def validate_deployment_artifacts(project_dir: Path, has_backend: bool, has_frontend: bool,
                                  has_database: bool) -> list:
    """Returns a list of problem strings (empty = every artifact this
    project actually needs is present). Does NOT re-check source code
    correctness (that's DB/BE/FE_UT's job, already done) - only that the
    deployment-time files those stages/CICD_RUN are supposed to have
    produced actually exist on disk."""
    problems = []

    if has_backend:
        if not (project_dir / "backend" / "Dockerfile").exists():
            problems.append("backend/Dockerfile is missing - cannot build the backend image")
        if not any((project_dir / "backend" / name).exists()
                  for name in ("requirements.txt", "pyproject.toml", "package.json")):
            problems.append("backend has no requirements.txt/pyproject.toml/package.json - "
                            "the image build will fail to install dependencies")

    if has_frontend:
        if not (project_dir / "frontend" / "Dockerfile").exists():
            problems.append("frontend/Dockerfile is missing - cannot build the frontend image")
        if (project_dir / "frontend" / "package.json").exists() is False and \
                not (project_dir / "frontend" / "index.html").exists():
            problems.append("frontend has neither package.json nor index.html - nothing to serve")

    if has_database and not (project_dir / "schema.sql").exists():
        problems.append("schema.sql is missing but a database was planned - the db container "
                        "would start with no schema")

    if has_backend or has_frontend:
        if not (project_dir / "docker-compose.yml").exists():
            problems.append("docker-compose.yml is missing - cannot bring up the stack")

    return problems


# ============================================================================
# Deployment metadata - structured record of what actually got deployed,
# for the Supervisor/final pipeline output to report - not previously
# captured anywhere except as free-text deployment_status.
# ============================================================================

def collect_deployment_metadata(project_dir: Path, host_ports: dict, container_result: dict,
                                has_backend: bool, has_frontend: bool, has_database: bool,
                                rollback_available: bool) -> dict:
    """Builds the structured deployment record - only includes URL fields
    for services this project actually has (a backend-only project has no
    frontend_url, etc.)."""
    import datetime

    metadata = {
        "deployment_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "containers": list(container_result.get("services", {}).keys()),
        "services": container_result.get("services", {}),
        "rollback_available": rollback_available,
    }
    if has_backend:
        metadata["backend_url"] = f"http://localhost:{host_ports['backend']}/"
    if has_frontend:
        metadata["frontend_url"] = f"http://localhost:{host_ports['frontend']}/"
    if has_database:
        metadata["database_port"] = host_ports.get("db")
    return metadata
