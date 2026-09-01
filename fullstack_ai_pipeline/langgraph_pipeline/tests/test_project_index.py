"""
Deterministic tests for skills/project_index.py's summarize_project_structure -
a NO-LLM-CALL, always-current summary built specifically to close a real gap:
the "Architecture" text every generation call otherwise relies on is frozen
from the project's first build and never reflects routes/pages added later.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.project_index import summarize_project_structure


def test_empty_index_returns_empty_string():
    assert summarize_project_structure({}) == ""
    assert summarize_project_structure(None) == ""


def test_backend_routes_are_summarized():
    index = {
        "files": {
            "backend/routers/users.py": {"kind": "backend", "routes": [["/users", "get"], ["/users", "post"]]},
        }
    }
    summary = summarize_project_structure(index)
    assert "GET /users" in summary
    assert "POST /users" in summary


def test_frontend_routes_and_components_are_summarized():
    index = {
        "files": {
            "frontend/src/App.jsx": {"kind": "frontend", "routes": ["/", "/reports", "/visitors/:id/edit"],
                                     "component": "App"},
        }
    }
    summary = summarize_project_structure(index)
    assert "/reports" in summary
    assert "/visitors/:id/edit" in summary
    assert "App" in summary


def test_summary_includes_do_not_remove_guidance():
    """The summary must actively discourage dropping existing routes, not
    just list them - this is the specific instruction meant to prevent the
    regression it was built to fix."""
    index = {"files": {"backend/routers/users.py": {"kind": "backend", "routes": [["/users", "get"]]}}}
    summary = summarize_project_structure(index)
    assert "do not remove" in summary.lower()
