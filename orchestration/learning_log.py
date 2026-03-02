"""
Global Learning Log — Autonomous Factory
═════════════════════════════════════════
Cross-project learning from bug fixes and resolved issues.
Prevents repeat mistakes by injecting past learnings into worker prompts.
De-duplicates entries — increments occurrence_count for recurring patterns.
═════════════════════════════════════════
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from orchestration.database import ReadOnlyDB, queue_write

logger = logging.getLogger("factory.learning_log")


class LearningLog:
    """
    Global cross-project learning log.

    Every resolved bug/issue is logged with:
    - Root cause analysis
    - Fix applied
    - Prevention strategy
    - Keywords for search

    Before each task, relevant past fixes are injected into the worker prompt.
    """

    def __init__(self, read_db: ReadOnlyDB):
        self.db = read_db

    def log_fix(self, bug_description: str, root_cause: str, fix_applied: str,
                fixed_by: str, prevention_strategy: str = None,
                keywords: List[str] = None, project_id: str = None,
                task_id: str = None, dac_tag_id: int = None,
                project_type: str = None, phase: int = None) -> bool:
        """
        Log a bug fix to the global learning log.
        De-duplicates: if a similar entry exists, increments occurrence_count.
        """
        # Check for duplicate (same root cause, similar description)
        existing = self._find_similar(root_cause, bug_description)

        if existing:
            # Increment occurrence count
            log_id = existing["log_id"]
            try:
                queue_write(
                    operation="raw", table="learning_log",
                    params={
                        "sql": "UPDATE learning_log SET occurrence_count = occurrence_count + 1 WHERE log_id = ?",
                        "args": [log_id],
                    },
                    requester="learning_log",
                )
                logger.info(f"Learning log: incremented occurrence for log_id={log_id}")
                return True
            except RuntimeError as e:
                logger.error(f"Failed to increment learning log: {e}")
                return False

        # New entry
        try:
            queue_write(
                operation="insert", table="learning_log",
                params={
                    "project_id": project_id,
                    "task_id": task_id,
                    "dac_tag_id": dac_tag_id,
                    "bug_description": bug_description,
                    "root_cause": root_cause,
                    "fix_applied": fix_applied,
                    "prevention_strategy": prevention_strategy,
                    "fixed_by": fixed_by,
                    "keywords": json.dumps(keywords) if keywords else None,
                    "project_type": project_type,
                    "phase": phase,
                },
                requester="learning_log",
            )
            logger.info(f"Learning log: new entry — {bug_description[:60]}")
            return True
        except RuntimeError as e:
            logger.error(f"Failed to log learning: {e}")
            return False

    def _find_similar(self, root_cause: str, description: str) -> Optional[dict]:
        """Find a similar existing entry (same root cause pattern)."""
        # Search by keywords extracted from root cause
        words = root_cause.lower().split()
        significant_words = [w for w in words if len(w) > 4][:3]

        for word in significant_words:
            entries = self.db.get_learning_log(keywords=word, limit=10)
            for entry in entries:
                if self._is_similar(entry.get("root_cause", ""), root_cause):
                    return entry
        return None

    def _is_similar(self, existing: str, new: str) -> bool:
        """Check if two root causes are similar enough to de-duplicate."""
        existing_words = set(existing.lower().split())
        new_words = set(new.lower().split())

        if not existing_words or not new_words:
            return False

        overlap = len(existing_words & new_words)
        total = len(existing_words | new_words)

        return (overlap / total) > 0.6 if total > 0 else False

    # Entries younger than this threshold are eligible for injection regardless of count.
    _EXPIRY_DAYS = 90
    # Minimum occurrence count required for unvalidated entries (R11).
    _MIN_OCCURRENCES = 2

    @staticmethod
    def _is_qualified(entry: dict) -> bool:
        """
        Return True if an entry meets quality thresholds for prompt injection.

        Rules (R11):
        - occurrence_count >= 2  OR  validated == True
        - created_at within last 90 days (entries go stale)
        """
        occurrence = entry.get("occurrence_count", 1)
        validated = entry.get("validated", False)

        if not (occurrence >= LearningLog._MIN_OCCURRENCES or validated):
            return False

        created_at = entry.get("created_at")
        if created_at:
            try:
                if isinstance(created_at, str):
                    # Handle both 'YYYY-MM-DD HH:MM:SS' and ISO format
                    created_dt = datetime.fromisoformat(
                        created_at.replace(" ", "T").rstrip("Z")
                    )
                else:
                    created_dt = created_at
                if datetime.utcnow() - created_dt > timedelta(days=LearningLog._EXPIRY_DAYS):
                    return False
            except (ValueError, TypeError):
                pass  # Unparseable date — allow through

        return True

    def search_similar(self, description: str, project_type: str = None,
                        limit: int = 5) -> List[dict]:
        """Search for past learnings similar to a description.
        Only returns entries that pass quality thresholds (occurrence >= 2 or validated).
        """
        words = description.lower().split()
        significant = [w for w in words if len(w) > 3][:5]

        results = []
        seen_ids = set()

        for word in significant:
            entries = self.db.get_learning_log(
                project_type=project_type, keywords=word, limit=limit * 3
            )
            for entry in entries:
                if entry["log_id"] not in seen_ids and self._is_qualified(entry):
                    seen_ids.add(entry["log_id"])
                    results.append(entry)

        # Sort by occurrence count (most common first)
        results.sort(key=lambda x: x.get("occurrence_count", 1), reverse=True)
        return results[:limit]

    def inject_learnings(self, task_description: str,
                          project_type: str = None, limit: int = 3) -> str:
        """
        Build a learning injection string for worker prompts.
        Only injects entries with occurrence_count >= 2 OR validated=True,
        and created within the last 90 days (R11 — prevents context bloat).
        Returns formatted text or empty string if no qualified learnings.
        """
        learnings = self.search_similar(task_description, project_type, limit)

        if not learnings:
            return ""

        lines = ["## Past Learnings (avoid repeating these mistakes)"]
        for entry in learnings:
            occurrence = entry.get("occurrence_count", 1)
            lines.append(
                f"\n### [{entry.get('project_type', '?')}] "
                f"Occurred {occurrence}x"
            )
            lines.append(f"**Bug:** {entry['bug_description']}")
            lines.append(f"**Root cause:** {entry['root_cause']}")
            lines.append(f"**Fix:** {entry['fix_applied']}")
            if entry.get("prevention_strategy"):
                lines.append(f"**Prevention:** {entry['prevention_strategy']}")

        return "\n".join(lines)
