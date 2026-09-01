"""
Bounded, deterministic SCOPE SELECTION - Mode 3 of the Generation Strategy
Engine (skills/generation_strategy.py).

REPLACES an earlier ReAct tool-calling implementation that caused
effectively quadratic token growth - confirmed live, measured: a single
round accumulated ~330K-460K input tokens against a request whose actual
unique file content totaled ~9K tokens (a ~40-50x blowup). Root cause:
LangGraph's ReAct loop resends the ENTIRE accumulated conversation -
system prompt, every prior tool call, every prior tool result, every file
already read or written - on EVERY internal step. For an N-step exchange
where each step adds ~K tokens of new content, total tokens consumed
across the whole exchange is ~K * N*(N+1)/2, not K*N - cost grows with the
SQUARE of step count, so raising the step cap (as a prior fix did, to
solve a separate "ran out of budget before writing" bug) makes this
dramatically worse, not better.

The fix is architectural, not a bigger step cap: incremental generation is
no longer a multi-turn tool-calling conversation at all. It is exactly:

    scope selection (0 or 1 cheap LLM call, paths only, no file content)
        -> deterministic file reading (a dict lookup - the caller already
           has every file's content in memory from workspace sync, so
           this is free, not a tool call)
        -> ONE generation call (skills/batch_codegen.py's existing
           @@@FILE@@@/@@@DELETE@@@ mechanism, reused verbatim - no new
           output format, no new parser)
        -> deterministic write (batch_codegen.write_batch_files, already
           has no-op-write detection)

This module now owns ONLY the first step. Capabilities/backend.py and
frontend.py call select_generation_scope() to get a SUBSET of their
existing_files dict, then feed that subset through the exact same
existing_files_context() -> run_batch_generation() -> parse_batch_response()
-> write_batch_files() pipeline BATCH_ADAPTIVE already uses - "incremental"
and "batch" are now the same code path, differing only in how much of the
project gets inlined into that one generation call.

ReAct tool-calling itself is NOT removed from the pipeline - core/
agent_runtime.py's run_tool_agent and skills/agent_tools.py's tool set
still back CICD's deployment_agent, where genuinely adaptive, unpredictable
steps (docker compose ps, read logs only for services that are actually
unhealthy) are the correct shape. Code generation/editing just never
needed that shape - the set of "files this change might touch" is knowable
up front, which is exactly what makes deterministic scope selection safe.
"""

import json
import re

from core.model_router import get_router
from core.logger import get_logger
from skills.text_utils import extract_code_block

# Deliberately generic, low-precision words excluded from index keyword
# lookup - matching these against the Project Index would return noise
# (e.g. "please"/"should" aren't going to name a real entity), not a
# useful head start. Not trying to be exhaustive - false positives here
# just cost one extra, harmless find_module() lookup, not a real problem.
_GENERIC_REQUEST_WORDS = {
    "please", "should", "would", "could", "needs", "need", "want", "make", "makes",
    "update", "updates", "change", "changes", "fix", "fixes", "add", "adds", "remove",
    "removes", "delete", "deletes", "create", "creates", "when", "where", "which", "there",
}


# App-shell/routing/entry-point files - high blast-radius (touching one
# risks the WHOLE app's navigation, not just one feature) and disproportionately
# easy for keyword matching to over-select (almost any request touches
# "pages" in some generic sense). Confirmed real, observed harm without this
# exclusion: a request purely about a dependency and one page's export
# buttons repeatedly pulled App.jsx into scope, and the generation call
# rewrote its entire routing table from scratch each time, silently
# dropping several real, working routes it had no explicit reason to touch.
# Excluded from AUTOMATIC (keyword/LLM) selection only - a file named
# LITERALLY by path in feedback/task text is still always included (see
# _named_in_text) since that's a deliberate, specific signal, not a guess.
_STRUCTURAL_BASENAMES = {"app.jsx", "app.tsx", "app.js", "main.jsx", "main.tsx", "main.js", "index.js"}
_ROUTING_SIGNAL_WORDS = {
    "route", "routes", "routing", "navigation", "nav", "page", "pages",
    "sidebar", "menu", "shell", "layout",
}


def _is_structural_file(path: str) -> bool:
    return path.rsplit("/", 1)[-1].lower() in _STRUCTURAL_BASENAMES


def _mentions_routing(text: str) -> bool:
    words = {w.lower() for w in re.findall(r"[A-Za-z_]{3,}", text or "")}
    return bool(words & _ROUTING_SIGNAL_WORDS)


def _candidate_files_from_index(project_index: dict, text: str, path_prefix: str = "") -> list:
    """
    Free (no LLM call) keyword extraction from the change-request/feedback
    text, queried against the Project Index's entity clustering (skills/
    project_index.py's find_module). Returns [] if no index is available
    or nothing matches - a normal, expected case this falls open from, not
    an error.

    The Project Index stores project-root-relative paths (e.g.
    "frontend/src/App.jsx"), but existing_files dicts here use paths
    relative to that agent's own write_prefix (e.g. "src/App.jsx") - a real,
    confirmed bug: without stripping path_prefix, EVERY index candidate
    permanently fails the caller's `p in existing_files` lookup, so this
    "free" signal silently resolved to zero matches on every single call,
    every candidate got discarded, and the resulting empty scope could
    reach a live generation call with NO existing file content at all. Also
    drops any candidate that belongs to the other side entirely (e.g. a
    backend path surfacing during a frontend lookup) rather than leaving it
    in as permanently-unmatchable noise.
    """
    if not project_index:
        return []
    from skills.project_index import find_module

    words = {w.lower() for w in re.findall(r"[A-Za-z_]{4,}", text)} - _GENERIC_REQUEST_WORDS
    candidates = set()
    for word in words:
        entity = find_module(project_index, word)
        for paths in entity.values():
            candidates.update(paths)

    if path_prefix:
        candidates = {p[len(path_prefix):] for p in candidates if p.startswith(path_prefix)}

    if not _mentions_routing(text):
        candidates = {p for p in candidates if not _is_structural_file(p)}
    return sorted(candidates)


def _named_in_text(existing_files: dict, haystack: str) -> list:
    """Free (no LLM call): a file the feedback/task text already names by
    path is always in scope - the strongest possible signal, and exactly
    what a UT/VAL repair round's feedback provides."""
    return [p for p in existing_files if p and p in haystack]


# Cap on how many files a single generation call inlines even after scope
# selection - mirrors core/stage_loop.py's own _MAX_INLINE_FEEDBACK_FILES
# reasoning: scope selection can occasionally over-select (a common word
# matching many modules); this is a backstop, not the primary control.
_MAX_SCOPED_FILES = 12


def _select_scope_via_llm(all_paths: list, task_context: str, hint: list) -> list:
    """
    ONE cheap LLM call, used ONLY when free deterministic signals (Project
    Index + literal path mentions) found nothing - paths only, NEVER file
    content, so this call stays small (a few hundred tokens) regardless of
    project size. Returns [] on any failure (unavailable credentials,
    unparseable response) rather than guessing - the caller's own fallback
    (name-matched files, however few) still applies.
    """
    logger = get_logger()
    router = get_router()
    routing_relevant = _mentions_routing(task_context)

    system_prompt = """You are selecting which EXISTING files are relevant to a code change - you do
NOT write any code here, only decide which files plausibly need to be read/edited. Respond with
ONLY a JSON array of file paths, copied EXACTLY from the list given - no prose, no markdown fence
needed (but one is fine if you include it). Include a file only if it plausibly needs to be read or
changed for this request. When genuinely uncertain, prefer including a file over omitting it - the
generation step that follows will decide whether it truly needs to change. Return at most 12 paths.

IMPORTANT: app-shell/routing/entry-point files (App.jsx, main.jsx, index.js, and similar) are HIGH
RISK to include - touching one risks the whole app's navigation, not just the one feature being asked
about. Do NOT include any of these UNLESS the change explicitly requires adding, removing, or
restructuring a route, page, or the navigation/sidebar itself."""
    listing = "\n".join(sorted(all_paths))
    user_content = f"""Change needed:
{task_context[:2000]}
""" + (f"\nProject Index suggests these are related: {', '.join(hint)}" if hint else "") + f"""

All files available (choose only from this exact list):
{listing}"""

    try:
        response = router.invoke("select_generation_scope", [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ])
    except RuntimeError as e:
        logger.warning(f"  Scope selection call unavailable, falling back to free signals only: {str(e)[:150]}")
        return []

    try:
        text = extract_code_block(response.strip(), "json") or response.strip()
        paths = json.loads(text)
        if not isinstance(paths, list):
            return []
        valid = set(all_paths)
        selected = [p for p in paths if isinstance(p, str) and p in valid]
        # Hard backstop, not just a prompt instruction - a model ignoring
        # the instruction above must not be able to reintroduce the exact
        # regression this whole mechanism exists to prevent.
        if not routing_relevant:
            selected = [p for p in selected if not _is_structural_file(p)]
        return selected[:_MAX_SCOPED_FILES]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def select_generation_scope(existing_files: dict, task_context: str, feedback: str,
                            project_index: dict = None, extra_text: str = "", path_prefix: str = "") -> dict:
    """
    Returns a SUBSET of existing_files ({path: content}) - the files a
    generation call actually needs inlined, deterministically selected
    wherever possible:

      1. Any path literally named in feedback/task text - free, and the
         strongest signal a UT/VAL repair round can give.
      2. Project Index candidates for the request's keywords - free.
      3. ONLY if both of those find nothing: one cheap LLM call over file
         PATHS (never content) to pick likely candidates.

    path_prefix ("frontend/" or "backend/") tells the Project Index lookup
    (step 2) how to translate its project-root-relative paths back into
    this agent's own existing_files key format - see
    _candidate_files_from_index's docstring for the real incident this
    fixed (every index candidate silently failing to match, every call).

    Never reads or sends whole-repository content for scope selection
    itself - this function decides WHICH files to read, it never reads
    more than the final selection requires (the caller does one dict
    lookup per selected path, already in memory).
    """
    logger = get_logger()
    if not existing_files:
        return {}

    haystack = f"{feedback}\n{extra_text or ''}"
    named = _named_in_text(existing_files, haystack)
    index_candidates = _candidate_files_from_index(project_index, extra_text or "", path_prefix)
    combined = sorted(set(named) | set(index_candidates))
    signal_source = "index/text match" if (named or index_candidates) else None

    if not combined:
        combined = _select_scope_via_llm(sorted(existing_files.keys()), task_context, index_candidates)
        if combined:
            signal_source = "LLM selection"

    scoped = {p: existing_files[p] for p in combined if p in existing_files}

    # A non-empty `combined` does NOT guarantee a non-empty `scoped`: paths
    # named in feedback/index candidates were checked against existing_files
    # already inside _named_in_text (so those always match), but this is
    # not true after the LLM fallback's own hallucination/mismatch risk is
    # ruled out elsewhere - the real, confirmed failure mode here was index
    # candidates going stale (referencing files that were since renamed or
    # never matched the live existing_files dict), which silently produced
    # a 0-file `scoped` while `combined` itself looked non-empty, so the
    # emptiness check below MUST run on `scoped`, not `combined` - checking
    # `combined` alone let a real generation call proceed with ZERO existing
    # file content and no visibility into the current app, and it responded
    # by inventing ~two-thirds of the frontend from scratch, destroying real
    # work. Confirmed via a live incident, not theoretical.
    if not scoped:
        # Every deterministic and LLM-based signal came up empty (or didn't
        # survive the existing_files lookup) - proceed with an intentionally
        # SMALL, non-empty fallback rather than either "the whole project"
        # (defeats the point of scoping) or true zero (the generation call
        # would have no visibility into the current app at all). Picking the
        # smallest files first keeps this fallback's own cost bounded.
        fallback_pool = existing_files if _mentions_routing(extra_text or "") else \
            {p: c for p, c in existing_files.items() if not _is_structural_file(p)} or existing_files
        combined = sorted(fallback_pool, key=lambda p: len(fallback_pool[p]))[:_MAX_SCOPED_FILES]
        scoped = {p: existing_files[p] for p in combined if p in existing_files}
        signal_source = "bounded fallback - no free/LLM signal survived"
        logger.warning(f"  Scope selection found no signal at all (or it didn't match any current file) - "
                       f"falling back to {len(scoped)} smallest file(s) as a bounded default")

    scoped = dict(list(scoped.items())[:_MAX_SCOPED_FILES])
    logger.info(f"  Incremental scope: {len(scoped)}/{len(existing_files)} file(s) selected ({signal_source})")
    return scoped
