"""
Deterministic tests for skills/incremental_codegen.py's select_generation_scope
- the bounded scope-selection step that replaced a ReAct tool-calling
implementation confirmed to cause quadratic token growth (see that module's
docstring). No live LLM calls except where explicitly noted/mocked - the
whole point of this design is that the deterministic (free) paths should
resolve scope for the common cases without ever reaching the LLM fallback.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.incremental_codegen import (
    select_generation_scope, _candidate_files_from_index, _named_in_text,
    _is_structural_file, _mentions_routing,
)


def test_empty_existing_files_returns_empty():
    assert select_generation_scope({}, "add a field", "") == {}


def test_file_named_in_feedback_is_selected_deterministically():
    """The strongest signal - a UT/VAL failure literally naming a path -
    must resolve with ZERO LLM calls."""
    existing = {
        "routers/users.py": "content A",
        "routers/visitors.py": "content B",
        "main.py": "content C",
    }
    feedback = "routers/users.py: imports './Users.css' but no such file exists"
    with patch("skills.incremental_codegen._select_scope_via_llm") as mock_llm:
        scoped = select_generation_scope(existing, "fix the bug", feedback)
    mock_llm.assert_not_called()
    assert set(scoped.keys()) == {"routers/users.py"}
    assert scoped["routers/users.py"] == "content A"


def test_project_index_candidates_selected_deterministically():
    existing = {"routers/organizations.py": "org code", "routers/users.py": "user code"}
    fake_index = {"version": 1, "files": {}, "entities": {}}
    with patch("skills.incremental_codegen._candidate_files_from_index",
              return_value=["routers/organizations.py"]) as mock_idx, \
         patch("skills.incremental_codegen._select_scope_via_llm") as mock_llm:
        scoped = select_generation_scope(existing, "manage organizations", "", project_index=fake_index)
    mock_idx.assert_called_once()
    mock_llm.assert_not_called()
    assert set(scoped.keys()) == {"routers/organizations.py"}


def test_llm_fallback_only_used_when_no_free_signal(monkeypatch):
    """Confirms the LLM call is the LAST resort, not the default path."""
    existing = {"a.py": "x" * 100, "b.py": "y" * 100}

    def fake_llm_select(all_paths, task_context, hint):
        assert set(all_paths) == {"a.py", "b.py"}
        return ["b.py"]

    with patch("skills.incremental_codegen._select_scope_via_llm", side_effect=fake_llm_select) as mock_llm:
        scoped = select_generation_scope(existing, "totally unrelated text", "")
    mock_llm.assert_called_once()
    assert set(scoped.keys()) == {"b.py"}


def test_llm_call_never_receives_file_content():
    """Critical guarantee from the spec: scope selection must never send
    complete source files - only paths."""
    existing = {"secret_content.py": "SENSITIVE_MARKER_12345" * 50}
    captured = {}

    def fake_invoke(skill_name, messages):
        captured["messages"] = messages
        return '["secret_content.py"]'

    with patch("skills.incremental_codegen.get_router") as mock_get_router:
        mock_get_router.return_value.invoke.side_effect = fake_invoke
        select_generation_scope(existing, "fix something", "")

    all_text = " ".join(m["content"] for m in captured["messages"])
    assert "SENSITIVE_MARKER_12345" not in all_text
    assert "secret_content.py" in all_text  # the path itself is fine to send


def test_total_failure_falls_back_to_smallest_files_not_empty_or_everything():
    """When every signal (index, text match, LLM) comes up empty, the
    fallback must be small and bounded - never true zero (nothing to work
    from) and never the whole project (defeats the point of scoping)."""
    existing = {f"file_{i}.py": "x" * (i * 100) for i in range(20)}
    with patch("skills.incremental_codegen._select_scope_via_llm", return_value=[]):
        scoped = select_generation_scope(existing, "unrelated", "")
    assert 0 < len(scoped) <= 12
    # Smallest files first - file_0 (0 bytes... well smallest non-zero) should be included
    assert "file_0.py" in scoped


def test_scope_capped_at_max_even_with_many_free_matches():
    existing = {f"routers/mod_{i}.py": "content" for i in range(30)}
    feedback = " ".join(existing.keys())  # every path literally named
    scoped = select_generation_scope(existing, "fix everything", feedback)
    assert len(scoped) <= 12


def test_candidate_files_from_index_returns_empty_without_index():
    assert _candidate_files_from_index(None, "anything") == []
    assert _candidate_files_from_index({}, "anything") == []


def test_named_in_text_matches_exact_paths_only():
    existing = {"backend/routers/users.py": "x", "backend/main.py": "y"}
    assert _named_in_text(existing, "please fix backend/routers/users.py now") == ["backend/routers/users.py"]
    assert _named_in_text(existing, "nothing relevant here") == []


# ------------------------------------------------- structural-file denylist --
# Regression tests for a real, observed bug: a request about a dependency and
# one page's export buttons repeatedly pulled App.jsx into scope, and the
# generation call rewrote its entire routing table from scratch each time,
# silently dropping several real, working routes.

def test_is_structural_file_matches_app_and_main_regardless_of_directory():
    assert _is_structural_file("frontend/src/App.jsx")
    assert _is_structural_file("src/main.tsx")
    assert _is_structural_file("App.js")
    assert not _is_structural_file("frontend/src/pages/Reports.jsx")


def test_mentions_routing_detects_routing_keywords():
    assert _mentions_routing("add a new route for the settings page")
    assert _mentions_routing("update the sidebar navigation")
    assert not _mentions_routing("fix the export buttons and add a dependency")


def test_index_candidates_exclude_structural_files_without_routing_signal():
    existing = {"frontend/src/App.jsx": "x", "frontend/src/pages/Reports.jsx": "y"}
    fake_index = {"version": 1, "files": {}, "entities": {}}
    with patch("skills.project_index.find_module",
              return_value={"anything": ["frontend/src/App.jsx", "frontend/src/pages/Reports.jsx"]}):
        scoped = select_generation_scope(existing, "add export buttons to reports", "",
                                         project_index=fake_index, extra_text="add export buttons to reports")
    assert "frontend/src/App.jsx" not in scoped
    assert "frontend/src/pages/Reports.jsx" in scoped


def test_index_candidates_include_structural_files_when_routing_is_the_ask():
    existing = {"frontend/src/App.jsx": "x", "frontend/src/pages/Reports.jsx": "y"}
    fake_index = {"version": 1, "files": {}, "entities": {}}
    with patch("skills.project_index.find_module",
              return_value={"anything": ["frontend/src/App.jsx"]}):
        scoped = select_generation_scope(existing, "add a new route for a settings page", "",
                                         project_index=fake_index, extra_text="add a new route for a settings page")
    assert "frontend/src/App.jsx" in scoped


def test_literal_path_mention_bypasses_the_denylist():
    """A file named EXPLICITLY by path in feedback (e.g. a validator saying
    'App.jsx has bug X') is a deliberate signal, not a guess - it must
    still be included even though it's structural."""
    existing = {"frontend/src/App.jsx": "x"}
    feedback = "frontend/src/App.jsx: JSX syntax error on line 12"
    scoped = select_generation_scope(existing, "fix the syntax error", feedback)
    assert "frontend/src/App.jsx" in scoped


def test_llm_selected_structural_files_filtered_when_model_ignores_instruction():
    """Hard backstop: even if the underlying model ignores the prompt
    instruction and returns App.jsx anyway, _select_scope_via_llm's own
    post-filter (not just select_generation_scope's caller-side logic)
    must still remove it - mocking at the router level (not
    _select_scope_via_llm itself) so this actually exercises that filter."""
    existing = {"frontend/src/App.jsx": "x", "frontend/src/pages/Reports.jsx": "y"}

    def fake_invoke(skill_name, messages):
        return '["frontend/src/App.jsx", "frontend/src/pages/Reports.jsx"]'

    with patch("skills.incremental_codegen.get_router") as mock_get_router:
        mock_get_router.return_value.invoke.side_effect = fake_invoke
        scoped = select_generation_scope(existing, "add export buttons", "")
    assert "frontend/src/App.jsx" not in scoped
    assert "frontend/src/pages/Reports.jsx" in scoped


# ------------------------------------------------- path_prefix mismatch --
# Regression tests for a real, observed incident: the Project Index stores
# project-root-relative paths ("frontend/src/App.jsx") but existing_files
# dicts use paths relative to the agent's own write_prefix ("src/App.jsx").
# Without stripping path_prefix, every index candidate permanently failed
# to match existing_files, `combined` still looked non-empty (so no
# fallback triggered), and `scoped` silently resolved to {} - a live
# generation call proceeded with ZERO existing file content and responded
# by inventing ~two-thirds of the frontend from scratch, destroying real,
# working code. Confirmed live, not theoretical.

def test_index_candidates_are_reprefixed_to_match_existing_files():
    existing = {"src/App.jsx": "x", "src/pages/Reports.jsx": "y"}
    fake_index = {"version": 1, "files": {}, "entities": {}}
    with patch("skills.project_index.find_module",
              return_value={"anything": ["frontend/src/pages/Reports.jsx"]}):
        scoped = select_generation_scope(existing, "add export buttons to reports", "",
                                         project_index=fake_index, extra_text="add export buttons to reports",
                                         path_prefix="frontend/")
    assert set(scoped.keys()) == {"src/pages/Reports.jsx"}


def test_scope_never_silently_empty_when_index_candidates_cannot_match_existing_files():
    """Without path_prefix (the exact bug that caused the live incident),
    index candidates can never match existing_files - this must fall back
    to the bounded smallest-files default, never return {}."""
    existing = {f"src/file_{i}.py": "x" * (i * 50 + 10) for i in range(5)}
    fake_index = {"version": 1, "files": {}, "entities": {}}
    with patch("skills.project_index.find_module",
              return_value={"anything": ["frontend/src/file_0.py"]}), \
         patch("skills.incremental_codegen._select_scope_via_llm", return_value=[]):
        scoped = select_generation_scope(existing, "unrelated request text here", "",
                                         project_index=fake_index, extra_text="unrelated request text here")
    assert scoped != {}
    assert 0 < len(scoped) <= 12
