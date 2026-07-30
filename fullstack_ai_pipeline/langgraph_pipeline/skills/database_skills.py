"""
Database skills - Schema generation and validation
"""

import re

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


_TABLE_LEVEL_KEYWORDS = ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT")


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    """Split on sep, but only outside parentheses - so CHECK(status IN (a, b, c))
    doesn't get split into three pieces."""
    parts = []
    depth = 0
    current = []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


# Postgres allows "ALTER TABLE IF EXISTS name ..." (valid, and exactly the
# idempotent pattern this module's own generation prompts now ask for - see
# generate_schema_skill) - every regex below that parses a table name out of
# an ALTER TABLE statement must skip that optional clause first, or it
# mis-captures the literal word "IF" as if it were the table name. Confirmed
# live: this was a real bug, not a hypothetical - the model followed the
# idempotency guidance, wrote "ALTER TABLE IF EXISTS tasks ...", and the
# unpatched regex reported a phantom "no CREATE TABLE IF exists" error.
_ALTER_TABLE_NAME = r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?[\"']?(\w+)[\"']?"

_ADD_COLUMN_PATTERN = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?[\"']?(\w+)[\"']?\s+ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?[\"']?(\w+)[\"']?",
    re.IGNORECASE,
)


def _extract_table_columns(schema: str) -> dict[str, set[str]]:
    """table_name (lowercase) -> set of its column names (lowercase). A
    column can be defined either in its CREATE TABLE body, or added later via
    a separate "ALTER TABLE t ADD COLUMN c ..." statement (a common,
    legitimate generated-schema pattern: add the column first, then a
    further statement adds a FOREIGN KEY constraint on it) - both count,
    regardless of which order they appear in the file. Table-level
    constraint lines (PRIMARY KEY/FOREIGN KEY/UNIQUE/CHECK/CONSTRAINT) inside
    a CREATE TABLE body are skipped - only real column definitions
    contribute a name."""
    tables = {}
    for stmt in schema.split(";"):
        stmt = stmt.strip()
        # search, not match - a CREATE TABLE statement is commonly preceded
        # by a "-- comment" line explaining the table, so it isn't
        # necessarily the first thing in this chunk.
        create_match = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"']?(\w+)[\"']?\s*\(", stmt, re.IGNORECASE)
        if create_match:
            table_name = create_match.group(1).lower()
            body_start = stmt.index("(", create_match.end() - 1)
            body = stmt[body_start + 1: stmt.rfind(")")]
            columns = tables.setdefault(table_name, set())
            for field in _split_top_level(body):
                first_word = field.strip().split(None, 1)[0].strip('"\'') if field.strip() else ""
                if not first_word or first_word.upper() in _TABLE_LEVEL_KEYWORDS:
                    continue
                columns.add(first_word.lower())
            continue

        add_column_match = _ADD_COLUMN_PATTERN.search(stmt)
        if add_column_match:
            table_name, column_name = add_column_match.group(1).lower(), add_column_match.group(2).lower()
            if column_name.upper() not in _TABLE_LEVEL_KEYWORDS:
                tables.setdefault(table_name, set()).add(column_name)
    return tables


def _check_altered_tables_exist(schema: str) -> list[str]:
    """
    Real, observed failure mode - worse than a bad FK, and NOT caught by
    _check_foreign_keys: an "ALTER TABLE tasks ADD COLUMN ..." (or
    "... ADD CONSTRAINT ...") for a table that was never actually created by
    a CREATE TABLE anywhere in the script (confirmed live: a schema revision
    ended up with only "CREATE TABLE users", plus "ALTER TABLE tasks ADD
    COLUMN created_by ..." referencing a `tasks` table that flat-out doesn't
    exist). This is invalid on its own regardless of FK correctness - Postgres
    fails immediately with "relation \"tasks\" does not exist". The prior
    version of this validator didn't catch it because _extract_table_columns
    registers a table as "known" the moment ANY "ADD COLUMN" statement
    mentions it (needed so a legitimate "ADD COLUMN then ADD CONSTRAINT"
    two-step, THE SAME PATTERN, validates correctly when the table WAS
    created) - it never separately checked that the table was created in the
    first place. This checks that specifically.

    Returns a list of human-readable problems (empty if none).
    """
    created = set()
    altered = {}  # table_name (lower) -> the exact statement's table token, for the message
    for stmt in schema.split(";"):
        stmt = stmt.strip()
        # The trailing \s*\( is required, not cosmetic - without it this
        # regex is fooled by an ordinary English comment like "-- Create
        # table for tasks with primary key..." (confirmed live: it captured
        # "for" as if it were a table name, since case-insensitive "Create
        # table for" superficially resembles "CREATE TABLE <name>"). Real SQL
        # always has "(" immediately after the name; a comment never does -
        # same anchor _extract_table_columns already uses for this reason.
        create_match = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"']?(\w+)[\"']?\s*\(", stmt, re.IGNORECASE)
        if create_match:
            created.add(create_match.group(1).lower())
            continue
        alter_match = re.search(_ALTER_TABLE_NAME, stmt, re.IGNORECASE)
        if alter_match:
            altered.setdefault(alter_match.group(1).lower(), alter_match.group(1))

    return [
        f"ALTER TABLE references \"{original}\" but no CREATE TABLE {original} exists anywhere in this "
        f"schema - the table was never actually created"
        for table_lower, original in altered.items() if table_lower not in created
    ]


def _check_foreign_keys(schema: str) -> list[str]:
    """
    Real, observed failure mode: a generated schema's FOREIGN KEY constraint
    (either inline inside a CREATE TABLE, or a separate ALTER TABLE ... ADD
    CONSTRAINT) names a local column that was never actually added to the
    table being altered (confirmed live: "ALTER TABLE tasks ADD CONSTRAINT
    fk_user_id FOREIGN KEY (created_by) REFERENCES users(id)" where `tasks`
    has no `created_by` column at all). This is syntactically valid SQL - the
    old naive per-statement scan (checks a line merely STARTS WITH a known
    DDL keyword) never catches it - so it silently passed DB_UT, and the
    first place it ever surfaced was Postgres's own initdb script failing
    and the db container exiting, which nothing in the pipeline ties back to
    schema.sql - Testing just sees a generic "container exited" Docker error
    and the Supervisor has no way to know to send it back to Database. Catch
    it here instead, before it ever reaches Docker.

    Returns a list of human-readable problems (empty if none).
    """
    tables = _extract_table_columns(schema)
    problems = []

    fk_pattern = re.compile(
        r"(?:ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?[\"']?(\w+)[\"']?\s+ADD\s+CONSTRAINT\s+\w+\s+)?"
        r"FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+[\"']?(\w+)[\"']?\s*\(([^)]+)\)",
        re.IGNORECASE,
    )

    for stmt in schema.split(";"):
        alter_match = re.search(_ALTER_TABLE_NAME, stmt.strip(), re.IGNORECASE)
        create_match = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"']?(\w+)[\"']?\s*\(", stmt.strip(), re.IGNORECASE)
        owning_table = (alter_match or create_match).group(1).lower() if (alter_match or create_match) else None

        for fk_match in fk_pattern.finditer(stmt):
            explicit_table, local_cols_raw, ref_table_raw, ref_cols_raw = fk_match.groups()
            local_table = (explicit_table or owning_table or "").lower()
            ref_table = ref_table_raw.lower()
            local_cols = [c.strip().strip('"\'').lower() for c in local_cols_raw.split(",")]
            ref_cols = [c.strip().strip('"\'').lower() for c in ref_cols_raw.split(",")]

            if local_table and local_table in tables:
                missing = [c for c in local_cols if c not in tables[local_table]]
                if missing:
                    problems.append(
                        f"FOREIGN KEY on {local_table} references its own column(s) {missing} which "
                        f"{'was' if len(missing) == 1 else 'were'} never defined in {local_table} - "
                        f"add the column(s), or fix the constraint to reference an existing one"
                    )
            if ref_table in tables:
                missing = [c for c in ref_cols if c not in tables[ref_table]]
                if missing:
                    problems.append(
                        f"FOREIGN KEY references {ref_table}({ref_cols_raw.strip()}) but column(s) "
                        f"{missing} {'does' if len(missing) == 1 else 'do'} not exist on {ref_table}"
                    )
    return problems


def validate_schema_skill(schema: str) -> tuple[bool, str]:
    """
    Validate schema syntax (dry run).
    Returns: (is_valid, message)
    """

    # Basic validation - check for CREATE TABLE statements
    if not schema or "CREATE TABLE" not in schema.upper():
        return False, "Schema does not contain CREATE TABLE statements"

    # Check for basic SQL syntax issues. This is a naive per-statement
    # (split-on-";") scan, so anything that's legitimately allowed to
    # contain a bare ";" mid-statement needs to be recognized rather than
    # flagged as invalid - most commonly PL/pgSQL function/trigger bodies
    # (CREATE FUNCTION/TRIGGER ... AS $$ ... BEGIN ... RETURN ...; END; $$),
    # whose inner lines (RETURN, BEGIN, END, DECLARE, IF/ELSIF/EXCEPTION,
    # LANGUAGE, $$ dollar-quoting) aren't top-level DDL keywords on their own.
    in_function_body = False
    top_level_keywords = ("CREATE", "ALTER", "DROP", "INSERT", "COMMENT", "GRANT", "REVOKE")
    function_body_keywords = (
        "BEGIN", "END", "RETURN", "DECLARE", "IF", "ELSIF", "ELSE", "EXCEPTION",
        "LANGUAGE", "NEW", "OLD", "RAISE", "LOOP", "FOR", "WHILE", "PERFORM",
    )

    lines = schema.split(";")
    for line in lines:
        line = line.strip()
        if not line:
            continue

        upper = line.upper()

        if "$$" in line:
            # Toggles on entering/leaving a dollar-quoted function body;
            # a line can both open and close it (single-line body).
            in_function_body = in_function_body != (line.count("$$") % 2 == 1)
            continue

        if in_function_body:
            continue

        if any(upper.startswith(kw) or f" {kw}" in upper for kw in top_level_keywords):
            continue
        if any(kw in upper for kw in function_body_keywords) or line.startswith("--") or line.startswith("/*"):
            continue

        return False, f"Potentially invalid SQL statement: {line[:50]}..."

    # Syntax alone isn't enough - a FOREIGN KEY referencing a column that was
    # never actually defined, or an ALTER TABLE on a table that was never
    # actually created, are both valid-looking SQL that fails at runtime
    # (Postgres rejects them during initdb, crashing the db container) - see
    # _check_foreign_keys/_check_altered_tables_exist's docstrings for the
    # live failures these close. Table-existence is checked FIRST - a FK
    # error on a table that doesn't exist at all is redundant noise once the
    # more fundamental problem is already reported.
    table_problems = _check_altered_tables_exist(schema)
    if table_problems:
        return False, "; ".join(table_problems)

    fk_problems = _check_foreign_keys(schema)
    if fk_problems:
        return False, "; ".join(fk_problems)

    return True, "Schema syntax appears valid"
