"""
Status Tracker - Centralized stage status management and display.

Provides clear, real-time visibility into where each stage is in the pipeline:
- skipped: Not needed by execution plan
- pending: Needed but not started yet
- running: Currently executing (RUN node)
- validating: In UT/VAL checks
- updating: Fixing issues (RUN with feedback)
- done: Passed all checks
- failed: Exhausted all attempts
"""

from typing import Dict
from core.logger import get_logger


class StageTracker:
    """Tracks and displays status for all pipeline stages."""
    
    STAGE_ORDER = ["database", "backend", "frontend", "testing", "deployment"]
    
    STATUS_SYMBOLS = {
        "skipped": "⊘",
        "pending": "⏸",
        "running": "▶",
        "validating": "🔍",
        "updating": "🔄",
        "done": "✓",
        "failed": "✗",
    }
    
    STATUS_DESCRIPTIONS = {
        "skipped": "Not needed",
        "pending": "Waiting to start",
        "running": "Generating code",
        "validating": "Running checks",
        "updating": "Fixing issues",
        "done": "Complete",
        "failed": "Failed",
    }
    
    @staticmethod
    def display_status(stage_status: Dict[str, str], current_stage: str = None) -> None:
        """Display current status of all stages in a clean format."""
        logger = get_logger()
        
        logger.info("=" * 70)
        logger.info("PIPELINE STATUS")
        logger.info("=" * 70)
        
        for stage in StageTracker.STAGE_ORDER:
            status = stage_status.get(stage, "pending")
            symbol = StageTracker.STATUS_SYMBOLS.get(status, "?")
            description = StageTracker.STATUS_DESCRIPTIONS.get(status, "Unknown")
            
            # Highlight current stage
            prefix = "→" if stage == current_stage else " "
            stage_display = stage.upper().ljust(12)
            
            logger.info(f"{prefix} {symbol} {stage_display} {status.ljust(12)} - {description}")
        
        logger.info("=" * 70)
    
    @staticmethod
    def get_progress_summary(stage_status: Dict[str, str]) -> str:
        """Get a one-line progress summary."""
        counts = {
            "done": 0,
            "failed": 0,
            "skipped": 0,
            "in_progress": 0,
            "pending": 0,
        }
        
        for stage in StageTracker.STAGE_ORDER:
            status = stage_status.get(stage, "pending")
            if status == "done":
                counts["done"] += 1
            elif status == "failed":
                counts["failed"] += 1
            elif status == "skipped":
                counts["skipped"] += 1
            elif status in ("running", "validating", "updating"):
                counts["in_progress"] += 1
            else:
                counts["pending"] += 1
        
        total_needed = 5 - counts["skipped"]
        completed = counts["done"]
        
        parts = [f"{completed}/{total_needed} complete"]
        if counts["in_progress"] > 0:
            parts.append(f"{counts['in_progress']} running")
        if counts["failed"] > 0:
            parts.append(f"{counts['failed']} failed")
        
        return " | ".join(parts)
    
    @staticmethod
    def is_complete(stage_status: Dict[str, str]) -> bool:
        """Check if all stages are either done or skipped."""
        for stage in StageTracker.STAGE_ORDER:
            status = stage_status.get(stage, "pending")
            if status not in ("done", "skipped"):
                return False
        return True
    
    @staticmethod
    def get_next_pending_stage(stage_status: Dict[str, str]) -> str:
        """Get the next stage that needs work (pending or failed)."""
        for stage in StageTracker.STAGE_ORDER:
            status = stage_status.get(stage, "pending")
            if status in ("pending", "failed"):
                return stage
        return "done"
