"""
Planning tools - Interface layer between Planning capability and skills
"""

from skills.planning_skills import (
    analyze_request_skill,
    extract_requirements_skill,
    create_task_list_skill,
    choose_stack_skill
)


class AnalyzeRequestTool:
    """Analyzes user request and determines execution plan flags."""
    
    def execute(self, user_request: str) -> dict:
        return analyze_request_skill(user_request)


class ExtractRequirementsTool:
    """Extracts structured requirements from user request."""
    
    def execute(self, user_request: str) -> str:
        return extract_requirements_skill(user_request)


class CreateTaskListTool:
    """Generates concrete task list from requirements."""
    
    def execute(self, user_request: str, requirements: str) -> list[str]:
        return create_task_list_skill(user_request, requirements)


class ChooseStackTool:
    """Chooses concrete technology stack."""
    
    def execute(self, user_request: str, execution_plan: dict) -> str:
        return choose_stack_skill(user_request, execution_plan)
