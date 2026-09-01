"""
Shared Acceptance Test Generator - ONE pipeline capability, not a
Database-specific one. Database is the first caller (see capabilities/
database.py's run()), but the exact same skill is meant to be called by
Backend/Frontend/E2E/CI-CD later with their OWN stage_context/test_guidance
- none of those stages should ever grow their own copy of this logic.

What makes this "shared": every stage passes the same three Planner
artifacts (acceptance_criteria, architecture, tasks) - the business-level
source of truth - plus two stage-specific strings: `stage_context` (the
actual artifact being tested this round - schema.sql for Database, a
summary of written files for Backend/Frontend, etc.) and `test_guidance`
(how tests for THIS stage should actually connect to/exercise that
artifact - a real DB connection for Database, real HTTP calls for
Backend, etc.). The generation prompt itself, the "no trivial assertions"
rule, and the output contract are identical for every caller.
"""

from core.model_router import get_router
from core.logger import get_logger
from skills.text_utils import extract_code_block


def generate_acceptance_tests_skill(stage: str, stage_context: str, acceptance_criteria: list[str],
                                    architecture: str, tasks: list[str], test_guidance: str,
                                    output_format: str = "python pytest", code_lang: str = "python") -> str:
    """
    Generates one complete test file verifying BUSINESS requirements
    (Planner's acceptance criteria) against a stage's real artifact - not
    implementation-detail assertions like "the function exists" or
    "the response is not None".

    output_format/code_lang let a caller ask for something other than
    Python pytest (e.g. Frontend asking for a Vitest/React Testing Library
    file) without changing the default behavior for existing callers
    (Database/Backend) - output_format only changes the wording of the
    prompt's own instructions, code_lang only changes which fenced-code-
    block language extract_code_block looks for (falls back to a bare
    fence if the model tags it differently, so this is non-breaking either
    way).

    Returns the test file source as a string - empty string if there are
    no acceptance criteria to test, or if generation fails (fails open:
    callers should treat that as "no tests generated this round", not a
    reason to fail the stage).
    """
    if not acceptance_criteria:
        return ""

    logger = get_logger()
    logger.step("🧪", f"Generating acceptance tests for {stage}...")
    router = get_router()

    criteria_list = "\n".join(f"- {c}" for c in acceptance_criteria)

    system_prompt = f"""You are a QA engineer writing a real, executable {output_format} file that verifies
BUSINESS requirements, not implementation details.

{test_guidance}

Rules:
- Write ONLY real, meaningful assertions that actually check the acceptance criteria below - never a
  trivial assertion like "assert True" or "assert result is not None" with no real check behind it.
- Each acceptance criterion doesn't need its own test one-to-one - group related criteria into one test
  where that's the more natural way to verify them together (e.g. a full CRUD lifecycle in one test).
- Skip a criterion entirely (do not write a fake/trivial test for it) if the context given genuinely
  doesn't provide enough information to test it for real - a missing test is honest, a fake one is not.
- Output ONLY the complete {output_format} file - no markdown formatting, no prose explanation before or
  after it."""

    user_content = f"""Architecture:
{architecture}

Tasks:
{tasks}

Acceptance criteria to verify:
{criteria_list}

{stage_context}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        response = router.invoke("generate_acceptance_tests", messages)
    except RuntimeError as e:
        logger.warning(f"Acceptance test generation unavailable: {str(e)[:150]}")
        return ""

    code = extract_code_block(response.strip(), code_lang)
    logger.success(f"Generated acceptance tests for {stage} ({len(code)} chars)")
    return code
