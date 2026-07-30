"""
Structured console logging for the pipeline - every node, tool call, model
attempt, and stage transition goes through this one module, so the CLI's
output stays consistent (same icon per meaning, same indentation rules)
instead of every file hand-rolling its own print() formatting.

Icon vocabulary (kept consistent everywhere a message is logged):
  ✅ success   ⚠️  warning   ❌ error   ℹ️  info   🔧 tool call   ↳ tool result
"""

import time

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


class PipelineLogger:
    """Structured logger for pipeline progress - all console output flows through here."""

    def __init__(self):
        self.start_time = None
        self.step_times = {}

    def start(self):
        """Start timing the overall run."""
        self.start_time = time.time()

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
            print(f"\n✅ {node_name.replace('_', ' ').title()} completed in {elapsed:.1f}s")
            print("=" * 80)

    def tool_call(self, tool_name: str, args_summary: str = ""):
        """Log an agent about to call one of its tools - the CLI is otherwise
        blind to what happens inside a tool-calling agent's internal loop."""
        suffix = f"({args_summary})" if args_summary else "()"
        print(f"      🔧 tool call: {tool_name}{suffix}")

    def tool_result(self, tool_name: str, result_summary: str):
        """Log a tool's result (truncated) right after tool_call, same reason."""
        print(f"         ↳ {result_summary}")

    def stage(self, stage_name: str, status: str):
        """Log an explicit stage_status transition (pending/done/failed/skipped)."""
        icon = STAGE_ICON.get(status, "•")
        print(f"   {icon} stage[{stage_name}] -> {status}")

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

    def success(self, message: str, indent: int = 0):
        """Log success message."""
        prefix = "  " * indent
        print(f"{prefix}✅ {message}")

    def warning(self, message: str, indent: int = 0):
        """Log warning message."""
        prefix = "  " * indent
        print(f"{prefix}⚠️  {message}")

    def error(self, message: str, indent: int = 0):
        """Log error message."""
        prefix = "  " * indent
        print(f"{prefix}❌ {message}")

    def model_attempt(self, model_name: str, attempt: int):
        """Log which model a call is using - attempt 0 is the primary credential, 1+ is a fallback."""
        if attempt == 0:
            print(f"  🤖 Using: {model_name}")
        else:
            print(f"  🔄 Fallback {attempt}: {model_name}")

    def model_attempt_failed(self, attempt: int, error: str, will_retry: bool):
        """Log a failed model attempt, and whether another credential will be tried."""
        print(f"  ⚠️  Attempt {attempt + 1} failed: {error[:80]}")
        if will_retry:
            print(f"  🔄 Trying next credential...")
        else:
            print(f"  ❌ All attempts failed!")

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


# Global logger instance
_logger = None


def get_logger() -> PipelineLogger:
    """Get the global logger instance."""
    global _logger
    if _logger is None:
        _logger = PipelineLogger()
    return _logger
