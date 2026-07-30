"""
Test suite for the 5 canonical requests, adapted for the supervisor workflow.

Unlike the old fixed-graph pipeline, the supervisor decides agent ordering
dynamically (and can send work back to an earlier agent), so these tests
check invariants - the right agents ran, given the plan, and produced real
output - rather than one exact fixed node sequence.
"""

import os
import sys
import pytest

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from main import initialize_state, app


def _run(user_request: str, thread_id: str) -> dict:
    initial_state = initialize_state(user_request)
    config = {"configurable": {"thread_id": thread_id}}

    for _ in app.stream(initial_state, config):
        pass

    # app.stream() in the default "updates" mode yields only each node's own
    # partial return per step, not the accumulated state - pull the real
    # final state (after reducers merged every node's writes) from the
    # checkpointer instead.
    return app.get_state(config).values


class TestCanonicalRequests:
    """Test all 5 canonical request paths."""

    def test_1_database_schema_only(self):
        """
        Request: "Design a PostgreSQL schema for a library system."
        Expected: database=True, contract=False, backend=False, frontend=False, release=False
        """
        user_request = "Design a PostgreSQL schema for a library system."
        final_state = _run(user_request, "test_1")

        plan = final_state["runtime"]["execution_plan"]
        assert plan["database"] is True
        assert plan["backend"] is False
        assert plan["frontend"] is False
        assert plan["release"] is False

        completed = final_state["runtime"]["completed_nodes"]
        assert "planner" in completed
        assert "database" in completed
        assert "backend" not in completed
        assert "frontend" not in completed
        assert "deployment" not in completed

        assert final_state["project"]["workspace"]["database"]["schema"] != ""

        print(f"✓ Test 1 passed: {user_request}")

    def test_2_database_and_contract(self):
        """
        Request: "Generate an OpenAPI specification for a bookstore API."
        Expected: database=True, contract=True, backend=False, frontend=False, release=False
        """
        user_request = "Generate an OpenAPI specification for a bookstore API."
        final_state = _run(user_request, "test_2")

        plan = final_state["runtime"]["execution_plan"]
        assert plan["database"] is True
        assert plan["contract"] is True
        assert plan["backend"] is False
        assert plan["frontend"] is False

        completed = final_state["runtime"]["completed_nodes"]
        assert "planner" in completed
        assert "database" in completed
        assert "deployment" not in completed

        assert final_state["project"]["workspace"]["database"]["schema"] != ""
        assert final_state["project"]["workspace"]["contract"]["openapi_spec"] != ""

        print(f"✓ Test 2 passed: {user_request}")

    def test_3_backend_api(self):
        """
        Request: "Build a FastAPI REST API for a library system."
        Expected: database=True, contract=True, backend=True, frontend=False, release=True
        """
        user_request = "Build a FastAPI REST API for a library system."
        final_state = _run(user_request, "test_3")

        plan = final_state["runtime"]["execution_plan"]
        assert plan["database"] is True
        assert plan["backend"] is True
        assert plan["frontend"] is False
        assert plan["release"] is True

        completed = final_state["runtime"]["completed_nodes"]
        assert "planner" in completed
        assert "database" in completed
        assert "backend" in completed
        assert "deployment" in completed

        assert final_state["project"]["workspace"]["database"]["schema"] != ""
        assert final_state["project"]["workspace"]["contract"]["openapi_spec"] != ""
        assert len(final_state["project"]["workspace"]["backend"]["files"]) > 0
        assert final_state["runtime"]["final_project_path"] != ""

        print(f"✓ Test 3 passed: {user_request}")

    def test_4_frontend_only(self):
        """
        Request: "Build a React dashboard for employee analytics."
        Expected: database=False, contract=True, backend=False, frontend=True, release=True
        """
        user_request = "Build a React dashboard for employee analytics."
        final_state = _run(user_request, "test_4")

        plan = final_state["runtime"]["execution_plan"]
        assert plan["database"] is False
        assert plan["frontend"] is True
        assert plan["release"] is True

        completed = final_state["runtime"]["completed_nodes"]
        assert "planner" in completed
        assert "frontend" in completed
        assert "backend" not in completed
        assert "deployment" in completed

        assert len(final_state["project"]["workspace"]["frontend"]["files"]) > 0
        assert final_state["runtime"]["final_project_path"] != ""

        print(f"✓ Test 4 passed: {user_request}")

    def test_5_fullstack(self):
        """
        Request: "Build a full-stack expense tracker using React and FastAPI."
        Expected: database=True, contract=True, backend=True, frontend=True, release=True
        """
        user_request = "Build a full-stack expense tracker using React and FastAPI."
        final_state = _run(user_request, "test_5")

        plan = final_state["runtime"]["execution_plan"]
        assert plan["database"] is True
        assert plan["backend"] is True
        assert plan["frontend"] is True
        assert plan["release"] is True

        completed = final_state["runtime"]["completed_nodes"]
        assert "planner" in completed
        assert "database" in completed
        assert "backend" in completed
        assert "frontend" in completed
        assert "deployment" in completed

        # Backend and frontend files must both survive (each written under its
        # own subdirectory by its own agent - no write-conflict possible).
        assert len(final_state["project"]["workspace"]["backend"]["files"]) > 0
        assert len(final_state["project"]["workspace"]["frontend"]["files"]) > 0
        assert final_state["runtime"]["final_project_path"] != ""

        print(f"✓ Test 5 passed: {user_request}")


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v", "-s"])
