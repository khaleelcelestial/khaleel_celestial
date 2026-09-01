"""
Generation Strategy Engine - decides, per stage per round, whether to use
batch generation (fresh project, or a small/medium update) or incremental
tool-calling (a large project/update, AND the change looks low cross-
module-impact). Correctness always outranks token savings, per explicit
design priority: a change that LOOKS high-impact (a rename, an auth/
middleware/routing change, a shared-model change) forces batch generation
regardless of project size, since incremental editing's core risk is
missing a ripple effect in a file it never had a reason to read - the
exact bug class this pipeline has hit for real this session (a renamed
model breaking an unrelated import, a missing devDependency only used
by a file the model never re-checked).

Deliberately NOT based on the raw inline-context byte threshold alone
(core/stage_loop.py's existing_files_context() already has its own
_MAX_TOTAL_INLINE_BYTES fallback) - that's ONE signal this module combines
with others, not the sole decision factor. The size threshold below is
set to match that existing constant on purpose: past that point,
existing_files_context() is ALREADY degrading to a cruder text-matching
heuristic for what to inline, so switching to genuine tool-directed
reading at that same point is a strict improvement, not a new risk.

Designed so a future Project Index (explicitly deferred - not built in
this phase) can later replace/strengthen the plain file-count/size signal
here without changing this module's public interface (decide_strategy)
at all - a caller doesn't need to know whether "how big is this project"
was answered by counting files or by querying an index.

No structured "affected modules" signal exists to use here - Planner only
emits free-text architecture/tasks, not a structured module/entity list
(the same gap that made a strict Planner Validator unbuildable for
Backend/Frontend's own quality engines earlier this session) - so the
practical proxy for "does this look high-impact" is keyword detection
over the user's own request text and any prior validation feedback,
deliberately biased toward over-triggering: a false positive here just
costs some extra tokens on an already-cheap case; a false negative risks
a genuinely broken multi-file change shipping with a stale reference
somewhere incremental editing never looked.
"""

from enum import Enum


class Strategy(str, Enum):
    BATCH_FRESH = "batch_fresh"
    BATCH_ADAPTIVE = "batch_adaptive"
    INCREMENTAL = "incremental"


# Matches core/stage_loop.py's existing_files_context()'s own
# _MAX_TOTAL_INLINE_BYTES exactly, deliberately, not a coincidence - see
# module docstring for why that's the right point to switch strategies,
# not an arbitrary new number.
_LARGE_TOTAL_BYTES = 30_000
_LARGE_FILE_COUNT = 25

_HIGH_IMPACT_KEYWORDS = (
    "rename", "renam", "refactor", "restructure",
    "auth", "authentication", "authorization", "permission",
    "middleware", "global", "shared model", "shared schema",
    "every page", "every route", "every endpoint", "all pages", "all routes",
    "routing", "navigation structure", "design system", "theme",
)


def _looks_high_impact(user_request: str, feedback: str) -> bool:
    haystack = f"{user_request}\n{feedback}".lower()
    return any(keyword in haystack for keyword in _HIGH_IMPACT_KEYWORDS)


def _decide(is_fresh: bool, existing_files: dict, user_request: str, feedback: str):
    """Shared logic behind decide_strategy/decide_strategy_verbose - one
    place computes the decision, so the two entry points can't drift."""
    file_count = len(existing_files)
    total_bytes = sum(len(c) for c in existing_files.values())
    metadata = {"file_count": file_count, "total_bytes": total_bytes}

    if is_fresh or not existing_files:
        return Strategy.BATCH_FRESH, "fresh_project_or_no_existing_files", metadata

    if _looks_high_impact(user_request, feedback):
        return Strategy.BATCH_ADAPTIVE, "high_cross_module_impact_keywords_detected", metadata

    if file_count > _LARGE_FILE_COUNT or total_bytes > _LARGE_TOTAL_BYTES:
        return Strategy.INCREMENTAL, "project_size_exceeds_threshold", metadata

    return Strategy.BATCH_ADAPTIVE, "small_or_medium_update", metadata


def decide_strategy(is_fresh: bool, existing_files: dict, user_request: str = "",
                    feedback: str = "") -> Strategy:
    """
    is_fresh: True if this stage has no existing files yet for this
    project - nothing to be incremental ABOUT, so batch is the only
    sensible option regardless of anything else.
    existing_files: {path: content} - the stage's current on-disk files
    (workspace[stage]["files"]-shaped).
    user_request/feedback: this round's change-request text and any prior
    UT/VAL feedback - scanned for high-cross-module-impact signals.

    Returns a Strategy - capabilities/database.py, backend.py, frontend.py's
    run() branch on this to pick run_batch_generation vs. a tool-calling
    incremental edit pass (see skills/incremental_codegen.py). Resolves
    every ambiguous case toward BATCH_ADAPTIVE, never INCREMENTAL -
    correctness over token savings is not just a goal, it's the tie-
    breaker.
    """
    strategy, _reason, _metadata = _decide(is_fresh, existing_files, user_request, feedback)
    return strategy


def decide_strategy_verbose(is_fresh: bool, existing_files: dict, user_request: str = "",
                            feedback: str = "") -> tuple:
    """
    Same decision as decide_strategy, plus WHY - for observability logging
    (core/logger.py's generation_strategy()) that needs to report a reason
    and size metadata, not just the bare enum. Returns (Strategy, reason,
    metadata) where metadata is {"file_count": int, "total_bytes": int}.
    """
    return _decide(is_fresh, existing_files, user_request, feedback)
