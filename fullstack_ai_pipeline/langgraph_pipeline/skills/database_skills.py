"""
Database skills - Schema generation and validation
"""

from core.model_router import get_router
from core.logger import get_logger
from skills.text_utils import extract_code_block


def generate_schema_skill(requirements: str, tasks: list[str], existing_schema: str = "",
                          change_request: str = "") -> str:
    """
    Generate SQL schema from requirements - or, when existing_schema is
    provided (the Database agent is being re-invoked on an established
    project, not the initial build), revise it to satisfy change_request
    instead of regenerating from scratch. Backend/Frontend code already
    depends on the current table/column names, so an unprompted full
    rewrite would silently break everything downstream that isn't also
    being touched this round.
    Returns: SQL schema as string
    """

    router = get_router()

    if existing_schema:
        system_prompt = """You are a database architect revising an EXISTING PostgreSQL schema that
real backend code already depends on, AND that may already have real data in it in a running database -
this is a live revision, not a from-scratch rebuild. Apply ONLY the change described below - add/alter/
drop exactly what it requires, and leave every other table/column/name/type exactly as it already is. Do
not regenerate the schema from scratch and do not rename or restructure anything the change didn't ask
for. Never DROP a table/column that the requested change doesn't explicitly say to remove - existing data
compatibility matters here, this schema may be applied against a database that already has rows in it.

CRITICAL - every statement must be safe to re-run against a database that may already have some or all
of this schema applied (this schema gets re-applied on every update, not just the very first time):
- Use "CREATE TABLE IF NOT EXISTS", never a bare "CREATE TABLE", for every table - existing ones already
  in the live database must be left completely alone, not recreated (which would either error or, worse,
  silently drop existing data depending on how it's invoked).
- Use "ALTER TABLE ... ADD COLUMN IF NOT EXISTS", never a bare "ADD COLUMN" - re-running this schema
  should be a safe no-op for anything already there, and only take effect for what's genuinely new.
- A foreign key on a table that's ALSO new in this revision (its own CREATE TABLE IF NOT EXISTS is part
  of what you're outputting right now) should be defined INLINE inside that CREATE TABLE body, not as a
  separate ALTER TABLE ADD CONSTRAINT - inline means it's automatically covered by that CREATE TABLE's
  own IF NOT EXISTS, no extra idempotency pattern needed. Only use a separate ALTER TABLE ADD CONSTRAINT
  for a NEW constraint on a table that already existed BEFORE this revision (so its own CREATE TABLE
  statement can't be touched) - and even then, first drop it conditionally
  ("ALTER TABLE t DROP CONSTRAINT IF EXISTS name;") then add it fresh - "ADD CONSTRAINT" has no
  "IF NOT EXISTS" form in PostgreSQL, so this conditional-drop-then-add is the safe idempotent pattern.
- "CREATE INDEX IF NOT EXISTS" for every index, same reasoning.
- If the requested change REMOVES a column from a table that already existed BEFORE this revision (e.g.
  simplifying a table that no longer needs a field), you MUST include an explicit
  "ALTER TABLE t DROP COLUMN IF EXISTS col;" statement for it. Real, observed failure: simply leaving
  the column out of this schema's CREATE TABLE IF NOT EXISTS body does NOTHING to a table that already
  exists on the live database - CREATE TABLE IF NOT EXISTS is a complete no-op when the table is already
  there, so the old column (and any NOT NULL constraint on it) silently remains on the live table
  forever, and every INSERT that doesn't supply it then fails with a NOT NULL violation. Removing a
  column from the CREATE TABLE definition ONLY affects a future fresh volume - an explicit DROP COLUMN
  IF EXISTS is the only thing that removes it from the ALREADY-existing live table too. This applies
  even if you're also dropping a foreign key CONSTRAINT on that same column - dropping the constraint
  and dropping the column are two separate things, both required if the column itself is going away.

Output the COMPLETE resulting schema (existing tables + your changes merged together), as SQL DDL
statements only. Use PostgreSQL syntax. Output ONLY SQL, no markdown formatting."""
        user_content = (f"Existing schema:\n{existing_schema}\n\n"
                        f"Requested change:\n{change_request}\n\n"
                        f"Original requirements (context only, the change above takes priority):\n{requirements}")
    else:
        system_prompt = """You are a database architect. Generate a complete PostgreSQL schema based on requirements and tasks.

CRITICAL: Create ONLY the tables directly needed to satisfy the stated requirements and tasks below.
Do NOT add authentication/users tables, audit logs, or other "nice to have" tables unless explicitly
requested. If the requirements say "todo app", create a todos table only - nothing else. A missing
requested table is a real failure; an unrequested extra table is also a real failure (it creates a
schema/contract mismatch that blocks the entire pipeline).

Output ONLY the SQL DDL statements. Include:
- CREATE TABLE IF NOT EXISTS statements (not a bare CREATE TABLE - this schema gets safely re-applied
  against the live database EVERY time the stack deploys, including immediately after this very first
  build, not just on some future update - see below) with appropriate columns, types, and constraints
- Primary keys and foreign keys, defined INLINE inside the CREATE TABLE statement (either a column-level
  "REFERENCES other_table(col)" or a table-level "FOREIGN KEY (col) REFERENCES other_table(col)" within
  the same CREATE TABLE body) - NOT as a separate "ALTER TABLE ... ADD CONSTRAINT" statement. This
  matters even on a fresh build: a separate ADD CONSTRAINT is NOT safe to re-run (PostgreSQL has no
  "ADD CONSTRAINT IF NOT EXISTS"), and this schema WILL be re-run against the same database a second
  time immediately after this first deploy (once to initialize the empty database, once more right
  after as a safety re-apply) - a bare ADD CONSTRAINT fails the second time with "constraint already
  exists", which a constraint defined inline inside CREATE TABLE IF NOT EXISTS never hits (the whole
  statement is simply skipped if the table's already there).
- CREATE INDEX IF NOT EXISTS where appropriate
- Comments for clarity

Use PostgreSQL syntax. Output ONLY SQL, no markdown formatting."""
        user_content = f"Requirements:\n{requirements}\n\nTasks:\n{tasks}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ]

    try:
        schema = router.invoke("generate_schema", messages)
    except RuntimeError as e:
        # Fail open - on a revision, keep the existing schema rather than
        # losing it; on a fresh build, empty is already handled downstream
        # (validate_schema_skill marks it failed without crashing).
        get_logger().warning(f"Schema generation unavailable: {str(e)[:150]}")
        return existing_schema

    result = extract_code_block(schema, "sql")

    # Deterministic backstop for a revision: the system prompt above asks for
    # "the COMPLETE resulting schema (existing tables + changes merged)", but
    # a small model doesn't reliably follow that instruction - confirmed
    # live, repeatedly, across separate attempts: it emitted ONLY the delta
    # (ALTER TABLE/CREATE INDEX statements, zero CREATE TABLE) instead of a
    # self-contained script. This matters a lot here specifically: schema.sql
    # is mounted as docker-entrypoint-initdb.d, which Postgres runs ONCE
    # against a completely empty, freshly-created volume - it is never a
    # migration against an already-initialized live database. An ALTER-only
    # "revision" schema fails immediately on a fresh volume ("relation tasks
    # does not exist"), which is a guaranteed, deterministic failure, not a
    # flaky one. If the model's output has no CREATE TABLE at all, treat it
    # as a delta and prepend the existing schema's own table definitions -
    # Postgres will then apply the original CREATE TABLEs first and the
    # model's ALTER/CREATE INDEX statements after, in the same init run,
    # exactly reproducing what "merge them yourself" was supposed to produce.
    if existing_schema and result and "CREATE TABLE" not in result.upper():
        get_logger().warning("Schema revision returned only a delta (no CREATE TABLE) - prepending the "
                            "existing schema so the result stays self-contained for a fresh volume init")
        result = existing_schema.rstrip().rstrip(";") + ";\n\n" + result

    return result


def validate_schema_skill(schema: str) -> tuple[bool, str]:
    """
    Validate schema structure - fully deterministic, no LLM, no subjective
    reasoning (DB_UT's contract). Backed by pglast (real PostgreSQL parser
    bindings) instead of regex - see skills/db_validators.py for the actual
    validator suite (parse validity, duplicate/empty tables, primary keys,
    duplicate columns, reserved keywords, ALTER-on-undefined-table, foreign
    key existence, circular required FKs, duplicate indexes).

    Returns: (is_valid, message) - same contract the old regex-based
    version had, so every caller (ValidateSchemaTool, DatabaseCapability.
    check_ut) needed zero changes.
    """
    from skills.db_validators import run_structural_validators

    problems = run_structural_validators(schema)
    if problems:
        return False, "; ".join(problems)
    return True, "Schema is structurally valid"
