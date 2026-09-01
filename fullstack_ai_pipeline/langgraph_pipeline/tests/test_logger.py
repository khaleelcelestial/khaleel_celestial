"""
Deterministic tests for core/logger.py's observability layer (PIPELINE
OBSERVABILITY AND LOGGING FRAMEWORK UPGRADE). No LLM calls, no Docker, no
network - every test exercises PipelineLogger directly against a temp
directory. Run with: pytest tests/test_logger.py -v
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.logger import PipelineLogger, _redact_text, _collect_secret_values, _classify_error, _make_run_id


@pytest.fixture
def logger(tmp_path, monkeypatch):
    """A fresh PipelineLogger per test, writing under a temp dir instead of
    the real logs/runs/ - never touches the actual project's log history."""
    lg = PipelineLogger()
    monkeypatch.setattr(lg, "log_dir", None)  # start_run overwrites this
    real_base = tmp_path / "logs" / "runs"

    def fake_start_run(project_id="", mode="build"):
        import uuid
        lg.run_id = f"test-{uuid.uuid4().hex[:6]}"
        lg.project_id = project_id
        lg.run_mode = mode
        lg.start_time = lg.start_time or __import__("time").time()
        lg.log_dir = real_base / lg.run_id
        lg.log_dir.mkdir(parents=True, exist_ok=True)
        lg._emit("INFO", event="pipeline_started", status="STARTED", metadata={"mode": mode})
        return lg.run_id

    monkeypatch.setattr(lg, "start_run", fake_start_run)
    lg.start_run(project_id="test_project", mode="build")
    return lg


def _read_events(logger) -> list:
    return [json.loads(l) for l in (logger.log_dir / "pipeline.jsonl").read_text(encoding="utf-8").strip().split("\n")]


# --------------------------------------------------------------- run_id --

def test_run_id_format_and_uniqueness():
    ids = {_make_run_id() for _ in range(20)}
    assert len(ids) == 20  # no collisions across 20 rapid generations
    for run_id in ids:
        assert "-" in run_id
        date_part, time_part, suffix = run_id.split("-")
        assert len(date_part) == 8 and date_part.isdigit()
        assert len(time_part) == 6 and time_part.isdigit()
        assert len(suffix) == 4


def test_start_run_creates_log_dir_and_event(logger):
    assert logger.log_dir.exists()
    events = _read_events(logger)
    assert events[0]["event"] == "pipeline_started"
    assert events[0]["run_id"] == logger.run_id


# ------------------------------------------------------------- JSONL/schema --

def test_jsonl_one_object_per_line(logger):
    logger.info("hello")
    logger.warning("careful")
    text = (logger.log_dir / "pipeline.jsonl").read_text(encoding="utf-8")
    lines = text.strip().split("\n")
    for line in lines:
        obj = json.loads(line)  # must not raise - every line is valid standalone JSON
        assert "timestamp" in obj and "run_id" in obj and "level" in obj


def test_run_id_attached_to_every_event(logger):
    logger.info("a")
    logger.stage_started("backend", 1)
    logger.validator_result("backend", "ut", "X", "PASS")
    for e in _read_events(logger):
        assert e["run_id"] == logger.run_id


# ----------------------------------------------------------------- levels --

def test_debug_events_filtered_at_default_info_level(logger):
    logger.tool_call("read_file", "x.py")  # DEBUG-level
    logger.info("visible")                 # INFO-level
    events = [e["event"] for e in _read_events(logger)]
    assert "tool_call" not in events
    assert "info" in events


def test_debug_level_env_var_surfaces_debug_events(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_LOG_LEVEL", "DEBUG")
    lg = PipelineLogger()
    lg.log_dir = tmp_path / "run"
    lg.log_dir.mkdir()
    lg.run_id = "test"
    lg._emit("INFO", event="pipeline_started", status="STARTED")
    lg.tool_call("read_file", "x.py")
    events = [json.loads(l)["event"] for l in (lg.log_dir / "pipeline.jsonl").read_text().strip().split("\n")]
    assert "tool_call" in events


# --------------------------------------------------------------- stages --

def test_stage_events_recorded(logger):
    logger.stage_started("backend", 1)
    logger.stage_completed("backend", "PASSED")
    events = _read_events(logger)
    started = [e for e in events if e["event"] == "stage_started"]
    completed = [e for e in events if e["event"] == "stage_completed"]
    assert started and started[0]["stage"] == "backend"
    assert completed and completed[0]["status"] == "PASSED"


def test_retry_event_increments_stats(logger):
    assert logger._stats["retries"] == 0
    logger.retry("backend", 1, 3, "UT failed", issue_count=2)
    assert logger._stats["retries"] == 1
    events = [e for e in _read_events(logger) if e["event"] == "stage_retry"]
    assert events[0]["status"] == "RETRYING" and events[0]["attempt"] == 1


def test_validator_result_pass_fail_skipped(logger):
    logger.validator_result("database", "val", "ContractValidator", "FAIL", issue_count=4)
    logger.validator_result("database", "val", "RelationshipValidator", "PASS")
    logger.validator_result("testing", "val", "SecurityValidator", "SKIPPED")
    events = {e["validation"]: e for e in _read_events(logger) if e["event"] == "validator_result"}
    assert events["ContractValidator"]["status"] == "FAIL"
    assert events["ContractValidator"]["metadata"]["issue_count"] == 4
    assert events["RelationshipValidator"]["status"] == "PASS"
    assert events["SecurityValidator"]["status"] == "SKIPPED"


def test_generation_strategy_event(logger):
    logger.generation_strategy("backend", "incremental", "project_size_exceeds_threshold",
                               {"file_count": 30, "total_bytes": 40000})
    events = [e for e in _read_events(logger) if e["event"] == "generation_strategy"]
    assert events[0]["status"] == "INCREMENTAL"
    assert events[0]["metadata"]["file_count"] == 30


# ----------------------------------------------------------------- tools --

def test_tool_call_id_correlation(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPELINE_LOG_LEVEL", "DEBUG")
    lg = PipelineLogger()
    lg.log_dir = tmp_path / "run"
    lg.log_dir.mkdir()
    lg.run_id = "test"
    tcid = lg.tool_call("read_file", "main.py")
    lg.tool_result("read_file", "397 chars", tcid)
    events = [json.loads(l) for l in (lg.log_dir / "pipeline.jsonl").read_text().strip().split("\n")]
    started = next(e for e in events if e["status"] == "STARTED")
    completed = next(e for e in events if e["status"] == "COMPLETED")
    assert started["metadata"]["tool_call_id"] == completed["metadata"]["tool_call_id"] == tcid


# ----------------------------------------------------------------- files --

def test_file_changed_updates_stats_and_events(logger):
    logger.file_changed("backend", "backend/main.py", "CREATED", size_after=100)
    logger.file_changed("backend", "backend/models.py", "MODIFIED", size_before=50, size_after=80)
    logger.file_changed("backend", "backend/old.py", "DELETED", size_before=20)
    logger.file_changed("backend", "backend/same.py", "SKIPPED", size_before=10, size_after=10)
    assert logger._stats["files_created"] == 1
    assert logger._stats["files_modified"] == 1
    assert logger._stats["files_deleted"] == 1
    assert logger._stats["files_unchanged"] == 1
    events = [e for e in _read_events(logger) if e["event"].startswith("file_")]
    assert len(events) == 4


# -------------------------------------------------------------- deployment --

def test_deployment_and_rollback_events(logger):
    logger.deployment("docker_build", "COMPLETED", {"detail": "ok"})
    logger.rollback("STARTED", {"services": ["backend"]})
    logger.rollback("COMPLETED", {"services": ["backend"]})
    events = _read_events(logger)
    deploy = [e for e in events if e["event"] == "docker_build"]
    rollback = [e for e in events if e["event"] == "rollback"]
    assert deploy[0]["status"] == "COMPLETED"
    assert [e["status"] for e in rollback] == ["STARTED", "COMPLETED"]


# ------------------------------------------------------------------ errors --

def test_error_event_has_classification_fields(logger):
    logger.error("boom", error_type="BUILD_ERROR", stage="backend")
    events = [e for e in _read_events(logger) if e["event"] == "error"]
    assert events[0]["error_type"] == "BUILD_ERROR"
    assert events[0]["stage"] == "backend"


@pytest.mark.parametrize("message,expected", [
    ("Error 429: rate limit exceeded", "rate_limit"),
    ("Request timed out after 30s", "timeout"),
    ("401 Unauthorized - invalid API key", "auth_error"),
    ("500 Internal Server Error", "server_error"),
    ("something totally unrelated broke", "unknown"),
])
def test_classify_error(message, expected):
    assert _classify_error(message) == expected


def test_model_attempt_failed_classifies_and_tracks_fallback(logger):
    logger.model_attempt_failed(0, "429 rate limit", will_retry=True, provider="groq", model="skill_x")
    assert logger._stats["fallbacks"] == 1
    events = [e for e in _read_events(logger) if e["event"] == "model_attempt" and e["status"] == "FAILED"]
    assert events[0]["error_type"] == "rate_limit"
    assert events[0]["metadata"]["fallback"] is True


# --------------------------------------------------------------- redaction --

def test_redact_text_masks_exact_secret_value():
    secret = "sk-abcdefghijklmnop"
    text = f"Authorization used key {secret} for this call"
    redacted = _redact_text(text, [secret])
    assert secret not in redacted
    assert "***REDACTED***" in redacted


def test_redact_text_masks_bearer_token():
    text = "sent header Authorization: Bearer abc123XYZ.def456"
    redacted = _redact_text(text, [])
    assert "abc123XYZ" not in redacted
    assert "Bearer ***REDACTED***" in redacted


def test_redact_text_masks_connection_string_password():
    text = "connecting to postgresql://postgres:supersecret@db:5432/mydb"
    redacted = _redact_text(text, [])
    assert "supersecret" not in redacted
    assert "postgresql://***:***@" in redacted


def test_collect_secret_values_only_matches_secret_like_names(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY_A1", "gsk_realsecretvalue123")
    monkeypatch.setenv("SOME_HARMLESS_VAR", "not_a_secret_but_long_enough")
    monkeypatch.setenv("SHORT_KEY", "abc")  # too short (<6 chars) - excluded
    values = _collect_secret_values()
    assert "gsk_realsecretvalue123" in values
    assert "not_a_secret_but_long_enough" not in values
    assert "abc" not in values


def test_emit_never_leaks_secret_into_jsonl(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_API_KEY", "sk-leaktest-abcdef123456")
    lg = PipelineLogger()
    lg.log_dir = tmp_path / "run"
    lg.log_dir.mkdir()
    lg.run_id = "test"
    lg.error("Call failed with key sk-leaktest-abcdef123456 in the payload")
    raw = (lg.log_dir / "pipeline.jsonl").read_text(encoding="utf-8")
    assert "sk-leaktest-abcdef123456" not in raw
    assert "REDACTED" in raw


# ------------------------------------------------------------ run isolation --

def test_two_runs_get_separate_log_dirs(tmp_path, monkeypatch):
    lg = PipelineLogger()
    real_base = tmp_path / "logs" / "runs"

    def start(project_id="", mode="build"):
        import uuid
        lg.run_id = f"test-{uuid.uuid4().hex[:8]}"
        lg.log_dir = real_base / lg.run_id
        lg.log_dir.mkdir(parents=True)
        return lg.run_id

    id1 = start()
    dir1 = lg.log_dir
    id2 = start()
    dir2 = lg.log_dir
    assert id1 != id2
    assert dir1 != dir2
    assert dir1.exists() and dir2.exists()


# -------------------------------------------------------------- run summary --

def test_pipeline_completed_writes_valid_summary_json(logger):
    logger.file_changed("backend", "a.py", "CREATED", size_after=10)
    logger.pipeline_completed({"backend": "done"})
    summary = json.loads((logger.log_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["result"] == "SUCCESS"
    assert summary["run_id"] == logger.run_id
    assert summary["files"]["created"] == 1


def test_pipeline_failed_summary_names_root_cause(logger):
    logger.validator_result("backend", "val", "APIContractValidator", "FAIL", issue_count=1,
                            message="Frontend calls POST /visitors but OpenAPI defines PUT /visitors/{id}")
    logger.pipeline_failed("generic exception text", stage="backend", stage_status={"backend": "failed"})
    summary = json.loads((logger.log_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["result"] == "FAILED"
    assert summary["failed_stage"] == "backend"
    assert summary["failed_validator"] == "APIContractValidator"
    assert "PUT /visitors" in summary["error"]


def test_render_stage_tree_reflects_actual_progress(logger):
    logger.stage_started("backend", 1)
    logger.validator_result("backend", "ut", "X", "PASS")
    logger.validator_result("backend", "val", "Y", "FAIL", issue_count=1)
    tree = logger.render_stage_tree({"backend": "validating"})
    assert "BACKEND" in tree
    assert "✓ UT" in tree
    assert "✗ VAL" in tree


# ------------------------------------------------------- logging is best-effort --

def test_emit_swallows_exception_and_does_not_raise(logger):
    logger.log_dir = Path("Z:/this/path/cannot/possibly/exist/on/this/machine")
    # Must not raise, even though the write will fail - logging failures
    # can never crash the actual pipeline.
    logger.info("this write will fail internally")
    logger.error("so will this one")
    logger.stage("backend", "done")


def test_write_summary_swallows_exception_and_does_not_raise(logger):
    logger.log_dir = Path("Z:/this/path/cannot/possibly/exist/on/this/machine")
    logger.pipeline_completed({"backend": "done"})  # must not raise
    logger.pipeline_failed("boom", stage="backend", stage_status={})  # must not raise


# ------------------------------------------------------ LLM call metrics --

def test_llm_call_completed_tracks_duration_tokens_and_breakdowns(logger):
    call_id = logger.llm_call_start()
    logger.llm_call_completed(call_id, "backend_agent", "backend", "generation", "gpt-5.4-mini",
                              "azure_openai", "azure1", 0, input_tokens=100, output_tokens=50, total_tokens=150)
    assert logger._stats["llm_calls_ok"] == 1
    assert logger._stats["input_tokens"] == 100
    assert logger._stats["output_tokens"] == 50
    assert logger._llm_by_agent["backend_agent"]["calls"] == 1
    assert logger._llm_by_stage["backend"]["ok"] == 1
    assert logger._llm_by_model["gpt-5.4-mini"]["total_tokens"] == 150
    assert logger._llm_by_provider["azure_openai"]["calls"] == 1
    events = [e for e in _read_events(logger) if e["event"] == "llm_call"]
    assert events[0]["status"] == "SUCCESS"
    assert isinstance(events[0]["duration_ms"], (int, float))


def test_llm_call_failed_tracks_failure_and_rate_limits(logger):
    call_id = logger.llm_call_start()
    logger.llm_call_failed(call_id, "backend_agent", "backend", "generation", "llama-3.3-70b",
                           "groq", "account1", 1, error_type="rate_limit", error_message="429 too many requests")
    assert logger._stats["llm_calls_failed"] == 1
    assert logger._rate_limits["rate_limit"] == 1
    assert logger._llm_by_agent["backend_agent"]["failed"] == 1
    events = [e for e in _read_events(logger) if e["event"] == "llm_call"]
    assert events[0]["status"] == "FAILED"
    assert events[0]["error_type"] == "rate_limit"


def test_llm_call_id_unique_and_monotonic(logger):
    ids = [logger.llm_call_start() for _ in range(5)]
    assert ids == sorted(set(ids))
    assert len(set(ids)) == 5


# ---------------------------------------------------------- context size --

def test_context_size_records_estimate_not_real_tokens(logger):
    text = "x" * 4000  # ~1000 estimated tokens at chars/4
    logger.context_size("backend", "batch", files_count=12, text=text)
    events = [e for e in _read_events(logger) if e["event"] == "context_size"]
    assert events[0]["metadata"]["context_files"] == 12
    assert events[0]["metadata"]["context_bytes"] == 4000
    assert events[0]["metadata"]["estimated_context_tokens"] == 1000
    assert logger._context_sizes == [1000]


# ------------------------------------------------------- generation round --

def test_generation_round_recorded_for_comparison(logger):
    logger.generation_round("backend", "incremental", llm_calls=2, input_tokens=14200, output_tokens=4100,
                            files_read=6, files_written=3, duration_ms=41000, retries=0, result="written")
    assert len(logger._generation_rounds) == 1
    assert logger._generation_rounds[0]["strategy"] == "incremental"
    assert logger._generation_rounds[0]["files_written"] == 3
    events = [e for e in _read_events(logger) if e["event"] == "generation_round"]
    assert events[0]["metadata"]["llm_calls"] == 2


# --------------------------------------------------------------- files/tools --

def test_file_read_tracks_count_and_bytes(logger):
    logger.file_read("backend", "backend/main.py", 397)
    logger.file_read("backend", "backend/models.py", 4934)
    assert logger._stats["files_read"] == 2
    assert logger._stats["bytes_read"] == 397 + 4934


def test_tool_counts_and_duration_aggregated(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPELINE_LOG_LEVEL", "DEBUG")
    lg = PipelineLogger()
    lg.log_dir = tmp_path / "run"
    lg.log_dir.mkdir()
    lg.run_id = "test"
    id1 = lg.tool_call("read_file", "a.py")
    lg.tool_result("read_file", "10 chars", id1)
    id2 = lg.tool_call("read_file", "b.py")
    lg.tool_result("read_file", "20 chars", id2)
    id3 = lg.tool_call("write_file", "c.py")
    lg.tool_result("write_file", "OK", id3)
    assert lg._tool_counts["read_file"]["calls"] == 2
    assert lg._tool_counts["write_file"]["calls"] == 1
    assert lg._tool_counts["read_file"]["duration_ms"] >= 0


# --------------------------------------------------------------- retries --

def test_retry_tracked_per_stage(logger):
    logger.retry("backend", 1, 3, "UT failed")
    logger.retry("backend", 2, 3, "VAL failed")
    logger.retry("frontend", 1, 3, "UT failed")
    assert logger._stage_retry_counts == {"backend": 2, "frontend": 1}


# ------------------------------------------------------------ full summary --

def test_summary_json_has_full_schema_after_new_metrics(logger):
    call_id = logger.llm_call_start()
    logger.llm_call_completed(call_id, "backend_agent", "backend", "generation", "gpt-5.4-mini",
                              "azure_openai", "azure1", 0, input_tokens=100, output_tokens=50, total_tokens=150)
    logger.file_read("backend", "a.py", 100)
    logger.context_size("backend", "batch", 5, "x" * 400)
    logger.generation_round("backend", "batch", 1, 100, 50, 5, 2, 5000, 0, "written")
    logger.pipeline_completed({"backend": "done"})
    summary = json.loads((logger.log_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["llm"]["successful_calls"] == 1
    assert summary["llm"]["by_agent"]["backend_agent"]["calls"] == 1
    assert summary["files"]["read"] == 1
    assert summary["context"]["average_estimated_tokens"] == 100
    assert len(summary["generation_rounds"]) == 1
    assert summary["cost"] == "unavailable"
    assert "latency" in summary and "by_stage_ms" in summary["latency"]
