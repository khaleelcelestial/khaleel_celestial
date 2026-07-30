"""
Contract tools - OpenAPI generation interface
"""

from skills.contract_skills import generate_openapi_skill


class GenerateOpenAPITool:
    """Generates OpenAPI specification from requirements - or revises an existing one for a change request."""

    def execute(self, requirements: str, tasks: list[str], schema: str = "",
               existing_spec: str = "", change_request: str = "") -> str:
        return generate_openapi_skill(requirements, tasks, schema, existing_spec, change_request)
