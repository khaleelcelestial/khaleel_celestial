"""
Planner Capability - Always runs first (fresh builds only; update mode
skips straight to the supervisor loop). Analyzes the request, extracts
requirements/tasks/architecture, assigns the project_id, and sets the
initial stage_status for every later stage based on the execution plan
alone - so "this project has no database" is visible in state from step
one, not implied by scattered `if plan.get(...)` checks in later nodes.
"""

from core.state import ProjectState
from tools.planning_tools import (
    AnalyzeRequestTool, ExtractRequirementsTool, CreateTaskListTool, ChooseStackTool
)
from skills.project_registry import project_dir_for, slugify
from core.logger import get_logger


class PlannerCapability:
    def __init__(self):
        self.analyze_tool = AnalyzeRequestTool()
        self.requirements_tool = ExtractRequirementsTool()
        self.tasks_tool = CreateTaskListTool()
        self.stack_tool = ChooseStackTool()

    def run(self, state: ProjectState) -> dict:
        logger = get_logger()
        logger.node_start("planner")

        user_request = state["user_request"]

        logger.info("Calling analyze_request_skill...")
        execution_plan = self.analyze_tool.execute(user_request)
        logger.success(f"Plan: DB={execution_plan['database']}, Contract={execution_plan['contract']}, "
                      f"Backend={execution_plan['backend']}, Frontend={execution_plan['frontend']}, "
                      f"Release={execution_plan['release']}")

        logger.info("Calling extract_requirements_skill...")
        requirements = self.requirements_tool.execute(user_request)
        logger.success(f"Requirements extracted ({len(requirements)} chars)")

        logger.info("Calling create_task_list_skill...")
        tasks = self.tasks_tool.execute(user_request, requirements)
        logger.success(f"Generated {len(tasks)} tasks")
        for i, task in enumerate(tasks, 1):
            print(f"      {i}. {task}")

        logger.info("Calling choose_stack_skill...")
        architecture = self.stack_tool.execute(user_request, execution_plan)
        logger.success("Technology stack chosen")

        project_id = state["project"].get("project_id") or slugify(user_request)
        project_dir = project_dir_for(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Project directory: {project_dir}")

        # Set every stage's status right now, from the execution plan alone.
        # Database/backend/frontend/deployment start "skipped" and flip to
        # "pending" only if the plan actually calls for them; testing is
        # "pending" whenever there's anything at all to verify.
        stage_status = {
            "database": "pending" if execution_plan.get("database") or execution_plan.get("contract") else "skipped",
            "backend": "pending" if execution_plan.get("backend") else "skipped",
            "frontend": "pending" if execution_plan.get("frontend") else "skipped",
            "testing": "pending" if execution_plan.get("backend") or execution_plan.get("frontend") else "skipped",
            "deployment": "pending" if execution_plan.get("release") else "skipped",
        }
        for stage_name, status in stage_status.items():
            logger.stage(stage_name, status)

        logger.node_complete("planner")

        return {
            "project": {
                "project_id": project_id,
                "requirements": requirements,
                "architecture": architecture,
                "tasks": tasks,
            },
            "runtime": {
                "execution_plan": execution_plan,
                "stage_status": stage_status,
                "current_stage": "planner",
                "completed_nodes": ["planner"],
                "logs": [f"Planner: execution plan {execution_plan}, project_id={project_id}"]
            }
        }
