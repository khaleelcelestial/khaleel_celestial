"""
Database tools - Interface for database operations
"""

from skills.database_skills import generate_schema_skill, validate_schema_skill


class GenerateSchemaTool:
    """Generates database schema from requirements - or revises an existing one for a change request."""

    def execute(self, requirements: str, tasks: list[str], existing_schema: str = "",
               change_request: str = "") -> str:
        return generate_schema_skill(requirements, tasks, existing_schema, change_request)


class ValidateSchemaTool:
    """Validates schema syntax."""
    
    def execute(self, schema: str) -> tuple[bool, str]:
        return validate_schema_skill(schema)
