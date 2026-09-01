"""
Structured console logging AND persistent structured execution logging for
the pipeline - every node, tool call, model attempt, and stage transition
goes through this one module. Two complementary layers behind ONE call site
per event (PIPELINE OBSERVABILITY UPGRADE):
  - Console (unchanged from before): human-readable "what's happening now"
  - JSONL (new): logs/runs/<run_id>/pipeline.jsonl, one line per event -
    "what exactly happened during this run", reconstructable afterward and
    filterable by run_id/stage/attempt/event/tool/model/error_type. A
    logs/runs/<run_id>/summary.json is written once the run ends.

Every existing console method (node_start, node_complete, step, tool_call,
tool_result, model_attempt, model_attempt_failed, stage, info, success,
warning, error) keeps its old signature and behavior - they now ALSO emit a
structured event alongside the console print, so no existing call site
anywhere in the pipeline needed to change to get persistence.

Logging must never crash the pipeline: every structured-event write goes
through _emit, which swallows any exception (disk full, bad permissions,
whatever) and simply drops that one event rather than raising.

Icon vocabulary (kept consistent everywhere a message is logged):
  ✅ success   ⚠️  warning   ❌ error   ℹ️  info   🔧 tool call   ↳ tool result
"""

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

NODE_EMOJI = {
    "planner": "🧠",
    "database": "🗄️",
    "supervisor": "🧭",
    "backend": "⚙️",
    "frontend": "🎨",
    "testing": "🔍",
    "deployment": "📦",
}

STAGE_ICON = {"done": "✅", "failed": "❌", "skipped": "⏭️ ", "pending": "⏳"}

_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _level_value(level: str) -> int:
    try:
        return _LEVELS.index((level or "INFO").upper())
    except ValueError:
        return _LEVELS.index("INFO")


def _make_run_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


# Any env var whose NAME looks like it holds a secret gets its VALUE
# redacted everywhere it appears in a logged string - not a fixed list of
# expected var names, so a generated project's own DATABASE_URL password
# (which follows the same naming convention) is covered too, not just this
# pipeline's own API keys. Sorted longest-first so a short value that
# happens to be a substring of a longer one doesn't get redacted first and
# leave a mangled remainder of the longer secret behind.
_SECRET_NAME_HINTS = ("key", "secret", "password", "token", "credential")
_BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9\-_.=]+", re.IGNORECASE)
_CONN_STRING_PATTERN = re.compile(r"(postgresql|postgres|mysql|mongodb)(\+\w+)?://[^:@/\s]+:[^@\s]+@", re.IGNORECASE)


def _collect_secret_values() -> list:
    values = []
    for name, value in os.environ.items():
        if not value or len(value) < 6:
            continue
        if any(hint in name.lower() for hint in _SECRET_NAME_HINTS):
            values.append(value)
    return sorted(set(values), key=len, reverse=True)


def _redact_text(text: str, secret_values: list) -> str:
    for value in secret_values:
        if value and value in text:
            text = text.replace(value, "***REDACTED***")
    text = _BEARER_PATTERN.sub("Bearer ***REDACTED***", text)
    text = _CONN_STRING_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2) or ''}://***:***@", text)
    return text


def _redact(value, secret_values: list):
    if isinstance(value, str):
        return _redact_text(value, secret_values)
    if isinstance(value, dict):
        return {k: _redact(v, secret_values) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v, secret_values) for v in value]
    return value


class PipelineLogger:
    """Structured logger for pipeline progress - all console AND persistent output flows through here."""

    def __init__(self):
        self.start_time = None
        self.step_times = {}
        self.run_id = None
        self.project_id = ""
        self.run_mode = ""
        self.log_dir = None
        self.level = _level_value(os.environ.get("PIPELINE_LOG_LEVEL", "INFO"))
        self._seq = 0
        self._secret_values = None
        self._stats = {
            "llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "retries": 0, "fallbacks": 0,
            "files_created": 0, "files_modified": 0, "files_deleted": 0, "files_unchanged": 0,
            "files_read": 0, "bytes_read": 0, "bytes_written": 0,
            "llm_calls_ok": 0, "llm_calls_failed": 0,
            "llm_time_ms": 0.0, "tool_time_ms": 0.0, "docker_time_ms": 0.0,
        }
        # Per-dimension breakdowns, keyed by whatever dimension value shows
        # up - a plain dict of counters, not a fixed schema, so a new
        # agent/model/provider/tool name is picked up automatically with no
        # code change here.
        self._llm_by_agent = {}      # skill_name -> {"calls", "ok", "failed", "input_tokens", "output_tokens", "total_tokens"}
        self._llm_by_stage = {}      # pipeline stage -> same shape
        self._llm_by_model = {}      # model_name -> same shape
        self._llm_by_provider = {}   # provider -> same shape
        self._tool_counts = {}       # tool_name -> {"calls", "duration_ms"}
        self._tool_call_starts = {}  # tool_call_id -> perf_counter() at STARTED, for duration on COMPLETED
        self._llm_call_starts = {}   # call_id -> perf_counter(), for duration on completion
        self._stage_durations = {}   # node_name (e.g. "backend_run") -> accumulated ms across every attempt
        self._stage_retry_counts = {}  # stage -> count
        self._rate_limits = {"rate_limit": 0, "timeout": 0, "auth_error": 0, "server_error": 0}
        self._context_sizes = []     # list of estimated_context_tokens, one per generation call - avg/max
        self._generation_rounds = []  # one entry per RUN round: {stage, strategy, llm_calls, tokens, files, duration_ms, result}
        self._call_seq = 0
        # Cheapest possible "what actually broke" tracking - updated as a
        # side effect of validator_result/retry, not by re-scanning the
        # whole JSONL after the fact. Overwritten by whichever failure is
        # MOST RECENT, which is also the most relevant one: a stage that
        # fails after N retries is failing for whatever its LAST attempt's
        # reason was, not its first.
        self._last_failure = {}
        # {stage: {"run": True|None, "ut": "PASS"/"FAIL"/None, "val": "PASS"/"FAIL"/None}}
        # - drives the human-readable stage tree in _write_summary/print_stage_tree.
        self._stage_phases = {}

    # ---------------------------------------------------------------- run --

    def start(self):
        """Start timing the overall run (existing method, unchanged)."""
        self.start_time = time.time()

    def start_run(self, project_id: str = "", mode: str = "build") -> str:
        """
        Begin a new pipeline execution: mints a run_id, creates its log
        directory (logs/runs/<run_id>/), and emits PIPELINE_STARTED. Call
        once, at the top of main.py's run_pipeline() - every other
        structured event attaches to whichever run_id is current. Returns
        the run_id so a caller can surface it (e.g. print "Run: <id>").
        """
        self.run_id = _make_run_id()
        self.project_id = project_id
        self.run_mode = mode
        self.start_time = self.start_time or time.time()
        try:
            base = Path(__file__).resolve().parent.parent / "logs" / "runs" / self.run_id
            base.mkdir(parents=True, exist_ok=True)
            self.log_dir = base
        except Exception:
            self.log_dir = None
        self._emit("INFO", event="pipeline_started", status="STARTED",
                    metadata={"mode": mode, "project_id": project_id})
        print(f"🔖 Run: {self.run_id}")
        return self.run_id

    def pipeline_completed(self, stage_status: dict = None):
        self._emit("INFO", event="pipeline_completed", status="COMPLETED",
                    duration_ms=self._elapsed_ms(), metadata={"stage_status": stage_status or {}})
        self._write_summary(result="SUCCESS", stage_status=stage_status or {})

    def pipeline_failed(self, error: str, stage: str = "", stage_status: dict = None):
        self._emit("ERROR", event="pipeline_failed", status="FAILED", stage=stage,
                    duration_ms=self._elapsed_ms(), error_message=str(error)[:2000],
                    metadata={"stage_status": stage_status or {}})
        self._write_summary(result="FAILED", stage_status=stage_status or {}, error=str(error), stage=stage)

    # ------------------------------------------------------------ stages --

    def stage_started(self, stage: str, attempt: int = 1):
        self._stage_phases.setdefault(stage, {"run": None, "ut": None, "val": None})["run"] = True
        self._emit("INFO", stage=stage, event="stage_started", status="STARTED", attempt=attempt)

    def stage_completed(self, stage: str, status: str, duration_ms: float = None,
                        issue_count: int = None):
        """status: PASSED | FAILED | SKIPPED"""
        if status == "FAILED":
            self._last_failure = {"stage": stage, "phase": "", "validator": "", "issue_count": issue_count,
                                   "message": self._last_failure.get("message", "") if self._last_failure.get("stage") == stage else ""}
        self._emit("INFO" if status == "PASSED" else "WARNING", stage=stage, event="stage_completed",
                    status=status, duration_ms=duration_ms, metadata={"issue_count": issue_count})

    def retry(self, stage: str, attempt: int, max_attempts: int, reason: str, issue_count: int = None):
        self._stats["retries"] += 1
        self._stage_retry_counts[stage] = self._stage_retry_counts.get(stage, 0) + 1
        self._emit("WARNING", stage=stage, event="stage_retry", status="RETRYING", attempt=attempt,
                    message=reason, metadata={"max_attempts": max_attempts, "issue_count": issue_count})

    def generation_strategy(self, stage: str, strategy: str, reason: str, metadata: dict = None):
        self._emit("INFO", stage=stage, event="generation_strategy", status=strategy.upper(),
                    message=reason, metadata=metadata or {})

    def validator_result(self, stage: str, phase: str, validator: str, status: str,
                         issue_count: int = 0, message: str = ""):
        """phase: 'ut' | 'val'. status: PASS | FAIL | SKIPPED."""
        level = "WARNING" if status == "FAIL" else "INFO"
        if phase in ("ut", "val"):
            self._stage_phases.setdefault(stage, {"run": None, "ut": None, "val": None})[phase] = status
        if status == "FAIL":
            self._last_failure = {"stage": stage, "phase": phase, "validator": validator,
                                  "issue_count": issue_count, "message": message}
        self._emit(level, stage=stage, event="validator_result", validation=validator,
                    status=status, message=message[:500], metadata={"phase": phase, "issue_count": issue_count})

    def feedback(self, stage: str, source: str, issue_count: int, severity: str = "", summary: str = ""):
        self._emit("INFO", stage=stage, event="feedback", status="SENT",
                    metadata={"source": source, "issue_count": issue_count, "severity": severity},
                    message=summary[:500])

    # -------------------------------------------------------------- files --

    def file_changed(self, stage: str, path: str, operation: str,
                     size_before: int = None, size_after: int = None):
        """operation: CREATED | MODIFIED | DELETED | SKIPPED (unchanged content)."""
        key = {"CREATED": "files_created", "MODIFIED": "files_modified",
               "DELETED": "files_deleted", "SKIPPED": "files_unchanged"}.get(operation)
        if key:
            self._stats[key] += 1
        if operation in ("CREATED", "MODIFIED") and isinstance(size_after, int):
            self._stats["bytes_written"] += size_after
        self._emit("INFO", stage=stage, event="file_" + operation.lower(), status=operation,
                    files=[path], metadata={"size_before": size_before, "size_after": size_after})

    # --------------------------------------------------------- deployment --

    def deployment(self, event: str, status: str, metadata: dict = None):
        self._emit("INFO" if status not in ("FAILED",) else "ERROR",
                    stage="deployment", event=event, status=status, deployment=metadata or {})

    def rollback(self, status: str, metadata: dict = None):
        self._emit("WARNING" if status == "STARTED" else ("ERROR" if status == "FAILED" else "INFO"),
                    stage="deployment", event="rollback", status=status, rollback=metadata or {})

    # ----------------------------------------------------- console (old) --

    def node_start(self, node_name: str):
        """Log the start of a graph node (planner/database/supervisor/backend/frontend/testing/deployment)."""
        emoji = NODE_EMOJI.get(node_name, "▶️")
        print(f"\n{emoji}  {node_name.upper().replace('_', ' ')}")
        print("=" * 80)
        self.step_times[node_name] = time.time()

    def node_complete(self, node_name: str):
        """Log completion of a node, with how long it took."""
        if node_name in self.step_times:
            elapsed = time.time() - self.step_times[node_name]
            elapsed_ms = elapsed * 1000
            print(f"\n✅ {node_name.replace('_', ' ').title()} completed in {elapsed:.1f}s")
            print("=" * 80)
            self._stage_durations[node_name] = self._stage_durations.get(node_name, 0.0) + elapsed_ms
            if node_name.startswith("cicd_"):
                self._stats["docker_time_ms"] += elapsed_ms
            self._emit("INFO", event="node_complete", metadata={"node": node_name}, duration_ms=elapsed_ms)

    def tool_call(self, tool_name: str, args_summary: str = "", tool_call_id: int = None):
        """Log an agent about to call one of its tools - the CLI is otherwise
        blind to what happens inside a tool-calling agent's internal loop."""
        suffix = f"({args_summary})" if args_summary else "()"
        print(f"      🔧 tool call: {tool_name}{suffix}")
        tcid = tool_call_id if tool_call_id is not None else self._next_seq()
        self._tool_call_starts[tcid] = time.perf_counter()
        entry = self._tool_counts.setdefault(tool_name, {"calls": 0, "duration_ms": 0.0})
        entry["calls"] += 1
        self._emit("DEBUG", event="tool_call", tool=tool_name, status="STARTED",
                   message=args_summary[:300], metadata={"tool_call_id": tcid})
        return tcid

    def tool_result(self, tool_name: str, result_summary: str, tool_call_id: int = None):
        """Log a tool's result (truncated) right after tool_call, same reason."""
        start = self._tool_call_starts.pop(tool_call_id, None) if tool_call_id is not None else None
        duration_ms = round((time.perf_counter() - start) * 1000, 1) if start is not None else None
        dur_suffix = f" ({duration_ms:.0f}ms)" if isinstance(duration_ms, (int, float)) else ""
        print(f"         ↳ {result_summary}{dur_suffix}")
        if isinstance(duration_ms, (int, float)):
            self._stats["tool_time_ms"] += duration_ms
            self._tool_counts.setdefault(tool_name, {"calls": 0, "duration_ms": 0.0})["duration_ms"] += duration_ms
        self._emit("DEBUG", event="tool_call", tool=tool_name, status="COMPLETED", duration_ms=duration_ms,
                   message=result_summary[:300], metadata={"tool_call_id": tool_call_id})

    def stage(self, stage_name: str, status: str):
        """Log an explicit stage_status transition (pending/done/failed/skipped)."""
        icon = STAGE_ICON.get(status, "•")
        print(f"   {icon} stage[{stage_name}] -> {status}")
        self._emit("INFO", stage=stage_name, event="stage_status", status=status.upper())

    def step(self, emoji: str, message: str):
        """
        Log a skill/action announcement with its own distinctive icon (e.g.
        "📋 Analyzing request..."), rather than everything through the
        generic ℹ️  info icon - each skill's icon is part of what makes the
        console output scannable at a glance.
        """
        print(f"{emoji}  {message}")

    def info(self, message: str, indent: int = 0):
        """Log info message."""
        prefix = "  " * indent
        print(f"{prefix}ℹ️  {message}")
        self._emit("INFO", event="info", message=message[:1000])

    def success(self, message: str, indent: int = 0):
        """Log success message."""
        prefix = "  " * indent
        print(f"{prefix}✅ {message}")
        self._emit("INFO", event="success", status="COMPLETED", message=message[:1000])

    def warning(self, message: str, indent: int = 0):
        """Log warning message."""
        prefix = "  " * indent
        print(f"{prefix}⚠️  {message}")
        self._emit("WARNING", event="warning", message=message[:1000])

    def error(self, message: str, indent: int = 0, error_type: str = "UNKNOWN_ERROR", stage: str = ""):
        """Log error message."""
        prefix = "  " * indent
        print(f"{prefix}❌ {message}")
        self._emit("ERROR", event="error", stage=stage, error_type=error_type, error_message=str(message)[:2000])

    def model_attempt(self, model_name: str, attempt: int, provider: str = "", account: str = "",
                      stage: str = "", input_tokens=None, output_tokens=None, total_tokens=None):
        """Log which model a call is using - attempt 0 is the primary credential, 1+ is a fallback."""
        if attempt == 0:
            print(f"  🤖 Using: {model_name}")
        else:
            print(f"  🔄 Fallback {attempt}: {model_name}")
        self._stats["llm_calls"] += 1
        for k, v in (("input_tokens", input_tokens), ("output_tokens", output_tokens),
                     ("total_tokens", total_tokens)):
            if isinstance(v, int):
                self._stats[k] += v
        self._emit("DEBUG", stage=stage, event="model_attempt", status="STARTED", model=model_name,
                   provider=provider, credential_slot=account, attempt=attempt,
                   token_usage={"input_tokens": input_tokens if input_tokens is not None else "unknown",
                                "output_tokens": output_tokens if output_tokens is not None else "unknown",
                                "total_tokens": total_tokens if total_tokens is not None else "unknown"})

    def llm_call_start(self) -> int:
        """
        Mint a call_id and start its timer - call right before the actual
        client.invoke()/react_agent.invoke() happens, pass the returned id
        to llm_call_completed()/llm_call_failed() afterward so duration_ms
        is real wall-clock time, not an estimate.
        """
        self._call_seq += 1
        call_id = self._call_seq
        self._llm_call_starts[call_id] = time.perf_counter()
        return call_id

    def _llm_call_duration_ms(self, call_id: int):
        start = self._llm_call_starts.pop(call_id, None)
        return round((time.perf_counter() - start) * 1000, 1) if start is not None else None

    def _bump_llm_breakdown(self, bucket: dict, key: str, ok: bool,
                            input_tokens=None, output_tokens=None, total_tokens=None):
        if not key:
            return
        entry = bucket.setdefault(key, {"calls": 0, "ok": 0, "failed": 0,
                                        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
        entry["calls"] += 1
        entry["ok" if ok else "failed"] += 1
        for k, v in (("input_tokens", input_tokens), ("output_tokens", output_tokens), ("total_tokens", total_tokens)):
            if isinstance(v, int):
                entry[k] += v

    def llm_call_completed(self, call_id: int, agent: str, stage: str, operation: str, model: str,
                           provider: str, account: str, attempt: int,
                           input_tokens=None, output_tokens=None, total_tokens=None):
        """
        ONE comprehensive record for a successful LLM call - the "LLM_CALL"
        event from the observability spec, with real duration and whatever
        token usage the provider reported. This (not model_attempt, which
        fires before the call and never knows duration/outcome) is the
        source of truth for LLM_CALL counts/breakdowns in the run summary.
        """
        duration_ms = self._llm_call_duration_ms(call_id)
        self._stats["llm_calls_ok"] += 1
        if isinstance(duration_ms, (int, float)):
            self._stats["llm_time_ms"] += duration_ms
        for k, v in (("input_tokens", input_tokens), ("output_tokens", output_tokens), ("total_tokens", total_tokens)):
            if isinstance(v, int):
                self._stats[k] += v
        dur_str = f"{duration_ms/1000:.1f}s" if isinstance(duration_ms, (int, float)) else "?"
        in_str = input_tokens if input_tokens is not None else "unknown"
        out_str = output_tokens if output_tokens is not None else "unknown"
        print(f"     ↳ {agent}: {dur_str} | tokens in={in_str} out={out_str}")
        self._bump_llm_breakdown(self._llm_by_agent, agent, True, input_tokens, output_tokens, total_tokens)
        self._bump_llm_breakdown(self._llm_by_stage, stage, True, input_tokens, output_tokens, total_tokens)
        self._bump_llm_breakdown(self._llm_by_model, model, True, input_tokens, output_tokens, total_tokens)
        self._bump_llm_breakdown(self._llm_by_provider, provider, True, input_tokens, output_tokens, total_tokens)
        self._emit("INFO", stage=stage, event="llm_call", status="SUCCESS", model=model, provider=provider,
                   credential_slot=account, attempt=attempt, duration_ms=duration_ms,
                   metadata={"call_id": call_id, "agent": agent, "operation": operation},
                   token_usage={"input_tokens": input_tokens if input_tokens is not None else "unknown",
                                "output_tokens": output_tokens if output_tokens is not None else "unknown",
                                "total_tokens": total_tokens if total_tokens is not None else "unknown"})

    def llm_call_failed(self, call_id: int, agent: str, stage: str, operation: str, model: str,
                        provider: str, account: str, attempt: int, error_type: str, error_message: str):
        duration_ms = self._llm_call_duration_ms(call_id)
        self._stats["llm_calls_failed"] += 1
        if isinstance(duration_ms, (int, float)):
            self._stats["llm_time_ms"] += duration_ms
        if error_type in self._rate_limits:
            self._rate_limits[error_type] += 1
        dur_str = f"{duration_ms/1000:.1f}s" if isinstance(duration_ms, (int, float)) else "?"
        print(f"     ↳ {agent}: FAILED after {dur_str} ({error_type})")
        self._bump_llm_breakdown(self._llm_by_agent, agent, False)
        self._bump_llm_breakdown(self._llm_by_stage, stage, False)
        self._bump_llm_breakdown(self._llm_by_model, model, False)
        self._bump_llm_breakdown(self._llm_by_provider, provider, False)
        self._emit("WARNING", stage=stage, event="llm_call", status="FAILED", model=model, provider=provider,
                   credential_slot=account, attempt=attempt, duration_ms=duration_ms, error_type=error_type,
                   error_message=error_message[:500], metadata={"call_id": call_id, "agent": agent, "operation": operation})

    def context_size(self, stage: str, strategy: str, files_count: int, text: str):
        """
        Records how much context went into a generation call - the batch-
        vs-incremental comparison the spec explicitly calls out as the
        point of this whole section. estimated_tokens is a plain chars/4
        heuristic (clearly labeled as an estimate, never conflated with a
        provider-reported real count) - good enough to compare relative
        context size across rounds/strategies, which is the actual use case.
        """
        bytes_count = len(text.encode("utf-8"))
        estimated_tokens = len(text) // 4
        self._context_sizes.append(estimated_tokens)
        print(f"  Context: {files_count} file(s), {bytes_count:,} bytes, ~{estimated_tokens:,} est. tokens "
             f"({strategy})")
        self._emit("INFO", stage=stage, event="context_size", status=strategy.upper(),
                   metadata={"context_files": files_count, "context_bytes": bytes_count,
                            "estimated_context_tokens": estimated_tokens})

    def generation_round(self, stage: str, strategy: str, llm_calls: int, input_tokens, output_tokens,
                         files_read: int, files_written: int, duration_ms: float, retries: int, result: str):
        """
        One row of the Generation Strategy Comparison table - correlates
        everything about a single RUN round (which strategy, how many
        calls/tokens/files/how long, how many retries, pass or fail) so
        batch vs incremental can be judged objectively across a run, not
        pieced together by hand from scattered events.
        """
        self._generation_rounds.append({
            "stage": stage, "strategy": strategy, "llm_calls": llm_calls,
            "input_tokens": input_tokens if input_tokens is not None else "unknown",
            "output_tokens": output_tokens if output_tokens is not None else "unknown",
            "files_read": files_read, "files_written": files_written,
            "duration_ms": round(duration_ms, 1) if isinstance(duration_ms, (int, float)) else None,
            "retries": retries, "result": result,
        })
        dur_str = f"{duration_ms/1000:.1f}s" if isinstance(duration_ms, (int, float)) else "?"
        in_str = input_tokens if input_tokens is not None else "unknown"
        out_str = output_tokens if output_tokens is not None else "unknown"
        print(f"  Round summary [{stage}/{strategy}]: {llm_calls} LLM call(s), tokens in={in_str} out={out_str}, "
             f"{files_read} file(s) read, {files_written} written, {dur_str}, result={result}")
        self._emit("INFO", stage=stage, event="generation_round", status=result, duration_ms=duration_ms,
                   metadata={"strategy": strategy, "llm_calls": llm_calls, "files_read": files_read,
                            "files_written": files_written, "retries": retries})

    def file_read(self, stage: str, path: str, size: int):
        """Companion to file_changed() for the read side - tracks files_read
        count and bytes_read for the File Read Efficiency section. Not
        folded into file_changed() itself since a read isn't a "change"."""
        self._stats["files_read"] += 1
        self._stats["bytes_read"] += size
        self._emit("DEBUG", stage=stage, event="file_read", status="READ", files=[path],
                   metadata={"size": size})

    def model_attempt_failed(self, attempt: int, error: str, will_retry: bool,
                             provider: str = "", model: str = "", error_type: str = ""):
        """Log a failed model attempt, and whether another credential will be tried."""
        print(f"  ⚠️  Attempt {attempt + 1} failed: {error[:80]}")
        if will_retry:
            print(f"  🔄 Trying next credential...")
            self._stats["fallbacks"] += 1
        else:
            print(f"  ❌ All attempts failed!")
        classified = error_type or _classify_error(error)
        self._emit("WARNING" if will_retry else "ERROR", event="model_attempt", status="FAILED",
                   model=model, provider=provider, attempt=attempt, error_type=classified,
                   error_message=str(error)[:1000], metadata={"fallback": will_retry})

    def separator(self):
        """Print separator line."""
        print("-" * 80)

    def header(self, title: str):
        """Print section header."""
        print(f"\n{'=' * 80}")
        print(f"{title}")
        print("=" * 80)

    def elapsed(self) -> float:
        """Get elapsed time."""
        if self.start_time:
            return time.time() - self.start_time
        return 0.0

    def summary(self, total_steps: int):
        """Print final summary."""
        elapsed = self.elapsed()
        print(f"\n{'=' * 80}")
        print(f"✅ Pipeline completed successfully!")
        print(f"   Steps: {total_steps}")
        print(f"   Time: {elapsed:.1f}s")
        print("=" * 80)

    # --------------------------------------------------------- internals --

    def _elapsed_ms(self):
        return self.elapsed() * 1000 if self.start_time else None

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _emit(self, level: str, event: str, status: str = None, stage: str = None,
              message: str = None, attempt: int = None, tool: str = None, model: str = None,
              provider: str = None, credential_slot: str = None, duration_ms: float = None,
              token_usage: dict = None, files: list = None, error_type: str = None,
              error_message: str = None, validation: str = None, deployment: dict = None,
              rollback: dict = None, metadata: dict = None):
        """
        Write one structured JSONL event. Best-effort ONLY - any failure here
        (missing log_dir, disk error, a bad value that won't JSON-serialize)
        is swallowed so a logging problem can never take down the actual
        pipeline run. Events below the configured level (PIPELINE_LOG_LEVEL,
        default INFO) are skipped before ever reaching disk.
        """
        if _level_value(level) < self.level:
            return
        try:
            if self._secret_values is None:
                self._secret_values = _collect_secret_values()

            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "run_id": self.run_id,
                "project_id": self.project_id or None,
                "stage": stage,
                "event": event,
                "status": status,
                "attempt": attempt,
                "tool": tool,
                "model": model,
                "provider": provider,
                "credential_slot": credential_slot,
                "duration_ms": round(duration_ms, 1) if isinstance(duration_ms, (int, float)) else None,
                "token_usage": token_usage,
                "files": files,
                "error_type": error_type,
                "error_message": error_message,
                "validation": validation,
                "deployment": deployment,
                "rollback": rollback,
                "message": message,
                "metadata": metadata,
            }
            record = {k: v for k, v in record.items() if v is not None}
            record = _redact(record, self._secret_values)

            if self.log_dir:
                with open(self.log_dir / "pipeline.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, default=str) + "\n")
        except Exception:
            pass  # logging must never crash the pipeline

    def render_stage_tree(self, stage_status: dict) -> str:
        """
        Human-readable "PIPELINE -> STAGE -> RUN/UT/VAL" tree per the spec's
        HUMAN READABLE LOG section - built from _stage_phases (populated by
        stage_started/validator_result as the run actually progresses, not
        re-derived from stage_status alone, which only ever has one final
        word per stage). Stages with no phase activity yet (never reached
        this run) are omitted rather than shown as a wall of pending stages.
        """
        lines = ["PIPELINE"]
        order = ["database", "backend", "frontend", "testing", "deployment"]
        for stage in order:
            phases = self._stage_phases.get(stage)
            if not phases:
                continue
            lines.append(f"  |- {stage.upper()}")
            if phases.get("run"):
                lines.append("  |    RUN")
            for phase_key, label in (("ut", "UT"), ("val", "VAL")):
                status = phases.get(phase_key)
                if status == "PASS":
                    lines.append(f"  |    ✓ {label}")
                elif status == "FAIL":
                    lines.append(f"  |    ✗ {label}")
            final = stage_status.get(stage, "")
            if final:
                lines.append(f"  |    -> {final}")
        return "\n".join(lines)

    def _write_summary(self, result: str, stage_status: dict, error: str = "", stage: str = ""):
        try:
            llm_calls_total = self._stats["llm_calls_ok"] + self._stats["llm_calls_failed"]
            known_time_ms = self._stats["llm_time_ms"] + self._stats["tool_time_ms"] + self._stats["docker_time_ms"]
            total_duration_ms = self.elapsed() * 1000
            other_time_ms = max(0.0, total_duration_ms - known_time_ms)

            summary = {
                "run_id": self.run_id,
                "project_id": self.project_id or None,
                "mode": self.run_mode,
                "result": result,
                "duration_s": round(self.elapsed(), 1),
                "duration_ms": round(total_duration_ms, 1),
                "stages": stage_status,

                "llm": {
                    "total_calls": llm_calls_total or self._stats["llm_calls"],
                    "successful_calls": self._stats["llm_calls_ok"],
                    "failed_calls": self._stats["llm_calls_failed"],
                    "retries": self._stats["retries"],
                    "fallbacks": self._stats["fallbacks"],
                    "input_tokens": self._stats["input_tokens"] or "unknown",
                    "output_tokens": self._stats["output_tokens"] or "unknown",
                    "total_tokens": self._stats["total_tokens"] or "unknown",
                    "by_agent": self._llm_by_agent,
                    "by_stage": self._llm_by_stage,
                    "by_model": self._llm_by_model,
                    "by_provider": self._llm_by_provider,
                },

                "tokens": {  # kept for backward compat with any existing reader of this key
                    "input": self._stats["input_tokens"] or "unknown",
                    "output": self._stats["output_tokens"] or "unknown",
                    "total": self._stats["total_tokens"] or "unknown",
                },

                "context": {
                    "average_estimated_tokens": (round(sum(self._context_sizes) / len(self._context_sizes))
                                                 if self._context_sizes else "unavailable"),
                    "maximum_estimated_tokens": max(self._context_sizes) if self._context_sizes else "unavailable",
                    "note": "estimated via chars/4 heuristic, not a provider-reported count",
                },

                "generation_rounds": self._generation_rounds,

                "retries": self._stats["retries"],
                "fallbacks": self._stats["fallbacks"],
                "retries_by_stage": self._stage_retry_counts,

                "rate_limits": self._rate_limits,

                "files": {
                    "read": self._stats["files_read"],
                    "created": self._stats["files_created"],
                    "modified": self._stats["files_modified"],
                    "deleted": self._stats["files_deleted"],
                    "unchanged_writes": self._stats["files_unchanged"],
                    "bytes_read": self._stats["bytes_read"],
                    "bytes_written": self._stats["bytes_written"],
                    "estimated_tokens_read": self._stats["bytes_read"] // 4,
                    "estimated_tokens_written": self._stats["bytes_written"] // 4,
                },

                "tools": {name: c["calls"] for name, c in self._tool_counts.items()},
                "tools_detail": self._tool_counts,

                "latency": {
                    "by_stage_ms": self._stage_durations,
                    "llm_ms": round(self._stats["llm_time_ms"], 1),
                    "tool_ms": round(self._stats["tool_time_ms"], 1),
                    "docker_ms": round(self._stats["docker_time_ms"], 1),
                    "other_ms": round(other_time_ms, 1),
                },

                "cost": "unavailable",  # no pricing table configured - never invented (see model_config.py)
            }
            if result == "FAILED":
                # Prefer the most specific known culprit (a named validator
                # that actually failed) over the bare exception text - per
                # the spec, the developer shouldn't have to search thousands
                # of lines to find which check actually caused this.
                root_cause = dict(self._last_failure) if self._last_failure else {}
                summary["failed_stage"] = root_cause.get("stage") or stage
                summary["failed_phase"] = root_cause.get("phase") or None
                summary["failed_validator"] = root_cause.get("validator") or None
                summary["issue_count"] = root_cause.get("issue_count")
                summary["error"] = str(root_cause.get("message") or error)[:1000]

            if self._secret_values is None:
                self._secret_values = _collect_secret_values()
            summary = _redact(summary, self._secret_values)

            if self.log_dir:
                with open(self.log_dir / "summary.json", "w", encoding="utf-8") as f:
                    json.dump(summary, f, indent=2, default=str)

            print(f"\n{'=' * 80}")
            print(f"PIPELINE {result}" + (f" (run {self.run_id})" if self.run_id else ""))
            print(f"  Duration: {summary['duration_s']}s | LLM calls: {summary['llm']['total_calls']} "
                 f"({summary['llm']['successful_calls']} ok, {summary['llm']['failed_calls']} failed) | "
                 f"Retries: {summary['retries']} | Fallbacks: {summary['fallbacks']}")
            print(f"  Tokens: input={summary['llm']['input_tokens']} output={summary['llm']['output_tokens']} "
                 f"total={summary['llm']['total_tokens']}")
            print(f"  Files: read={summary['files']['read']}, +{summary['files']['created']} created, "
                 f"~{summary['files']['modified']} modified, -{summary['files']['deleted']} deleted, "
                 f"{summary['files']['unchanged_writes']} unchanged writes skipped")
            if summary["tools"]:
                print(f"  Tools: " + ", ".join(f"{name}={n}" for name, n in summary["tools"].items()))
            print(f"  Latency: llm={summary['latency']['llm_ms']:.0f}ms tool={summary['latency']['tool_ms']:.0f}ms "
                 f"docker={summary['latency']['docker_ms']:.0f}ms other={summary['latency']['other_ms']:.0f}ms")
            if any(self._rate_limits.values()):
                print(f"  Rate limits/errors: " + ", ".join(f"{k}={v}" for k, v in self._rate_limits.items() if v))
            if result == "FAILED":
                print(f"  Failed at: stage={summary.get('failed_stage')} "
                     f"phase={summary.get('failed_phase') or '?'} "
                     f"validator={summary.get('failed_validator') or '?'} "
                     f"issues={summary.get('issue_count') if summary.get('issue_count') is not None else '?'}")
                print(f"  Reason: {summary['error'][:300]}")
            print(self.render_stage_tree(stage_status))
            if self.log_dir:
                print(f"  Full log: {self.log_dir / 'pipeline.jsonl'}")
            print("=" * 80)
        except Exception:
            pass  # logging must never crash the pipeline


_ERROR_CLASSIFIERS = (
    ("rate_limit", ("rate limit", "429", "quota")),
    ("timeout", ("timeout", "timed out")),
    ("auth_error", ("authentication", "unauthorized", "401", "403", "api key")),
    ("server_error", ("500", "502", "503", "504", "internal server error")),
)


def _classify_error(error: str) -> str:
    lowered = (error or "").lower()
    for label, needles in _ERROR_CLASSIFIERS:
        if any(n in lowered for n in needles):
            return label
    return "unknown"


# Global logger instance
_logger = None


def get_logger() -> PipelineLogger:
    """Get the global logger instance."""
    global _logger
    if _logger is None:
        _logger = PipelineLogger()
    return _logger
