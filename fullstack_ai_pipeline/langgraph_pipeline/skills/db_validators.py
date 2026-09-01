"""
Structural schema validation, backed by pglast (real PostgreSQL parser
bindings - libpg_query) instead of regex. This is DB_UT's engine: proving
"the generated schema is structurally correct" - fully deterministic, no
LLM, no subjective reasoning, same contract as the old regex-based
validate_schema_skill (see skills/database_skills.py).

Design: one small Validator base class + one focused subclass per concern
(see class list below), each independently testable and each returning
plain human-readable problem strings - not a monolithic function, so a new
check is a new class, not a growing pile of regex inside one function. This
is deliberately the same shape the shared Validation Engine pattern (see
DB_VAL) reuses, and the pattern later stages (Backend/Frontend/E2E/CI-CD)
are meant to adopt too.

What's deliberately NOT attempted here, and why: validating that a DEFAULT
value or a type name is semantically valid (not just syntactically parseable)
requires a real Postgres catalog - pglast only parses, it doesn't know what
types/functions actually exist. That's exactly what the temp-Postgres
PostgreSQL Validator (a later DB_VAL addition) is for - applying the schema
to a real database is the only reliable way to catch that class of error.
Same reasoning for "inconsistent naming" - there's no universal, false-
positive-free rule for what counts as inconsistent across arbitrary domains
(CRM vs. banking vs. inventory naming conventions genuinely differ), so it's
left out rather than guessed at.
"""

from pglast import parse_sql, ast, keywords
from pglast.parser import ParseError
from pglast.enums import ConstrType

_RESERVED = {kw.lower() for kw in keywords.RESERVED_KEYWORDS}


# ============================================================================
# SCHEMA MODEL - one pass over the parse tree, built once, reused by every
# validator below so none of them re-walks the AST from scratch.
# ============================================================================

class ColumnInfo:
    def __init__(self, name: str, type_name: str, not_null: bool = False, has_default: bool = False):
        self.name = name
        self.type_name = type_name
        self.not_null = not_null
        self.has_default = has_default
        self.is_primary = False


class ForeignKeyInfo:
    def __init__(self, local_table: str, local_columns: list[str], ref_table: str, ref_columns: list[str]):
        self.local_table = local_table
        self.local_columns = local_columns
        self.ref_table = ref_table
        self.ref_columns = ref_columns


class TableInfo:
    def __init__(self, name: str):
        self.name = name
        self.columns: dict[str, ColumnInfo] = {}
        self.primary_key_columns: list[str] = []
        self.foreign_keys: list[ForeignKeyInfo] = []
        self.unique_constraints: list[list[str]] = []
        self.constraint_names: list[str] = []
        self.declared_more_than_once = False
        self.duplicate_column_names: list[str] = []
        self.created_via_create_table = False  # False if this entry only exists because an
                           # ALTER TABLE touched it - see AlterTableTargetValidator, which needs
                           # to tell that apart from "columns" (populated by ALTER too)


class IndexInfo:
    def __init__(self, name: str, table: str, columns: list[str]):
        self.name = name
        self.table = table
        self.columns = columns


class SchemaModel:
    """Built once from the raw parse tree - every validator reads from this,
    none of them re-parses or re-walks the AST independently."""

    def __init__(self):
        self.tables: dict[str, TableInfo] = {}
        self.indexes: list[IndexInfo] = []
        self.altered_tables_referenced: list[str] = []  # every table name an ALTER TABLE touches
        self.parse_error: str | None = None

    def _type_name_of(self, type_name_node) -> str:
        return type_name_node.names[-1].sval if type_name_node and type_name_node.names else "unknown"

    def _is_not_null(self, elt) -> bool:
        # NOT NULL is represented as a Constraint(contype=CONSTR_NOTNULL) in
        # the column's own constraints list, NOT via ColumnDef.is_not_null
        # (that field means something unrelated - domain/inheritance, and is
        # always False for an ordinary column regardless of NOT NULL).
        # PRIMARY KEY implies NOT NULL semantically in real Postgres even
        # though pglast doesn't emit a separate NOTNULL constraint alongside
        # it - treated as not_null here too, since that's what matters for
        # anything (e.g. CircularForeignKeyValidator) reasoning about
        # "is a row required to reference another row".
        return any(c.contype in (ConstrType.CONSTR_NOTNULL, ConstrType.CONSTR_PRIMARY)
                   for c in (elt.constraints or []))

    def _record_column_constraint(self, table: TableInfo, col_name: str, constraint) -> None:
        contype = constraint.contype
        if contype == ConstrType.CONSTR_PRIMARY:
            table.primary_key_columns.append(col_name)
        elif contype == ConstrType.CONSTR_FOREIGN and constraint.pktable:
            ref_cols = [a.sval for a in constraint.pk_attrs] if constraint.pk_attrs else [col_name]
            table.foreign_keys.append(ForeignKeyInfo(table.name, [col_name], constraint.pktable.relname, ref_cols))
        elif contype == ConstrType.CONSTR_UNIQUE:
            table.unique_constraints.append([col_name])
        if constraint.conname:
            table.constraint_names.append(constraint.conname)

    def _record_table_constraint(self, table: TableInfo, constraint) -> None:
        contype = constraint.contype
        local_cols = [k.sval for k in constraint.keys] if constraint.keys else \
                     ([a.sval for a in constraint.fk_attrs] if constraint.fk_attrs else [])
        if contype == ConstrType.CONSTR_PRIMARY:
            table.primary_key_columns.extend(local_cols)
        elif contype == ConstrType.CONSTR_FOREIGN and constraint.pktable:
            ref_cols = [a.sval for a in constraint.pk_attrs] if constraint.pk_attrs else []
            table.foreign_keys.append(ForeignKeyInfo(table.name, local_cols, constraint.pktable.relname, ref_cols))
        elif contype == ConstrType.CONSTR_UNIQUE:
            table.unique_constraints.append(local_cols)
        if constraint.conname:
            table.constraint_names.append(constraint.conname)

    def _handle_create(self, stmt) -> None:
        name = stmt.relation.relname.lower()
        table = self.tables.get(name)
        if table is None:
            table = TableInfo(name)
            self.tables[name] = table
        elif table.created_via_create_table:
            # Only a genuine second CREATE TABLE counts as a duplicate - if
            # this entry only exists so far because an earlier ALTER TABLE
            # touched it (unusual ordering, but valid), this is really the
            # FIRST real CREATE TABLE for it, not a duplicate.
            table.declared_more_than_once = True
        table.created_via_create_table = True

        seen_in_this_create = set()
        for elt in (stmt.tableElts or []):
            if isinstance(elt, ast.ColumnDef):
                col_name = elt.colname.lower()
                if col_name in seen_in_this_create:
                    table.duplicate_column_names.append(col_name)
                seen_in_this_create.add(col_name)

                type_name = self._type_name_of(elt.typeName)
                col = ColumnInfo(col_name, type_name, self._is_not_null(elt),
                                 has_default=elt.raw_default is not None)
                table.columns[col_name] = col
                for c in (elt.constraints or []):
                    self._record_column_constraint(table, col_name, c)
                    if c.contype == ConstrType.CONSTR_PRIMARY:
                        col.is_primary = True
            elif isinstance(elt, ast.Constraint):
                self._record_table_constraint(table, elt)

    def _handle_alter(self, stmt) -> None:
        from pglast.enums import AlterTableType
        name = stmt.relation.relname.lower()
        self.altered_tables_referenced.append(name)
        table = self.tables.setdefault(name, TableInfo(name))

        for cmd in (stmt.cmds or []):
            if cmd.subtype == AlterTableType.AT_AddColumn and isinstance(cmd.def_, ast.ColumnDef):
                elt = cmd.def_
                type_name = self._type_name_of(elt.typeName)
                col = ColumnInfo(elt.colname.lower(), type_name, self._is_not_null(elt),
                                 has_default=elt.raw_default is not None)
                table.columns[col.name] = col
                for c in (elt.constraints or []):
                    self._record_column_constraint(table, col.name, c)
            elif cmd.subtype == AlterTableType.AT_AddConstraint and isinstance(cmd.def_, ast.Constraint):
                self._record_table_constraint(table, cmd.def_)

    def _handle_index(self, stmt) -> None:
        table = stmt.relation.relname.lower()
        columns = [p.name for p in (stmt.indexParams or []) if getattr(p, "name", None)]
        self.indexes.append(IndexInfo(stmt.idxname, table, columns))


def build_schema_model(schema: str) -> SchemaModel:
    model = SchemaModel()
    try:
        tree = parse_sql(schema)
    except ParseError as e:
        model.parse_error = str(e)
        return model

    for raw in tree:
        stmt = raw.stmt
        if isinstance(stmt, ast.CreateStmt):
            model._handle_create(stmt)
        elif isinstance(stmt, ast.AlterTableStmt):
            model._handle_alter(stmt)
        elif isinstance(stmt, ast.IndexStmt):
            model._handle_index(stmt)
    return model


# ============================================================================
# VALIDATORS - one focused class per concern. Each .validate(model) returns
# a list of human-readable problem strings (empty = passed).
# ============================================================================

class Validator:
    name = "base"

    def validate(self, model: SchemaModel) -> list[str]:
        raise NotImplementedError


class ParseValidator(Validator):
    """Does the schema parse as valid PostgreSQL at all? Replaces the old
    naive per-statement keyword scan with a real parser - catches malformed
    SQL and unsupported syntax that a keyword-prefix check could never
    reliably detect."""
    name = "parse"

    def validate(self, model: SchemaModel) -> list[str]:
        if model.parse_error:
            return [f"Schema does not parse as valid PostgreSQL: {model.parse_error}"]
        if not model.tables:
            return ["Schema contains no CREATE TABLE statements"]
        return []


class DuplicateTableValidator(Validator):
    name = "duplicate_tables"

    def validate(self, model: SchemaModel) -> list[str]:
        return [f"table '{t.name}' is declared more than once"
                for t in model.tables.values() if t.declared_more_than_once]


class EmptyTableValidator(Validator):
    name = "empty_tables"

    def validate(self, model: SchemaModel) -> list[str]:
        return [f"table '{t.name}' has no columns" for t in model.tables.values() if not t.columns]


class PrimaryKeyValidator(Validator):
    """Every table should declare a primary key - a table with none is
    almost always a generation mistake (can't reliably reference/update/
    delete a specific row), not a deliberate design choice."""
    name = "primary_key"

    def validate(self, model: SchemaModel) -> list[str]:
        return [f"table '{t.name}' has no PRIMARY KEY" for t in model.tables.values()
                if t.columns and not t.primary_key_columns]


class DuplicateColumnValidator(Validator):
    """Same column name declared twice in the SAME CREATE TABLE body - a
    genuine generation mistake, not the legitimate "declared in CREATE,
    also touched by a later ADD COLUMN IF NOT EXISTS" idempotency pattern
    (that's cross-statement and tracked separately, not flagged here)."""
    name = "duplicate_columns"

    def validate(self, model: SchemaModel) -> list[str]:
        return [f"column '{table.name}.{col}' is declared more than once in the same CREATE TABLE"
                for table in model.tables.values() for col in table.duplicate_column_names]


class ReservedKeywordValidator(Validator):
    """Table/column names that collide with a real PostgreSQL reserved
    keyword (from pglast's own authoritative list, not a guessed list)
    require quoting everywhere they're used - a common source of "syntax
    error at or near ..." surprises later in Backend's generated SQL."""
    name = "reserved_keywords"

    def validate(self, model: SchemaModel) -> list[str]:
        problems = []
        for table in model.tables.values():
            if table.name in _RESERVED:
                problems.append(f"table name '{table.name}' is a reserved PostgreSQL keyword - quote it "
                                f"everywhere or rename it")
            for col in table.columns.values():
                if col.name in _RESERVED:
                    problems.append(f"column '{table.name}.{col.name}' is a reserved PostgreSQL keyword - "
                                    f"quote it everywhere or rename it")
        return problems


class AlterTableTargetValidator(Validator):
    """An ALTER TABLE on a table that was never actually created anywhere
    in this schema - valid-looking SQL that fails immediately in Postgres
    ("relation ... does not exist"). Confirmed, observed failure mode."""
    name = "alter_table_target"

    def validate(self, model: SchemaModel) -> list[str]:
        return [f"ALTER TABLE references \"{name}\" but no CREATE TABLE {name} exists anywhere in "
                f"this schema - the table was never actually created"
                for name in set(model.altered_tables_referenced)
                if not model.tables.get(name, TableInfo(name)).created_via_create_table]


class ForeignKeyValidator(Validator):
    """Every FOREIGN KEY's local column(s) must actually exist on the
    table that owns the constraint, and the referenced table/column(s)
    must actually exist too."""
    name = "foreign_keys"

    def validate(self, model: SchemaModel) -> list[str]:
        problems = []
        for table in model.tables.values():
            for fk in table.foreign_keys:
                missing_local = [c for c in fk.local_columns if c not in table.columns]
                if missing_local:
                    problems.append(f"FOREIGN KEY on {table.name} references its own column(s) "
                                    f"{missing_local} which {'was' if len(missing_local) == 1 else 'were'} "
                                    f"never defined in {table.name}")
                ref_table = model.tables.get(fk.ref_table.lower())
                if ref_table is None:
                    problems.append(f"FOREIGN KEY on {table.name} references table '{fk.ref_table}' "
                                    f"which does not exist anywhere in this schema")
                    continue
                missing_ref = [c for c in fk.ref_columns if c not in ref_table.columns]
                if missing_ref:
                    problems.append(f"FOREIGN KEY on {table.name} references {fk.ref_table}"
                                    f"({', '.join(fk.ref_columns)}) but column(s) {missing_ref} "
                                    f"{'does' if len(missing_ref) == 1 else 'do'} not exist on {fk.ref_table}")
        return problems


class CircularForeignKeyValidator(Validator):
    """A cycle of required (NOT NULL) foreign keys (A -> B -> A) can make
    every row impossible to insert - neither table's first row can ever
    satisfy the other's constraint. A cycle through nullable FKs is fine
    (insert with NULL first, backfill after), so only all-NOT-NULL cycles
    are flagged."""
    name = "circular_foreign_keys"

    def validate(self, model: SchemaModel) -> list[str]:
        graph = {}
        for table in model.tables.values():
            for fk in table.foreign_keys:
                if all(table.columns[c].not_null for c in fk.local_columns if c in table.columns):
                    graph.setdefault(table.name, set()).add(fk.ref_table.lower())

        problems = []
        seen_cycles = set()
        for start in graph:
            visited, stack = set(), [start]
            path = []
            def dfs(node, path):
                if node in path:
                    cycle = tuple(sorted(path[path.index(node):]))
                    if cycle not in seen_cycles and len(cycle) > 1:
                        seen_cycles.add(cycle)
                        problems.append(f"circular required (NOT NULL) foreign key chain: "
                                        f"{' -> '.join(path[path.index(node):] + [node])}")
                    return
                if node not in graph:
                    return
                for neighbor in graph[node]:
                    dfs(neighbor, path + [node])
            dfs(start, [])
        return problems


class DuplicateIndexValidator(Validator):
    name = "duplicate_indexes"

    def validate(self, model: SchemaModel) -> list[str]:
        seen = {}
        problems = []
        for idx in model.indexes:
            key = (idx.table, tuple(idx.columns))
            if key in seen:
                problems.append(f"duplicate index on {idx.table}({', '.join(idx.columns)}): "
                                f"'{seen[key]}' and '{idx.name}' cover the same columns")
            else:
                seen[key] = idx.name
        return problems


# Ordered so a more fundamental problem (doesn't parse at all) is reported
# before anything downstream that depends on having a usable model - same
# short-circuit reasoning the old regex version used.
STRUCTURAL_VALIDATORS: list[Validator] = [
    ParseValidator(),
    DuplicateTableValidator(),
    EmptyTableValidator(),
    PrimaryKeyValidator(),
    DuplicateColumnValidator(),
    ReservedKeywordValidator(),
    AlterTableTargetValidator(),
    ForeignKeyValidator(),
    CircularForeignKeyValidator(),
    DuplicateIndexValidator(),
]


def run_structural_validators(schema: str) -> list[str]:
    """Runs every validator in order, short-circuiting after ParseValidator
    if the schema doesn't even parse (nothing downstream has a usable model
    to check). Returns the combined list of problems (empty = fully valid)."""
    from core.logger import get_logger
    logger = get_logger()

    if not schema:
        return ["Schema is empty"]

    model = build_schema_model(schema)
    problems = ParseValidator().validate(model)
    logger.validator_result("database", "ut", "ParseValidator", "FAIL" if problems else "PASS",
                            issue_count=len(problems))
    if problems:
        return problems

    for validator in STRUCTURAL_VALIDATORS[1:]:
        before = len(problems)
        problems.extend(validator.validate(model))
        issue_count = len(problems) - before
        logger.validator_result("database", "ut", type(validator).__name__,
                                "FAIL" if issue_count else "PASS", issue_count=issue_count)
    return problems


# ============================================================================
# DB_VAL VALIDATORS (Phase 2) - "does the schema correctly implement the
# intended business model", not just "is it structurally valid". Same
# Validator base class/shape as the DB_UT suite above, operating on a
# richer context (schema + contract, and old-vs-new for migrations) instead
# of the schema alone. Supervisor still only ever sees ONE pass/fail from
# DatabaseCapability.check_val() - see database.py, which flattens
# run_val_validators()'s output into the exact same list[str] shape the old
# check_schema_matches_contract() returned, so no caller needed to change.
# ============================================================================

# Path segments that are legitimate API endpoints with no 1:1 table behind
# them (auth/utility/export actions) - excluded from ContractValidator's
# "API without a table" check so those don't false-positive on every
# project (every generated backend has a login endpoint; not every project
# has an "auth" table, nor should it).
_NON_RESOURCE_PATH_SEGMENTS = {"auth", "login", "logout", "token", "health", "docs", "export", "reports"}


def _normalize_resource_name(segment: str) -> str:
    return segment.strip("/").lower().replace("-", "_")


def _extract_contract_resources(openapi_spec: str) -> dict[str, set[str]]:
    """Parses openapi.yaml properly (real YAML, not substring search) into
    {resource_name: {http_methods_seen_anywhere_under_that_resource}}. The
    resource name is the first real path segment (e.g. "/visitors/{id}"
    and "/visitors" both map to "visitors") - path parameters and deeper
    segments are ignored for this purpose, since CRUD methods for one
    resource are commonly split across a collection path and an
    item-by-id path."""
    import yaml

    try:
        spec = yaml.safe_load(openapi_spec) or {}
    except yaml.YAMLError:
        return {}

    resources: dict[str, set[str]] = {}
    for path, methods in (spec.get("paths") or {}).items():
        segments = [s for s in path.split("/") if s and not s.startswith("{")]
        if not segments:
            continue
        resource = _normalize_resource_name(segments[0])
        if not isinstance(methods, dict):
            continue
        resources.setdefault(resource, set()).update(
            m.lower() for m in methods.keys() if m.lower() in ("get", "post", "put", "patch", "delete")
        )
    return resources


class ContractValidator(Validator):
    """Does every table have SOME API coverage, and does every non-utility
    API resource have a backing table? Replaces the old substring-search
    version (check_schema_matches_contract) with a real YAML parse - no
    more risk of matching a table name that happens to appear inside an
    unrelated word or comment."""
    name = "contract"

    def validate(self, model: SchemaModel, openapi_spec: str = "") -> list[str]:
        if not openapi_spec:
            return []
        resources = _extract_contract_resources(openapi_spec)
        if not resources:
            return []

        problems = []
        for table_name in model.tables:
            normalized = _normalize_resource_name(table_name)
            # Tolerant of a singular/plural mismatch (table "visitor" vs.
            # resource "visitors") - same leniency the old version had,
            # still needed since there's no reliable universal singularizer.
            if not any(normalized == r or normalized.rstrip("s") == r.rstrip("s") for r in resources):
                problems.append(f"table '{table_name}' has no matching API resource in openapi.yaml")

        table_names = {_normalize_resource_name(t) for t in model.tables}
        for resource in resources:
            if resource in _NON_RESOURCE_PATH_SEGMENTS:
                continue
            if not any(resource == t or resource.rstrip("s") == t.rstrip("s") for t in table_names):
                problems.append(f"API resource '{resource}' in openapi.yaml has no matching table in schema.sql")
        return problems


class RelationshipValidator(Validator):
    """Deterministic, schema-only relationship sanity check: a column named
    like a foreign key (e.g. "user_id") whose implied target table ("users")
    genuinely exists in this schema, but which has no actual FOREIGN KEY
    constraint declared, is very likely a missed relationship - the column
    exists but nothing enforces the relationship it's clearly meant to
    represent.

    NOTE on scope: this only checks the schema's internal consistency, not
    "does this match what the Planner said the relationships should be" -
    that would need Planner to emit structured entity/relationship data
    (it currently only emits free-text architecture + acceptance_criteria),
    the same kind of upstream addition already done once for
    acceptance_criteria. Flagged here rather than silently skipped."""
    name = "relationships"

    def validate(self, model: SchemaModel) -> list[str]:
        problems = []
        table_names = set(model.tables.keys())
        for table in model.tables.values():
            declared_fk_columns = {c for fk in table.foreign_keys for c in fk.local_columns}
            for col_name in table.columns:
                if not col_name.endswith("_id") or col_name in declared_fk_columns:
                    continue
                implied_table = col_name[:-3]  # "user_id" -> "user"
                candidates = {implied_table, implied_table + "s"}
                match = next((t for t in candidates if t in table_names and t != table.name), None)
                if match:
                    problems.append(f"column '{table.name}.{col_name}' looks like a foreign key to "
                                    f"'{match}' (table exists) but has no FOREIGN KEY constraint declared")
        return problems


class MigrationValidator(Validator):
    """Compares the schema BEFORE this round's write against the new one -
    a table or column that existed before and is now simply gone, with no
    explicit DROP statement anywhere in the new schema for it, is almost
    certainly an accidental deletion (a generation mistake), not an
    intentional change. This is exactly the class of bug that previously
    reached disk undetected (a schema revision that silently lost several
    tables, confirmed live this session) - DB_UT's per-schema structural
    checks can't catch it because the resulting schema was, by itself,
    perfectly valid SQL; only comparing it against what came before reveals
    the problem."""
    name = "migration"

    def validate(self, new_model: SchemaModel, previous_schema: str = "", new_schema: str = "") -> list[str]:
        if not previous_schema:
            return []
        old_model = build_schema_model(previous_schema)
        if old_model.parse_error:
            return []  # can't diff against something that didn't even parse

        problems = []
        new_schema_upper = new_schema.upper()
        for old_table_name, old_table in old_model.tables.items():
            if old_table_name not in new_model.tables:
                if f"DROP TABLE" in new_schema_upper and old_table_name.upper() in new_schema_upper:
                    continue  # an explicit drop for it exists somewhere - intentional
                problems.append(f"table '{old_table_name}' existed before this revision and is now "
                                f"missing entirely, with no DROP TABLE statement for it - this looks "
                                f"like an accidental deletion, not an intentional one")
                continue

            new_table = new_model.tables[old_table_name]
            for old_col_name in old_table.columns:
                if old_col_name in new_table.columns:
                    continue
                drop_marker = f"DROP COLUMN"
                if drop_marker in new_schema_upper and old_col_name.upper() in new_schema_upper:
                    continue  # an explicit drop for it exists somewhere - intentional
                problems.append(f"column '{old_table_name}.{old_col_name}' existed before this revision "
                                f"and is now missing, with no DROP COLUMN statement for it - this looks "
                                f"like an accidental deletion, not an intentional one")
        return problems


def run_val_validators(schema: str, openapi_spec: str, previous_schema: str = "",
                       run_postgres_check: bool = True, test_file_content: str = "") -> list[str]:
    """DB_VAL's engine - runs the Contract/Relationship/Migration validators
    (and, if those all pass, the real-Postgres apply check plus the
    Requirement Test Runner - see below) and flattens the result into the
    same list[str] shape the old check_schema_matches_contract() returned,
    so DatabaseCapability.check_val() (and the Supervisor, which only ever
    sees ONE pass/fail from it) needed zero changes.

    The PostgreSQL Validator (skills/db_postgres_validator.py) only runs
    when everything else already passed - it costs real wall-clock time
    (spinning up a container), so there's no point paying that cost to
    re-confirm a schema already known to be broken by a check that's free.
    run_postgres_check=False skips it entirely (e.g. for fast tests that
    don't want to touch Docker). test_file_content, if given (the shared
    Acceptance Test Generator's output - see database.py's run()), is
    executed for real against that same live database as the Requirement
    Test Runner - a failing generated test fails DB_VAL just like any
    other problem here.
    """
    from core.logger import get_logger
    logger = get_logger()

    if not schema:
        return []

    model = build_schema_model(schema)
    if model.parse_error:
        return []  # DB_UT already failed this round for the same reason - no point piling on

    problems = []
    for name, result in (
        ("ContractValidator", ContractValidator().validate(model, openapi_spec)),
        ("RelationshipValidator", RelationshipValidator().validate(model)),
        ("MigrationValidator", MigrationValidator().validate(model, previous_schema, schema)),
    ):
        problems.extend(result)
        logger.validator_result("database", "val", name, "FAIL" if result else "PASS", issue_count=len(result))

    if not problems and run_postgres_check:
        from skills.db_postgres_validator import validate_schema_against_postgres
        pg_problems = validate_schema_against_postgres(schema, test_file_content)
        problems.extend(pg_problems)
        logger.validator_result("database", "val", "PostgreSQLValidator",
                                "FAIL" if pg_problems else "PASS", issue_count=len(pg_problems))

    return problems
