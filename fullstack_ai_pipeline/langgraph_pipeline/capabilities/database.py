"""
Database Agent - DB_RUN / DB_UT / DB_VAL as three real LangGraph nodes (see
core/graph.py for the edges), matching the documented architecture diagram.
Runs once, deterministically, right after Planner on a fresh build ("build
around the database"). Reuses the same schema/OpenAPI generation as the
original pipeline and writes both directly to disk so every later agent can
read them with its own file tools.

The Supervisor can also route back here on an update - e.g. "add a due_date
column" - in which case DB_RUN reads whatever schema.sql/openapi.yaml already
exist and asks for a targeted revision (not a from-scratch regenerate),
since Backend/Frontend code already depends on the current table/column
names and API paths.
"""

from core.state import ProjectState, BuildStatus
from core.stage_loop import MAX_STAGE_ATTEMPTS, checkpoint
from tools.database_tools import GenerateSchemaTool, ValidateSchemaTool
from tools.contract_tools import GenerateOpenAPITool
from skills.project_registry import project_dir_for
from skills.quality_skills import check_schema_matches_contract
from core.logger import get_logger

STAGE = "database"
MAX_REVIEW_REPEATS = 3  # same threshold/reasoning as e2e.py's review_code leniency


class DatabaseCapability:
    def __init__(self):
        self.generate_schema_tool = GenerateSchemaTool()
        self.validate_schema_tool = ValidateSchemaTool()
        self.generate_openapi_tool = GenerateOpenAPITool()

    def run(self, state: ProjectState) -> dict:
        """DB_RUN: writes schema.sql (and openapi.yaml, the contract DB_VAL
        later checks the schema against) - one real attempt per graph step."""
        logger = get_logger()
        logger.node_start("database_run")
        checkpoint(state)

        plan = state["runtime"]["execution_plan"]
        project_dir = project_dir_for(state["project"]["project_id"])
        requirements = state["project"]["requirements"]
        tasks = state["project"]["tasks"]
        change_request = state["user_request"]

        needs_db = bool(plan.get("database") or plan.get("contract"))
        if not needs_db:
            logger.info("execution_plan has no database/contract need - skipping")
            logger.stage("database", "skipped")
            logger.node_complete("database_run")
            return {
                "runtime": {
                    "stage_status": {"database": "skipped"},
                    "current_stage": "database_run",
                    "completed_nodes": ["database_run"],
                    "logs": ["Database: skipped (not needed by this plan)"]
                }
            }

        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0) + 1
        feedback = state["runtime"].get("stage_feedback", {}).get(STAGE, "")
        logger.info(f"DB_RUN attempt {attempts}/{MAX_STAGE_ATTEMPTS}")

        # Only treat schema.sql/openapi.yaml on disk as "existing, revise it"
        # on a genuine --update run. On a fresh "build", real backend/frontend
        # code was never written against whatever's on disk - it might be
        # leftover from an earlier, aborted attempt at this exact project_id
        # (confirmed by direct testing: this caused the model to emit ALTER/
        # DROP statements against an assumed-already-existing table instead
        # of a real CREATE TABLE, since it saw a stale schema.sql and switched
        # into "revise" mode on its own). A fresh build must always generate
        # from scratch, never silently inherit unrelated leftover state.
        is_update = state["runtime"].get("mode") == "update"
        schema_path = project_dir / "schema.sql"
        existing_schema = schema_path.read_text(encoding="utf-8") if (is_update and schema_path.exists()) else ""
        # If what's on disk doesn't even pass its OWN validation (missing
        # CREATE TABLE entirely, or an ALTER TABLE on a table that was never
        # created, or a bad FOREIGN KEY - see validate_schema_skill) it isn't
        # usable as "the existing schema to revise" at all - using it as
        # context would just carry the corruption forward (a revision built
        # ON TOP of an already-broken schema just compounds it - confirmed
        # live: one broken revision's leftover ALTER statements got
        # duplicated into the NEXT revision's output, referencing tables that
        # still didn't exist). Treat this the same as a fresh build: generate
        # a complete schema from scratch instead of a delta against a base
        # that isn't itself valid.
        if existing_schema:
            is_existing_valid, existing_problem = self.validate_schema_tool.execute(existing_schema)
            if not is_existing_valid:
                logger.warning(f"schema.sql on disk doesn't pass its own validation ({existing_problem[:150]}) - "
                               f"generating a complete schema from scratch instead of revising it")
                existing_schema = ""
        contract_path = project_dir / "openapi.yaml"
        existing_spec = contract_path.read_text(encoding="utf-8") if (is_update and contract_path.exists()) else ""

        effective_request = change_request + (
            f"\n\nYour previous attempt had these problems - fix them:\n{feedback}" if feedback else ""
        )

        schema = existing_schema
        if plan.get("database"):
            schema = self.generate_schema_tool.execute(requirements, tasks, existing_schema, effective_request)
            schema_path.write_text(schema, encoding="utf-8")
            logger.success(f"Schema written to schema.sql ({schema.upper().count('CREATE TABLE')} tables)")

        openapi_spec = existing_spec
        if plan.get("contract"):
            openapi_spec = self.generate_openapi_tool.execute(
                requirements, tasks, schema, existing_spec, effective_request
            )
            contract_path.write_text(openapi_spec, encoding="utf-8")
            logger.success(f"OpenAPI spec written to openapi.yaml ({len(openapi_spec)} chars)")

        logger.node_complete("database_run")
        return {
            "project": {
                "workspace": {
                    "database": {
                        "version": 1, "schema": schema,
                        "status": BuildStatus.GENERATED.value if schema else BuildStatus.PENDING.value
                    },
                    "contract": {
                        "version": 1, "openapi_spec": openapi_spec,
                        "status": BuildStatus.GENERATED.value if openapi_spec else BuildStatus.PENDING.value
                    },
                }
            },
            "runtime": {
                "stage_status": {"database": "validating"},
                "stage_attempts": {STAGE: attempts},
                "current_stage": "database_run",
                "completed_nodes": ["database_run"],
                "logs": [f"Database: schema={'yes' if schema else 'no'}, contract={'yes' if openapi_spec else 'no'}"]
            }
        }

    def check_ut(self, state: ProjectState) -> dict:
        """DB_UT: SQL parses / valid? Deterministic schema validation."""
        logger = get_logger()
        logger.node_start("database_ut")
        checkpoint(state)

        if state["runtime"].get("stage_status", {}).get("database") == "skipped":
            logger.node_complete("database_ut")
            return {"runtime": {"stage_feedback": {STAGE: ""}, "current_stage": "database_ut"}}

        schema = state["project"]["workspace"].get("database", {}).get("schema", "")
        is_valid, message = self.validate_schema_tool.execute(schema) if schema else (True, "")

        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0)
        if is_valid:
            logger.success("DB_UT passed: schema parses/valid")
        else:
            logger.warning(f"DB_UT failed (attempt {attempts}/{MAX_STAGE_ATTEMPTS}): {message}")

        give_up = not is_valid and attempts >= MAX_STAGE_ATTEMPTS
        logger.node_complete("database_ut")
        return {
            "runtime": {
                "stage_status": {"database": "failed"} if give_up else {},
                "stage_feedback": {STAGE: "" if is_valid else f"Schema validation failed: {message}"},
                "current_stage": "database_ut",
                "failed_nodes": ["database_ut"] if give_up else [],
                "logs": [f"DB_UT: {'passed' if is_valid else message[:200]}"]
            }
        }

    def check_val(self, state: ProjectState) -> dict:
        """DB_VAL: matches contract? Does the OpenAPI spec actually cover
        every table the schema just defined."""
        logger = get_logger()
        logger.node_start("database_val")
        checkpoint(state)

        stage_status = state["runtime"].get("stage_status", {})
        if stage_status.get("database") == "skipped":
            logger.node_complete("database_val")
            return {"runtime": {"stage_feedback": {STAGE: ""}, "current_stage": "database_val"}}
        # NOTE: do NOT early-return on stage_status.get("database") == "failed"
        # here - that status persists across OUTER Supervisor rounds (only
        # DB_VAL itself ever changes it) and is not evidence that DB_UT gave
        # up THIS round; checking it caused a real, observed infinite loop
        # (a stage that failed once could never pass again - DB_VAL would
        # keep short-circuiting to "failed" forever instead of re-running
        # its real check on the fresh attempt the Supervisor just started).
        # The routing (route_after_ut in core/graph.py) already guarantees
        # this node only runs after DB_UT genuinely passed THIS round.

        schema = state["project"]["workspace"].get("database", {}).get("schema", "")
        openapi_spec = state["project"]["workspace"].get("contract", {}).get("openapi_spec", "")
        all_mismatches = check_schema_matches_contract(schema, openapi_spec) if (schema and openapi_spec) else []

        # A specific mismatch that keeps recurring across MAX_REVIEW_REPEATS
        # OUTER rounds - not just this round's 3 internal attempts - despite
        # the model being fed the exact gap as feedback every time, is a real,
        # observed self-heal-can't-converge failure mode (confirmed live: a
        # model kept inventing a "task_history" table with no matching API
        # endpoint, across 6+ attempts over 2 full rounds, never fixing it).
        # Without this, database can block the ENTIRE pipeline forever - no
        # prerequisite-gated stage (backend/frontend/testing/deployment) can
        # ever run while database sits at "failed" - the same class of fix
        # already applied to E2E's review_code findings.
        history = state["runtime"].get("issue_history", [])
        repeat_counts = {}
        for past_issue in history:
            if past_issue.get("source") != "schema_contract":
                continue
            repeat_counts[past_issue.get("description")] = repeat_counts.get(past_issue.get("description"), 0) + 1

        mismatches = []
        for m in all_mismatches:
            if repeat_counts.get(m, 0) >= MAX_REVIEW_REPEATS:
                logger.info(f"Schema/contract gap has recurred {repeat_counts[m]}+ times across rounds without "
                           f"resolving - no longer blocking: {m}")
            else:
                mismatches.append(m)

        attempts = state["runtime"].get("stage_attempts", {}).get(STAGE, 0)
        passed = not mismatches
        if passed:
            logger.success("DB_VAL passed: matches contract")
        else:
            logger.warning(f"DB_VAL failed (attempt {attempts}/{MAX_STAGE_ATTEMPTS}): {len(mismatches)} gap(s)")

        give_up = not passed and attempts >= MAX_STAGE_ATTEMPTS
        new_status = {"database": "done"} if passed else ({"database": "failed"} if give_up else {})

        # A schema/contract revision invalidates whatever Backend/Frontend
        # already built against the OLD schema, and any Testing verdict
        # measured against that old code - reset them (never resurrecting a
        # stage the plan doesn't actually need).
        plan = state["runtime"]["execution_plan"]
        if passed:
            if plan.get("backend"):
                new_status["backend"] = "pending"
            if plan.get("frontend"):
                new_status["frontend"] = "pending"
            if plan.get("backend") or plan.get("frontend"):
                new_status["testing"] = "pending"

        logger.stage("database", "done" if passed else ("failed" if give_up else "pending"))
        logger.node_complete("database_val")
        return {
            "runtime": {
                "stage_status": new_status,
                "stage_feedback": {STAGE: "" if passed else "; ".join(mismatches)},
                "quality_passed": None if passed else state["runtime"].get("quality_passed"),
                # Every mismatch this round found (BEFORE the leniency filter)
                # accumulates here so the recurrence check above can count
                # occurrences across future rounds too - not just what's
                # still blocking after filtering.
                "issue_history": [
                    {"severity": "medium", "file": "schema.sql/openapi.yaml", "description": m,
                     "suggested_fix": "Add the missing endpoint or remove the unused table.",
                     "source": "schema_contract"}
                    for m in all_mismatches
                ],
                "current_stage": "database_val",
                "completed_nodes": ["database_val", "database"] if passed else [],
                "failed_nodes": ["database_val"] if give_up else [],
                "logs": [f"DB_VAL: {'passed' if passed else '; '.join(mismatches)[:200]}"]
            }
        }
