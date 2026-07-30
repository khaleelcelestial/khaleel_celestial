"""
Simple task skills - classify whether a request needs the full app-building
pipeline or is just a lightweight content-generation task (e.g. "write me a
.txt file explaining X"), and generate the latter directly.
"""

import json

from core.model_router import get_router
from core.logger import get_logger
from skills.text_utils import extract_code_block


def classify_intent_skill(user_request: str) -> str:
    """
    Returns "simple_file" if the request only wants one or a few standalone
    content files generated (text/markdown/notes/scripts) with no
    application to build, or "app_build" for anything involving a backend,
    frontend, or database. Defaults to "app_build" on any failure - that's
    the safer assumption given this pipeline's primary purpose.
    """
    router = get_router()

    system_prompt = """Classify the user's request into exactly one category.

SIMPLE_FILE: the request only wants one or a few standalone content files
generated (e.g. a text file, markdown notes, a single script, a document) -
no application, API, backend, frontend, or database is being built.

APP_BUILD: the request wants an application, website, API, backend, frontend,
dashboard, or anything with a database/server component - even a small one.

Output ONLY one word: SIMPLE_FILE or APP_BUILD."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_request}
    ]

    try:
        response = router.invoke("classify_intent", messages)
    except RuntimeError:
        return "app_build"

    response = response.strip().upper()
    return "simple_file" if "SIMPLE_FILE" in response else "app_build"


def generate_simple_file_skill(user_request: str) -> dict:
    """
    Generate the standalone file(s) a SIMPLE_FILE request asked for.
    Returns: {filename: content}
    """
    router = get_router()

    system_prompt = """You write standalone content files for the user's request.

Output ONLY a JSON object mapping filename to full file content:
{
  "notes.txt": "full content here...",
  "another_file.md": "..."
}

Pick sensible filenames and extensions for what was asked. Output ONLY the JSON object,
nothing else."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_request}
    ]

    try:
        response_text = router.invoke("generate_simple_content", messages)
    except RuntimeError as e:
        get_logger().warning(f"Content generation unavailable: {str(e)[:150]}")
        return {}

    response_text = extract_code_block(response_text, "json")

    try:
        files = json.loads(response_text)
    except json.JSONDecodeError as e:
        get_logger().warning(f"JSON parse error: {str(e)[:100]}")
        return {}

    if not isinstance(files, dict):
        return {}

    return files
