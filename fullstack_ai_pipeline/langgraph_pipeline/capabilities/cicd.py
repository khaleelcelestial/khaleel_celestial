"""
CI/CD Deployment Agent - CICD_RUN / CICD_UT / CICD_VAL as three real
LangGraph nodes (see core/graph.py for the edges), matching the documented
architecture diagram. Replaces the old capabilities/deployment.py (renamed -
same underlying work, split into 3 nodes instead of one).

Local Docker only, by design: CICD_RUN packages + builds/starts the stack
via `docker compose up --build -d` (no git/registry push - that would need
real credentials and network side effects nobody asked for). CICD_UT checks
the build/start itself succeeded (deterministic subprocess return code, not
text-matching an agent's report). CICD_VAL checks the result is actually
live/healthy via a real HTTP request - the diagram's "repo/registry live +
healthy?" check, done against the local deployment instead of a registry.
"""

import shutil
import subprocess

from core.state import ProjectState
from core.stage_loop import MAX_STAGE_ATTEMPTS, checkpoint
from skills.project_registry import project_dir_for, sync_workspace_from_disk, save_project_snapshot
from skills.agent_tools import make_agent_tools
from skills.text_utils import cap_report
from core.agent_runtime import run_tool_agent
from core.logger import get_logger

STAGE = "deployment"

CICD_SYSTEM_PROMPT = """You are the deployment agent. You verify and deploy - you do not write
or fix application code yourself (no write_file tool on purpose). The project's Dockerfiles and
docker-compose.yml have already been generated for you, and a separate deterministic step has already
run "docker compose up --build -d" for real before you were called. Minimize tool calls - run "docker 
compose ps" ONCE and "docker compose logs" ONLY for failed services. Read files ONLY if you need to 
diagnose a specific error."""


class CICDCapability:
    def run(self, state: ProjectState) -> dict:
        """CICD_RUN: packages the project and brings it up for real via
        `docker compose up --build -d` - the diagram's "push to repo / build
        image" step, done locally (no git/registry push)."""
        logger = get_logger()
        logger.node_start("cicd_run")
        checkpoint(state)

        project_dir = project_dir_for(state["project"]["project_id"])
        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0) + 1
        logger.info(f"CICD_RUN attempt {attempts}/{MAX_STAGE_ATTEMPTS}")
        logger.stage_started(STAGE, attempts)

        from skills.docker_skills import write_docker_assets, generate_start_scripts
        from skills.quality_skills import generate_docs_skill

        workspace = sync_workspace_from_disk(project_dir, state["project"]["workspace"])
        has_backend = bool(workspace.get("backend", {}).get("files"))
        has_frontend = bool(workspace.get("frontend", {}).get("files"))
        has_database = bool(workspace.get("database", {}).get("schema"))

        docker_artifacts = write_docker_assets(project_dir, workspace)
        logger.info(f"Generated docker assets: {docker_artifacts}")

        # Deployment artifact pre-flight check - fails fast with a clear,
        # specific message instead of a raw `docker compose up` error
        # partway through an expensive build. Does NOT re-check source
        # code correctness (DB/BE/FE_UT's own job, already done) - only
        # that the deployment-time files those stages/write_docker_assets
        # were supposed to produce actually exist on disk.
        from skills.cicd_validators import validate_deployment_artifacts
        artifact_problems = validate_deployment_artifacts(project_dir, has_backend, has_frontend, has_database)
        if artifact_problems:
            detail = "Missing required deployment artifact(s):\n" + "\n".join(f"- {p}" for p in artifact_problems)
            logger.warning(detail)
            logger.stage("deployment", "failed")
            logger.node_complete("cicd_run")
            return {
                "project": {"workspace": workspace},
                "runtime": {
                    "deployment_status": detail,
                    "stage_status": {"deployment": "failed"},
                    "stage_attempts": {STAGE: attempts},
                    "stage_progress": {STAGE: False},
                    "current_stage": "cicd_run",
                    "completed_nodes": ["cicd_run"],
                    "failed_nodes": ["cicd_run"],
                    "logs": [f"CICD_RUN: {detail}"]
                }
            }

        start_scripts = generate_start_scripts(project_dir, workspace)
        if start_scripts:
            logger.info(f"Generated local-run fallback scripts: {start_scripts}")

        docs = generate_docs_skill(workspace, state["project"].get("requirements", ""))
        for name, content in docs.items():
            (project_dir / name).write_text(content, encoding="utf-8")
        workspace["docs"] = docs
        logger.info(f"Generated docs: {list(docs.keys())}")

        save_project_snapshot(
            project_dir=project_dir,
            project_id=state["project"]["project_id"],
            user_request=state["user_request"],
            execution_plan=state["runtime"]["execution_plan"],
            requirements=state["project"].get("requirements", ""),
            architecture=state["project"].get("architecture", ""),
            acceptance_criteria=state["project"].get("acceptance_criteria", []),
            tasks=state["project"].get("tasks", []),
            workspace=workspace,
            stage_status=state["runtime"].get("stage_status", {}),
        )
        logger.info("Project registry snapshot saved")

        logger.info("Checking Docker availability (shutil.which + docker info)...")
        docker_available = shutil.which("docker") is not None
        if docker_available:
            try:
                docker_info = subprocess.run(
                    ["docker", "info"], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=15,
                )
                docker_available = docker_info.returncode == 0
            except (subprocess.TimeoutExpired, OSError):
                docker_available = False

        if not docker_available:
            status = ("Docker isn't available/running on this machine. The project is fully packaged "
                      f"and dockerized at {project_dir} - install/start Docker Desktop, then run "
                      "`docker compose up --build` there to host it. "
                      + ("In the meantime, run `./start.sh` (or `start.bat` on Windows) in that "
                         "folder to bring it up as plain local processes instead." if start_scripts else ""))
            logger.warning(status)
            logger.stage("deployment", "failed")
            logger.node_complete("cicd_run")
            return {
                "project": {"workspace": workspace},
                "runtime": {
                    "final_project_path": str(project_dir),
                    "deployment_status": status,
                    "stage_status": {"deployment": "failed"},
                    "stage_attempts": {STAGE: attempts},
                    "stage_progress": {STAGE: False},
                    "current_stage": "cicd_run",
                    "completed_nodes": ["cicd_run"],
                    "failed_nodes": ["cicd_run"],
                    "logs": [f"CICD_RUN: {status}"]
                }
            }

        # Rollback safety net (see skills/cicd_validators.py) - snapshot
        # whatever image is CURRENTLY running for each service, BEFORE the
        # upcoming build overwrites it, so a failed update can be reverted
        # fast (no rebuild) instead of leaving a broken deployment live.
        # Only ever non-empty on an UPDATE (a service that's never been
        # deployed before has no ":latest" image yet to snapshot) - this
        # is never mistaken for "safe to always roll back to" on a first
        # deploy. Database data/schema is never part of this snapshot -
        # see DATABASE ROLLBACK SAFETY in cicd_validators.py.
        from skills.cicd_validators import snapshot_images_for_rollback
        rollback_candidates = (["backend"] if has_backend else []) + (["frontend"] if has_frontend else [])
        snapshotted_services = snapshot_images_for_rollback(project_dir, rollback_candidates)
        if snapshotted_services:
            logger.info(f"Snapshotted current image(s) for rollback safety: {snapshotted_services}")

        logger.success("Docker is available and running - building/starting the stack for real")
        try:
            build_result = subprocess.run(
                ["docker", "compose", "up", "--build", "-d"],
                cwd=str(project_dir), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300,
            )
            build_ok = build_result.returncode == 0
            build_detail = build_result.stderr[-500:] if not build_ok else "containers started"
        except (subprocess.TimeoutExpired, OSError) as e:
            build_ok = False
            build_detail = str(e)[:300]

        if build_ok and has_database:
            # schema.sql's own docker-entrypoint-initdb.d mechanism only ever
            # runs ONCE, against a completely empty volume - on every deploy
            # after the very first one (db_data, the named volume, already
            # has data), Postgres silently skips it even if schema.sql
            # changed. Apply it explicitly so an update's schema change
            # actually reaches the live, already-running database - see
            # apply_schema_to_running_db's docstring. Every statement is
            # generated to be idempotent, so this is always safe to run,
            # including on a fresh build (a harmless no-op there).
            from skills.docker_skills import check_containers_healthy, apply_schema_to_running_db
            db_health = check_containers_healthy(project_dir, ["db"], timeout_s=30)
            if db_health["passed"]:
                schema_applied, schema_detail = apply_schema_to_running_db(project_dir)
                if schema_applied:
                    logger.success("Schema applied to the live database (idempotent - only new tables/"
                                  "columns took effect, existing data untouched)")
                else:
                    logger.warning(f"Applying schema.sql to the live database failed: {schema_detail}")
                    build_ok = False
                    build_detail = f"schema apply to live db failed: {schema_detail}"
            else:
                logger.warning(f"db container not healthy yet, skipping live schema apply: {db_health['detail']}")
                build_ok = False
                build_detail = f"db container not healthy: {db_health['detail']}"

        logger.deployment("docker_build", "COMPLETED" if build_ok else "FAILED",
                          {"detail": build_detail[:300]})

        if build_ok:
            logger.success(f"docker compose up --build -d succeeded")

            # Display access URLs to user
            from skills.project_registry import allocate_ports
            host_ports = allocate_ports(state["project"]["project_id"])
            logger.info("")
            logger.info("🌐 Application deployed and running!")
            if workspace.get("backend", {}).get("files"):
                logger.info(f"   Backend:  http://localhost:{host_ports['backend']}/")
            if workspace.get("frontend", {}).get("files"):
                logger.info(f"   Frontend: http://localhost:{host_ports['frontend']}/")
            logger.info(f"   Project:  {project_dir}")
            logger.info("")
            logger.info("💡 To stop: cd {project_dir} && docker compose down")
            logger.info("")
        else:
            logger.warning(f"docker compose up --build -d failed: {build_detail}")

        tools = make_agent_tools(project_dir, write_prefix="", allow_commands=True, include_write=False)
        summary, ok = run_tool_agent(
            "deployment_agent", CICD_SYSTEM_PROMPT,
            "The stack has already been brought up (or attempted). Confirm what's running and report.", tools
        )
        logger.success(f"Deployment agent: {summary[:300]}") if ok else logger.warning(f"Deployment agent: {summary[:300]}")

        prior_failures = state["runtime"].get("consecutive_agent_failures", 0)
        consecutive_failures = 0 if ok else prior_failures + 1

        capped_summary = cap_report(f"docker compose up --build -d: {'OK' if build_ok else build_detail}\n\n{summary}")

        logger.node_complete("cicd_run")
        return {
            "project": {"workspace": workspace},
            "runtime": {
                "stage_status": {"deployment": "validating"},
                "final_project_path": str(project_dir),
                "deployment_status": capped_summary,
                "test_results": {"cicd_build_ok": build_ok, "cicd_build_detail": build_detail,
                                "cicd_rollback_candidates": snapshotted_services},
                "stage_attempts": {STAGE: attempts},
                "stage_progress": {STAGE: True},
                "current_stage": "cicd_run",
                "completed_nodes": ["cicd_run"],
                "consecutive_agent_failures": consecutive_failures,
                "logs": [f"CICD_RUN: {summary[:300]}"]
            }
        }

    def check_ut(self, state: ProjectState) -> dict:
        """CICD_UT: push or build succeeds? Deterministic subprocess return
        code from `docker compose up --build -d`, not a text-matched report."""
        logger = get_logger()
        logger.node_start("cicd_ut")
        checkpoint(state)

        test_results = state["runtime"].get("test_results", {})
        passed = bool(test_results.get("cicd_build_ok"))
        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0)
        if passed:
            logger.success("CICD_UT passed: build/start succeeded")
        else:
            logger.warning(f"CICD_UT failed (attempt {attempts}/{MAX_STAGE_ATTEMPTS}): "
                          f"{test_results.get('cicd_build_detail', '')[:200]}")

        give_up = not passed and attempts >= MAX_STAGE_ATTEMPTS
        detail = "" if passed else test_results.get("cicd_build_detail", "build failed")[:300]
        logger.node_complete("cicd_ut")
        return {
            "runtime": {
                "stage_status": {"deployment": "failed"} if give_up else {},
                "stage_feedback": {STAGE: detail},
                "current_stage": "cicd_ut",
                "failed_nodes": ["cicd_ut"] if give_up else [],
                "logs": [f"CICD_UT: {'passed' if passed else 'failed'}"]
            }
        }

    def check_val(self, state: ProjectState) -> dict:
        """CICD_VAL: repo/registry live + healthy? Two layers, both required:
        (1) container-level - every expected service is actually "running"
        AND passes its own Docker HEALTHCHECK (not just "the build exit code
        was 0" - a container can start and still be hung/crash-looping/every
        request 500ing, which `docker compose up`'s own exit code can never
        see). (2) application-level - a real HTTP request actually gets a
        non-5xx response, the same check as before. Passing CICD_UT's build
        step is necessary but explicitly NOT sufficient on its own - this is
        the node that actually decides "deployed" means something real."""
        logger = get_logger()
        logger.node_start("cicd_val")
        checkpoint(state)

        project_dir = project_dir_for(state["project"]["project_id"])
        workspace = state["project"].get("workspace", {})
        has_backend = bool(workspace.get("backend", {}).get("files"))
        has_frontend = bool(workspace.get("frontend", {}).get("files"))
        has_database = bool(workspace.get("database", {}).get("schema"))

        from skills.e2e_skills import http_reachable
        from skills.docker_skills import check_containers_healthy, get_service_logs
        from skills.project_registry import allocate_ports

        expected_services = (["db"] if has_database else []) \
            + (["backend"] if has_backend else []) + (["frontend"] if has_frontend else [])

        container_result = (check_containers_healthy(project_dir, expected_services)
                            if expected_services else {"passed": True, "services": {}, "missing": [], "detail": "n/a"})

        host_ports = allocate_ports(state["project"]["project_id"])
        backend_ok = not has_backend
        frontend_ok = not has_frontend
        backend_detail = frontend_detail = "not applicable"
        # Only bother with the application-level HTTP check once the
        # container itself is confirmed healthy - an HTTP probe against a
        # container Docker already reports as unhealthy/missing is redundant
        # noise, not new evidence.
        if container_result["passed"]:
            if has_backend:
                backend_ok, backend_detail = http_reachable(f"http://localhost:{host_ports['backend']}/")
            if has_frontend:
                frontend_ok, frontend_detail = http_reachable(f"http://localhost:{host_ports['frontend']}/")

        # Deployment smoke test: direct database connectivity to THIS
        # deployed instance (not a throwaway one) - reuses E2E's own
        # DatabaseIntegrationValidator rather than duplicating it (same
        # deliberately narrow scope as there: connectivity + real schema
        # presence, not a synthetic CRUD probe - see that class's
        # docstring for why). Everything else the spec's "smoke test" list
        # asks for (frontend/backend reachable, no 5xx) is already covered
        # by the checks above.
        db_ok, db_detail = True, "not applicable"
        if container_result["passed"] and has_database:
            from skills.e2e_validators import DatabaseIntegrationValidator
            from skills.db_validators import build_schema_model
            schema = workspace.get("database", {}).get("schema", "")
            model = build_schema_model(schema) if schema else None
            expected_tables = set(model.tables.keys()) if model and not model.parse_error else set()
            db_problems = DatabaseIntegrationValidator().validate(
                state["project"]["project_id"], host_ports, expected_tables
            )
            db_ok = not db_problems
            db_detail = "; ".join(db_problems) if db_problems else "connected, schema present"

        passed = container_result["passed"] and backend_ok and frontend_ok and db_ok
        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0)
        logger.deployment("health_check", "COMPLETED" if passed else "FAILED", {
            "containers_ok": container_result["passed"], "backend_ok": backend_ok,
            "frontend_ok": frontend_ok, "database_ok": db_ok,
        })

        # Real gap this closes: "frontend: health=unhealthy" alone gives
        # neither the Supervisor nor whichever agent it routes to anything
        # to act on - confirmed live, a frontend crashing on every request
        # (missing npm dependency) only ever showed up in its own container
        # logs. Pull those logs for every service that isn't "ok" so the
        # failure names the real problem, not just a status word - and tag
        # each with the stage that actually owns fixing it (only that
        # stage's agent has write access to the relevant files).
        _service_to_stage = {"db": "database", "backend": "backend", "frontend": "frontend"}
        broken_service_logs = ""
        if not passed:
            for service, info in container_result.get("services", {}).items():
                if not info.get("ok", True):
                    owner = _service_to_stage.get(service, service)
                    logs = get_service_logs(project_dir, service)
                    broken_service_logs += f"\n\n[{service} container logs - this is a '{owner}' stage issue]:\n{logs}"
            for service in container_result.get("missing", []):
                owner = _service_to_stage.get(service, service)
                broken_service_logs += f"\n\n[{service} container never appeared in `docker compose ps` - a '{owner}' stage issue]"
            if not db_ok:
                broken_service_logs += f"\n\n[database connectivity check - this is a 'database' stage issue]: {db_detail}"

        if passed:
            logger.success(f"CICD_VAL passed: containers running+healthy ({container_result['detail']}), "
                          f"backend reachable, frontend reachable")
            logger.info(f"Deployment persists in Docker Desktop after this pipeline exits - "
                       f"`docker compose down` in {project_dir} to stop it, or manage it from Docker Desktop directly.")
        else:
            logger.warning(f"CICD_VAL failed (attempt {attempts}/{MAX_STAGE_ATTEMPTS}): "
                          f"containers_ok={container_result['passed']} ({container_result['detail']}), "
                          f"backend={backend_ok} ({backend_detail}), frontend={frontend_ok} ({frontend_detail})")

        give_up = not passed and attempts >= MAX_STAGE_ATTEMPTS
        new_status = {"deployment": "done"} if passed else ({"deployment": "failed"} if give_up else {})

        # Rollback (highest-priority addition) - only attempted once this
        # attempt has genuinely given up (not on an ordinary retry-able
        # failure - CICD_RUN gets its normal MAX_STAGE_ATTEMPTS shots at
        # fixing this itself first), and only for services that actually
        # HAD a previous working deployment to restore (see
        # snapshot_images_for_rollback - empty on a first-ever deploy).
        # Database data/schema is never rolled back - see
        # cicd_validators.py's DATABASE ROLLBACK SAFETY notes.
        rollback_tag = ""
        deployment_metadata = {}
        if give_up:
            snapshotted_services = state["runtime"].get("test_results", {}).get("cicd_rollback_candidates", [])
            if snapshotted_services:
                from skills.cicd_validators import rollback_to_previous
                logger.warning(f"Deployment validation failed after {attempts} attempt(s) - attempting "
                               f"rollback to the previous working deployment...")
                logger.rollback("STARTED", {"services": snapshotted_services})
                rollback_ok, rollback_detail = rollback_to_previous(project_dir, snapshotted_services)
                if rollback_ok:
                    logger.success(f"Rollback succeeded: {rollback_detail}")
                    rollback_tag = "failed_rolled_back"
                    logger.rollback("COMPLETED", {"services": snapshotted_services, "detail": rollback_detail[:300]})
                else:
                    logger.warning(f"Rollback FAILED: {rollback_detail}")
                    rollback_tag = "failed_rollback_failed"
                    logger.rollback("FAILED", {"services": snapshotted_services, "detail": rollback_detail[:300]})
                broken_service_logs += (f"\n\nROLLBACK {'SUCCEEDED' if rollback_ok else 'FAILED'}: "
                                        f"{rollback_detail}\nNote: database data/schema is never automatically "
                                        f"rolled back (see cicd_validators.py) - if this update changed the "
                                        f"schema, manual database review may be required.")
            else:
                rollback_tag = "failed_no_previous_deployment"
                broken_service_logs += ("\n\nNo previous working deployment was recorded - nothing available "
                                        "to roll back to (this looks like a first deploy).")
        elif passed:
            # Deployment metadata (only meaningful on a real pass) - see
            # cicd_validators.py's collect_deployment_metadata for exactly
            # what's captured and why each field is conditional.
            from skills.cicd_validators import collect_deployment_metadata
            deployment_metadata = collect_deployment_metadata(
                project_dir, host_ports, container_result, has_backend, has_frontend, has_database,
                rollback_available=bool(state["runtime"].get("test_results", {}).get("cicd_rollback_candidates")),
            )

        health_report = (f"\n\nContainer health: {container_result['detail']}\n"
                         f"Live HTTP check: backend {'OK' if backend_ok else 'FAIL'} ({backend_detail}), "
                         f"frontend {'OK' if frontend_ok else 'FAIL'} ({frontend_detail}), "
                         f"database {'OK' if db_ok else 'FAIL'} ({db_detail}){broken_service_logs}"
                         + (f"\n\nDEPLOYMENT_STATUS: {rollback_tag}" if rollback_tag else ""))
        detail = "" if passed else (f"deployment not fully healthy - containers_ok={container_result['passed']} "
                                    f"({container_result['detail']}), backend={backend_ok}, frontend={frontend_ok}, "
                                    f"database={db_ok}{broken_service_logs}")

        logger.stage("deployment", "done" if passed else ("failed" if give_up else "pending"))
        logger.deployment("final_state", "COMPLETED", {
            "deployment_status": "healthy" if passed else (rollback_tag or ("pending" if not give_up else "failed")),
        })
        logger.node_complete("cicd_val")
        return {
            "runtime": {
                "stage_status": new_status,
                "stage_feedback": {STAGE: detail},
                "deployment_status": (state["runtime"].get("deployment_status", "") + health_report),
                "test_results": {"cicd_deployment_metadata": deployment_metadata},
                "current_stage": "cicd_val",
                "completed_nodes": ["cicd_val", "deployment"] if passed else [],
                "failed_nodes": ["cicd_val"] if give_up else [],
                "logs": [f"CICD_VAL: {'passed' if passed else 'failed'}" + (f" ({rollback_tag})" if rollback_tag else "")]
            }
        }
