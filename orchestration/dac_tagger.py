"""
DaC Tagger — Autonomous Factory
════════════════════════════════
Auto-tags events with DaC categories for training data collection.
Maps: pipeline events → DaC tags → training_data table.

Tag Types:
- TRAP: Rule violations, malformed output, gaps
- SER: Security issues, contract violations, merge conflicts
- DOM: Bug patterns, logic errors, domain mistakes
- HRO: Human intervention required, double rejections
- HAL: Silent errors, timeouts, hallucinations, concurrency issues
- ENV: Environment issues, config errors, dependency problems
════════════════════════════════
"""

import json
import logging
from typing import Dict, List, Optional

from orchestration.database import ReadOnlyDB, queue_write

logger = logging.getLogger("factory.dac_tagger")

# Mapping: source event → DaC tag type
# G: Auto-generated solution hints per tag type, written to training_data
# on every tag creation (validated=False — pending human review).
_TRAINING_SOLUTIONS: Dict[str, str] = {
    "TRAP": "Ensure worker output is valid JSON and writes only within its module scope",
    "SER":  "Resolve security/contract/conflict issue before proceeding",
    "DOM":  "Review domain logic and fix the identified bug",
    "HRO":  "Escalate to human reviewer for decision",
    "HAL":  "Add error handling, timeout guards, and output validation",
    "ENV":  "Fix environment configuration or dependency mismatch",
}

EVENT_TAG_MAP = {
    # Pipeline events
    "bug_capture": "DOM",
    "silent_error": "HAL",
    "security_scan": "SER",
    "contract_violation": "SER",
    "merge_conflict": "SER",
    "malformed_output": "TRAP",
    "missing_test": "TRAP",
    "gap_detected": "TRAP",
    "double_rejection": "HRO",
    "human_escalation": "HRO",
    "task_timeout": "HAL",
    "worker_crash": "HAL",
    "hallucination": "HAL",
    "dependency_unclear": "TRAP",
    "config_error": "ENV",
    "env_mismatch": "ENV",
}


class DaCTagger:
    """
    Automatically tags events with DaC categories.
    Each tag is written to dac_tags table and optionally populates training_data.
    """

    def __init__(self, read_db: ReadOnlyDB = None):
        self.db = read_db
        self._pending_tags: List[dict] = []

    def tag(self, task_id: str, event_type: str, context: str,
            source_step: str = None, source_worker: str = None,
            project_id: str = None, project_type: str = None,
            phase: int = None, complexity: str = None) -> str:
        """
        Create a DaC tag for an event.

        Args:
            task_id: The task this tag relates to
            event_type: Event key from EVENT_TAG_MAP (or raw tag type)
            context: Description of what happened
            source_step: Which TDD/pipeline step triggered this
            source_worker: Which worker was involved

        Returns:
            The tag type assigned (TRAP/SER/DOM/HRO/HAL/ENV)
        """
        # Resolve tag type
        tag_type = EVENT_TAG_MAP.get(event_type, event_type)
        if tag_type not in ("TRAP", "SER", "DOM", "HRO", "HAL", "ENV"):
            logger.warning(f"Unknown event type '{event_type}', defaulting to TRAP")
            tag_type = "TRAP"

        # Queue tag creation
        tag_data = {
            "task_id": task_id,
            "tag_type": tag_type,
            "context": context,
            "source_step": source_step,
            "source_worker": source_worker,
            "project_id": project_id,
            "project_type": project_type,
            "phase": phase,
            "complexity": complexity,
            "status": "open",
        }

        try:
            queue_write(
                operation="insert", table="dac_tags",
                params=tag_data, requester="dac_tagger",
            )
            self._pending_tags.append(tag_data)
            logger.info(f"DaC tag: {tag_type} for {task_id} ({event_type})")
        except RuntimeError as e:
            logger.error(f"Failed to queue DaC tag: {e}")

        # G: Auto-populate training_data from every dac_tag (validated=False)
        try:
            queue_write(
                operation="insert", table="training_data",
                params={
                    "project_id": project_id or "unknown",
                    "bug_description": f"[{tag_type}] {context[:500]}",
                    "bug_context": json.dumps({
                        "task_id": task_id,
                        "event_type": event_type,
                        "tag_type": tag_type,
                        "source_step": source_step,
                        "source_worker": source_worker,
                    }),
                    "solution": _TRAINING_SOLUTIONS.get(tag_type, "Investigate and fix"),
                    "fixed_by": "auto_dac_tagger",
                    "validated": False,
                    "phase": str(phase) if phase is not None else None,
                },
                requester="dac_tagger",
            )
        except RuntimeError as e:
            logger.error(f"Failed to queue training_data row: {e}")

        # Wire to learning_log so the feedback loop is closed.
        # Previously broken: dac_tagger never called log_fix() → learning log stayed empty
        # → context_manager injected nothing → workers repeated same mistakes.
        if self.db:
            try:
                from orchestration.learning_log import LearningLog
                ll = LearningLog(self.db)
                context_words = [w for w in context.lower().split() if len(w) > 4][:3]
                ll.log_fix(
                    bug_description=f"[{tag_type}] {context[:300]}",
                    root_cause=f"[{tag_type}] {event_type}: {context[:150]}",
                    fix_applied=_TRAINING_SOLUTIONS.get(tag_type, "Investigate and fix"),
                    fixed_by="auto_dac_tagger",
                    prevention_strategy=f"Prevent {tag_type} pattern: {context[:200]}",
                    keywords=[tag_type, event_type, source_step or "", source_worker or ""] + context_words,
                    project_id=project_id,
                    task_id=task_id,
                    project_type=project_type,
                    phase=phase,
                )
            except Exception as e:
                logger.debug(f"learning_log write failed (non-fatal): {e}")

        return tag_type

    def tag_from_tdd_result(self, task_id: str, tdd_result: dict,
                             project_id: str = None):
        """
        Auto-tag from TDD pipeline results.
        Processes bugs, dac_tags, and failure indicators.
        """
        for bug in tdd_result.get("bugs", []):
            self.tag(
                task_id=task_id,
                event_type="bug_capture",
                context=f"Bug: {bug.get('description', 'unknown')} "
                        f"[severity={bug.get('severity', '?')}]",
                source_step=bug.get("step", "BC"),
                project_id=project_id,
            )

        # Process step-level tags
        for step_id, result in tdd_result.get("results", {}).items():
            if isinstance(result, dict):
                for tag in result.get("dac_tags", []):
                    self.tag(
                        task_id=task_id,
                        event_type=tag if tag in EVENT_TAG_MAP.values() else "gap_detected",
                        context=f"TDD step {step_id}: {result.get('output', '')[:200]}",
                        source_step=step_id,
                        project_id=project_id,
                    )

    def tag_gate_rejection(self, task_id: str, rejection_count: int,
                            gate_result: dict, project_id: str = None):
        """Tag quality gate rejections. Double rejection → HRO."""
        if rejection_count >= 2:
            self.tag(
                task_id=task_id,
                event_type="double_rejection",
                context=f"Quality gate rejected {rejection_count}x. "
                        f"Issues: {gate_result.get('issues', [])}",
                source_step="quality_gate",
                source_worker="kimi",
                project_id=project_id,
            )
        else:
            self.tag(
                task_id=task_id,
                event_type="gap_detected",
                context=f"Gate rejection #{rejection_count}: {gate_result.get('issues', [])}",
                source_step="quality_gate",
                source_worker="kimi",
                project_id=project_id,
            )

    def populate_training_data(self, task_id: str, tag_type: str,
                                context: str, resolution: str,
                                project_id: str = None):
        """
        Auto-populate training_data table from resolved DaC tags.
        Called when a tag is resolved (bug fixed, issue addressed).
        """
        try:
            queue_write(
                operation="insert", table="training_data",
                params={
                    "project_id": project_id or "unknown",
                    "bug_description": f"[{tag_type}] {context}",
                    "bug_context": json.dumps({"tag_type": tag_type, "task_id": task_id}),
                    "solution": resolution,
                    "fixed_by": "auto",
                    "validated": False,
                },
                requester="dac_tagger",
            )
        except RuntimeError as e:
            logger.error(f"Failed to populate training data: {e}")

    def get_stats(self) -> dict:
        """Get DaC tag statistics."""
        if not self.db:
            return {}

        stats = {}
        for tag_type in ("TRAP", "SER", "DOM", "HRO", "HAL", "ENV"):
            tags = self.db.get_dac_tags(tag_type=tag_type)
            open_count = sum(1 for t in tags if t.get("status") == "open")
            stats[tag_type] = {"total": len(tags), "open": open_count}
        return stats
