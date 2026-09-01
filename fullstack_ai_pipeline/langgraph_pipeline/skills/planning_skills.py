"""
Planning skills - LLM-backed analysis and classification
Uses model router for provider abstraction
"""

from core.model_router import get_router
from core.logger import get_logger
from skills.text_utils import extract_code_block
import json


def analyze_request_skill(user_request: str) -> dict:
    """
    Classify the user request into project type and determine which stages are needed.
    Returns the ExecutionPlan flags.
    """
    
    logger = get_logger()
    logger.step("📋", "Analyzing request...")

    router = get_router()
    
    system_prompt = """You are a software project analyzer. Given a user request, determine which stages of the software pipeline are needed.

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

Examples:

Request: "Design a PostgreSQL schema for a library system."
Output: {"database": true, "contract": false, "backend": false, "frontend": false, "release": false}

Request: "Generate an OpenAPI specification for a bookstore API."
Output: {"database": true, "contract": true, "backend": false, "frontend": false, "release": false}

Request: "Build a FastAPI REST API for a library system."
Output: {"database": true, "contract": true, "backend": true, "frontend": false, "release": true}

Request: "Build a React dashboard for employee analytics."
Output: {"database": false, "contract": true, "backend": false, "frontend": true, "release": true}

Request: "Build a full-stack expense tracker using React and FastAPI."
Output: {"database": true, "contract": true, "backend": true, "frontend": true, "release": true}

Now analyze this request and output ONLY the JSON object, nothing else:"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_request}
    ]
    
    full_plan = {
        "database": True, "contract": True,
        "backend": True, "frontend": True, "release": True
    }

    try:
        response_text = router.invoke("analyze_request", messages)
    except RuntimeError as e:
        logger.warning(f"Analysis unavailable: {str(e)[:150]}. Defaulting to full pipeline.")
        return full_plan

    response_text = extract_code_block(response_text.strip(), "json")

    try:
        execution_plan = json.loads(response_text)
    except json.JSONDecodeError as e:
        # This is the very first node - fail open with the safest plan
        # (run everything) rather than crashing the whole pipeline.
        logger.warning(f"JSON parse error: {str(e)[:100]}. Defaulting to full pipeline.")
        execution_plan = full_plan

    logger.success("Execution plan determined")

    return execution_plan


def extract_requirements_skill(user_request: str) -> str:
    """
    Extract structured requirements from the user request.
    Returns: Actors, features, flows as structured text.
    """
    
    logger = get_logger()
    logger.step("📝", "Extracting requirements...")

    router = get_router()

    system_prompt = """You are a requirements analyst. Given a user request, extract structured requirements.

Format your output as:

## Actors
List the types of users/actors who will interact with the system.

## Features
List the key features/capabilities the system must provide.

## User Flows
Describe the main user workflows/journeys.

Be concise but specific. Focus on WHAT needs to be built, not HOW."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_request}
    ]

    try:
        return router.invoke("extract_requirements", messages)
    except RuntimeError as e:
        logger.warning(f"Requirements extraction unavailable: {str(e)[:150]}. Using the raw request as-is.")
        return f"## Raw Request\n\n{user_request}"


def create_task_list_skill(user_request: str, requirements: str, architecture: str = "") -> list[str]:
    """
    Generate concrete implementation tasks from requirements - grounded in
    the actual chosen architecture (real table/endpoint/page names) rather
    than generic feature descriptions, since this now runs AFTER
    choose_stack_skill (see planner.py) instead of before it.
    Returns: List of specific task strings.
    """

    logger = get_logger()
    logger.step("📌", "Creating task list...")

    router = get_router()

    system_prompt = """You are a technical project manager. Given a user request, requirements, and the
project's chosen architecture, generate a list of concrete implementation tasks.

Output ONLY a JSON array of task strings, like:
["Create the users table with email/password fields", "Implement POST /auth/token", "Build the login page"]

Each task should be:
- Concrete and actionable
- Grounded in the actual architecture given below - name the real tables/endpoints/pages/components it
  describes, not generic placeholders (e.g. "Implement POST /expenses" not "Build the API")
- Implementation-focused

Output ONLY the JSON array, nothing else."""

    user_content = f"Request: {user_request}\n\nRequirements:\n{requirements}"
    if architecture:
        user_content += f"\n\nArchitecture:\n{architecture}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ]
    
    fallback_tasks = ["Implement the requested functionality per the extracted requirements"]

    try:
        response_text = router.invoke("create_task_list", messages)
    except RuntimeError as e:
        logger.warning(f"Task list generation unavailable: {str(e)[:150]}.")
        return fallback_tasks

    response_text = extract_code_block(response_text.strip(), "json")

    try:
        tasks = json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error: {str(e)[:100]}. Falling back to a single generic task.")
        tasks = fallback_tasks

    return tasks


def choose_stack_skill(user_request: str, execution_plan: dict) -> str:
    """
    Choose concrete technology stack based on the request and execution plan.
    Returns: Architecture description with specific frameworks.
    """
    
    logger = get_logger()
    logger.step("🏗️ ", "Choosing technology stack...")

    router = get_router()
    
    system_prompt = """You are a technical architect. Given a user request and execution plan, describe
the technology stack this project will use.

The stack itself is FIXED across every project this pipeline builds - not your choice to make, since
the Backend/Frontend agents, Dockerfile generation, and the schema/ORM contract all hardcode it:
- Database: PostgreSQL.
- Backend: Python + FastAPI + SQLAlchemy (declarative Base matching schema.sql) + psycopg2-binary.
- Frontend: React + Vite.

Your job is only to describe how THIS request maps onto that fixed stack: what the API surface needs
to cover, which specific tables/models/endpoints/pages/components make sense for the requirements, and
any key libraries beyond the base stack (auth, state management, etc.) as applicable. Do not propose or
imply a different database, backend framework, ORM, or frontend framework - that would contradict what
the other agents are actually going to build and desynchronize the recorded architecture from reality.

Be specific about the request-specific parts. Keep it under 200 words."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Request: {user_request}\n\nExecution Plan: {execution_plan}"}
    ]

    try:
        return router.invoke("choose_stack", messages)
    except RuntimeError as e:
        logger.warning(f"Stack selection unavailable: {str(e)[:150]}. Defaulting to PostgreSQL + FastAPI + React.")
        return ("Database: PostgreSQL\nAPI Framework: FastAPI\n"
                "Frontend Framework: React (Vite)\nKey libraries: SQLAlchemy ORM, JWT auth")


def extract_acceptance_criteria_skill(user_request: str, requirements: str, architecture: str) -> list[str]:
    """
    Extract discrete, testable, business-level acceptance criteria from the
    request/requirements/architecture - the structured artifact the shared
    Acceptance Test Generator (used by Database/Backend/Frontend/E2E/CICD)
    needs to write real requirement-driven tests instead of guessing from
    free-text prose. Each criterion must be independently checkable (a real
    test could pass or fail against it), not a restatement of a feature.

    Returns: list of acceptance-criterion strings.
    """
    logger = get_logger()
    logger.step("✅", "Extracting acceptance criteria...")

    router = get_router()

    system_prompt = """You are a QA lead writing acceptance criteria for a software project. Given the
user request, extracted requirements, and chosen architecture, output a list of concrete, independently
testable business-level acceptance criteria - the kind of statements a test suite would assert against,
not a restatement of features.

Output ONLY a JSON array of strings, like:
["A rejected visitor must not appear in the pending-approvals list", "A soft-deleted user must not be
able to log in", "Deleting a user must not delete their historical reports"]

Focus on things a generic feature list would miss but that real usage depends on:
- CRUD correctness (creating/reading/updating/deleting behaves as expected, including what happens to
  related records)
- soft-delete / audit-history behavior (deleted records may need to stay for history, not disappear)
- foreign-key / relationship integrity (what must stay consistent when a related record changes)
- approval/workflow state transitions (what's allowed/forbidden at each state)
- authorization/business constraints (who can do what, uniqueness rules, required fields)

Each criterion must be:
- Specific enough to write a real test against (names the entity/state/rule involved)
- A business rule, not an implementation detail (never mention a table/column/framework name directly)
- Independently checkable - true or false, not a vague goal

Output ONLY the JSON array, nothing else. If the request is too simple for any of this to apply (e.g. a
single static page with no data model), output an empty array []."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Request: {user_request}\n\nRequirements:\n{requirements}"
                                    f"\n\nArchitecture:\n{architecture}"}
    ]

    try:
        response_text = router.invoke("extract_acceptance_criteria", messages)
        response_text = extract_code_block(response_text.strip(), "json")
        criteria = json.loads(response_text)
        return [c for c in criteria if isinstance(c, str) and c.strip()]
    except Exception as e:
        logger.warning(f"Acceptance criteria extraction unavailable ({str(e)[:150]}) - continuing with none.")
        return []


def mark_tasks_complete_skill(tasks: list[str], architecture: str, stage: str, summary: str) -> list[int]:
    """
    Ask which of Planner's tasks this stage's just-completed RUN attempt
    actually satisfies - a cheap, self-reported checkbox mechanism so
    task_status (see core/state.py) can reflect real progress instead of
    tasks staying a static, never-updated reference list forever.

    Called by database.py/backend.py/frontend.py's RUN nodes right after a
    real write (stage_progress True) - never on a no-op/failed attempt,
    since nothing new was actually satisfied. Fails open (returns []) on
    any error - this is bookkeeping, not a gate, so a parse hiccup here must
    never block or fail the pipeline.

    Returns: 0-based indices into `tasks` that this round's work completes.
    """
    logger = get_logger()

    if not tasks:
        return []

    router = get_router()
    numbered = "\n".join(f"{i}: {t}" for i, t in enumerate(tasks))

    system_prompt = """You are a technical project manager checking off a task list. Given the full task
list (numbered from 0), the project's architecture, and a summary of what the "{stage}" stage just built
this round, output ONLY a JSON array of the task NUMBERS that this round's work satisfies.

Only include a number if the summary shows real, concrete evidence that task is done (the actual
table/endpoint/page/file it describes was just created or completed) - do not guess or include a task
just because it's related to this stage in general. If nothing in the summary clearly completes any
task, output an empty array [].

Output ONLY the JSON array of integers, nothing else - e.g. [0, 2, 5] or []."""

    messages = [
        {"role": "system", "content": system_prompt.format(stage=stage)},
        {"role": "user", "content": f"Task list:\n{numbered}\n\nArchitecture:\n{architecture}"
                                    f"\n\nWhat the '{stage}' stage just built this round:\n{summary}"}
    ]

    try:
        response_text = router.invoke("mark_tasks_complete", messages)
        response_text = extract_code_block(response_text.strip(), "json")
        indices = json.loads(response_text)
        return [i for i in indices if isinstance(i, int) and 0 <= i < len(tasks)]
    except Exception as e:
        logger.warning(f"Task-completion check unavailable ({str(e)[:150]}) - task_status left unchanged this round.")
        return []
