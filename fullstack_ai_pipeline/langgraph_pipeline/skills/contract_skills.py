"""
Contract skills - OpenAPI specification generation
"""

from core.model_router import get_router
from core.logger import get_logger
from skills.text_utils import extract_code_block


def generate_openapi_skill(requirements: str, tasks: list[str], schema: str = "",
                           existing_spec: str = "", change_request: str = "") -> str:
    """
    Generate OpenAPI specification from requirements and optional schema - or,
    when existing_spec is provided (revising an established project, not the
    initial build), revise it to satisfy change_request instead of
    regenerating from scratch, since backend/frontend code already implements
    the current paths exactly.
    Returns: OpenAPI YAML as string
    """

    router = get_router()

    if existing_spec:
        system_prompt = """You are an API architect revising an EXISTING OpenAPI spec that real
backend and frontend code already implements. Apply ONLY the change described below - add/alter/remove
exactly the paths/schemas it requires, and leave every other path, field, and naming convention
(including whatever prefix, or lack of one, the existing spec already uses) exactly as it already is.
Do not regenerate the spec from scratch.

Output the COMPLETE resulting spec (existing paths + your changes merged together), as valid OpenAPI
3.0 YAML only. Output ONLY YAML, no markdown formatting."""
        context = (f"Existing OpenAPI spec:\n{existing_spec}\n\n"
                  f"Requested change:\n{change_request}\n\n"
                  f"Database Schema:\n{schema}\n\n"
                  f"Original requirements (context only, the change above takes priority):\n{requirements}")
    else:
        system_prompt = """You are an API architect. Generate a complete OpenAPI 3.0 specification based on requirements and tasks.

Output ONLY valid YAML for the OpenAPI spec. Include:
- openapi version (3.0.0)
- info section (title, version, description)
- servers section
- paths with operations (GET, POST, PUT, DELETE as needed)
- request/response schemas in components
- Appropriate HTTP status codes
- Authentication scheme if applicable

Paths: mount resources at the root, e.g. "/notes", "/notes/{id}" - do NOT add a "/api" or any other
top-level prefix. This spec is the single source of truth the backend and frontend agents both
implement paths from EXACTLY as written here - inventing a prefix here forces it into both of them,
even though neither expects one.

Use RESTful conventions. Be comprehensive. Output ONLY YAML, no markdown formatting."""
        context = f"Requirements:\n{requirements}\n\nTasks:\n{tasks}"
        if schema:
            context += f"\n\nDatabase Schema:\n{schema}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context}
    ]

    try:
        openapi_spec = router.invoke("generate_openapi", messages)
    except RuntimeError as e:
        get_logger().warning(f"OpenAPI generation unavailable: {str(e)[:150]}")
        return existing_spec

    return extract_code_block(openapi_spec, "yaml")
