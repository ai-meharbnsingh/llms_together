"""
Wave 1 Tests — Issues 2, 6, 7 (DB layer)
RED phase: these tests define the expected behaviour.
Run: pytest tests/test_7issues_wave1.py -v
"""
import asyncio
import json
import sys
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─── Issue 2: Git asyncio.Lock ─────────────────────────────────────────────

class TestGitManagerLock:
    """GitManager.atomic_commit must serialise parallel callers via asyncio.Lock."""

    def test_git_manager_has_commit_lock_attribute(self):
        """GitManager must expose a _commit_lock: asyncio.Lock."""
        from orchestration.git_manager import GitManager
        gm = GitManager("/tmp/fake_project")
        assert hasattr(gm, "_commit_lock"), (
            "GitManager missing _commit_lock — parallel commits will race"
        )
        assert isinstance(gm._commit_lock, asyncio.Lock), (
            "_commit_lock must be asyncio.Lock, not some other type"
        )

    def test_atomic_commit_is_coroutine(self):
        """atomic_commit must be an async def so it can await the lock."""
        import inspect
        from orchestration.git_manager import GitManager
        assert inspect.iscoroutinefunction(GitManager.atomic_commit), (
            "atomic_commit must be async def to use 'async with self._commit_lock'"
        )

    def test_concurrent_commits_serialise(self):
        """Two concurrent atomic_commit calls must not both enter the critical section at once."""
        from orchestration.git_manager import GitManager

        gm = GitManager("/tmp/fake_project")
        concurrent_inside = []

        async def _run():
            inside = []

            original_run_git = gm._run_git
            call_count = 0

            def fake_run_git(*args, **kwargs):
                nonlocal call_count
                if args[0] == "add":
                    inside.append(1)
                    concurrent_inside.append(len(inside))
                    inside.pop()
                return ""

            gm._run_git = fake_run_git

            async def commit_task(task_id):
                await gm.atomic_commit(task_id, f"msg for {task_id}")

            await asyncio.gather(
                commit_task("task_001"),
                commit_task("task_002"),
                commit_task("task_003"),
            )

        asyncio.run(_run())
        # With the lock, never more than 1 concurrent inside the critical section
        assert all(c <= 1 for c in concurrent_inside), (
            f"Lock not working — concurrent_inside counts: {concurrent_inside}"
        )


# ─── Issue 6: Learning log filter ─────────────────────────────────────────

class TestLearningLogFilter:
    """inject_learnings must skip entries with occurrence_count < 2 and not validated."""

    def _make_db(self, entries):
        db = MagicMock()
        db.get_learning_log.return_value = entries
        return db

    def test_inject_learnings_skips_single_occurrence_unvalidated(self):
        """Entry with occurrence_count=1, validated=False must NOT be injected."""
        from orchestration.learning_log import LearningLog
        db = self._make_db([
            {
                "log_id": 1,
                "bug_description": "some bug",
                "root_cause": "bad logic",
                "fix_applied": "fixed it",
                "occurrence_count": 1,
                "validated": False,
                "project_type": "web",
                "prevention_strategy": None,
            }
        ])
        ll = LearningLog(db)
        result = ll.inject_learnings("some task description", project_type="web")
        assert result == "", (
            f"Expected empty string for unvalidated single-occurrence entry, got: {result!r}"
        )

    def test_inject_learnings_includes_multi_occurrence(self):
        """Entry with occurrence_count >= 2 MUST be injected."""
        from orchestration.learning_log import LearningLog
        db = self._make_db([
            {
                "log_id": 2,
                "bug_description": "recurring bug",
                "root_cause": "bad logic repeated",
                "fix_applied": "fixed properly",
                "occurrence_count": 3,
                "validated": False,
                "project_type": "web",
                "prevention_strategy": "use a linter",
            }
        ])
        ll = LearningLog(db)
        result = ll.inject_learnings("some task about bad logic", project_type="web")
        assert "recurring bug" in result, (
            f"Expected multi-occurrence entry to be injected, got: {result!r}"
        )

    def test_inject_learnings_includes_validated_single(self):
        """Entry with validated=True must be injected even if occurrence_count=1."""
        from orchestration.learning_log import LearningLog
        db = self._make_db([
            {
                "log_id": 3,
                "bug_description": "validated single bug",
                "root_cause": "unique root cause",
                "fix_applied": "specific fix",
                "occurrence_count": 1,
                "validated": True,
                "project_type": "web",
                "prevention_strategy": None,
            }
        ])
        ll = LearningLog(db)
        result = ll.inject_learnings("unique root task", project_type="web")
        assert "validated single bug" in result, (
            f"Expected validated entry to be injected, got: {result!r}"
        )

    def test_inject_learnings_excludes_expired_entries(self):
        """Entries older than 90 days must NOT be injected."""
        from orchestration.learning_log import LearningLog
        old_date = (datetime.utcnow() - timedelta(days=91)).isoformat()
        db = self._make_db([
            {
                "log_id": 4,
                "bug_description": "old bug",
                "root_cause": "old root cause",
                "fix_applied": "old fix",
                "occurrence_count": 5,
                "validated": True,
                "project_type": "web",
                "prevention_strategy": None,
                "created_at": old_date,
            }
        ])
        ll = LearningLog(db)
        result = ll.inject_learnings("old root task", project_type="web")
        assert result == "", (
            f"Expected expired entry to be excluded, got: {result!r}"
        )


class TestContextManagerLearningFilter:
    """ContextManager.get_relevant_learnings must apply the same quality filter."""

    def test_get_relevant_learnings_filters_low_quality(self):
        """Returns only entries where occurrence_count >= 2 or validated=True."""
        from orchestration.context_manager import ContextManager

        db = MagicMock()
        db.get_learning_log.return_value = [
            {"log_id": 1, "bug_description": "b1", "root_cause": "rc1",
             "fix_applied": "f1", "occurrence_count": 1, "validated": False,
             "project_type": "web", "created_at": datetime.utcnow().isoformat()},
            {"log_id": 2, "bug_description": "b2", "root_cause": "rc2",
             "fix_applied": "f2", "occurrence_count": 3, "validated": False,
             "project_type": "web", "created_at": datetime.utcnow().isoformat()},
            {"log_id": 3, "bug_description": "b3", "root_cause": "rc3",
             "fix_applied": "f3", "occurrence_count": 1, "validated": True,
             "project_type": "web", "created_at": datetime.utcnow().isoformat()},
        ]
        cm = ContextManager("/tmp", db)
        results = cm.get_relevant_learnings("web", ["rc1", "rc2", "rc3"])
        ids = [r["log_id"] for r in results]
        assert 1 not in ids, "log_id=1 should be filtered (count=1, not validated)"
        assert 2 in ids, "log_id=2 should pass (count=3)"
        assert 3 in ids, "log_id=3 should pass (validated=True)"


# ─── Issue 7: WatchdogDB validate_training_data ────────────────────────────

class TestWatchdogDBValidateTrainingData:
    """WatchdogDB must expose validate_training_data(data_id) -> bool."""

    def test_watchdog_db_has_validate_training_data(self):
        """WatchdogDB must have a validate_training_data method."""
        from orchestration.database import WatchdogDB
        assert hasattr(WatchdogDB, "validate_training_data"), (
            "WatchdogDB missing validate_training_data() — "
            "training data can never transition to validated=True"
        )

    def test_validate_training_data_signature(self):
        """validate_training_data must accept data_id parameter."""
        import inspect
        from orchestration.database import WatchdogDB
        sig = inspect.signature(WatchdogDB.validate_training_data)
        params = list(sig.parameters.keys())
        assert "data_id" in params, (
            f"validate_training_data missing 'data_id' param. Got: {params}"
        )

    def test_validate_training_data_runs(self, tmp_path):
        """validate_training_data(training_id) must update validated=True in DB without error."""
        from orchestration.database import WatchdogDB

        db_path = str(tmp_path / "test_factory.db")
        wdb = WatchdogDB(db_path)

        # Insert a project first (FK parent required by schema)
        with wdb._write_conn() as conn:
            conn.execute(
                "INSERT INTO projects (project_id, name, status) VALUES (?,?,?)",
                ("proj_test_1", "Test Project", "active")
            )
            conn.commit()

        # Insert a training_data row
        with wdb._write_conn() as conn:
            conn.execute(
                "INSERT INTO training_data (project_id, bug_description, bug_context, "
                "solution, fixed_by, validated) VALUES (?,?,?,?,?,?)",
                ("proj_test_1", "test bug", "{}", "test fix", "auto", False)
            )
            conn.commit()
            row = conn.execute(
                "SELECT training_id FROM training_data WHERE project_id='proj_test_1'"
            ).fetchone()
        training_id = row[0]

        result = wdb.validate_training_data(training_id)
        assert result is True, f"validate_training_data returned {result}, expected True"

        # Verify DB state
        with wdb._write_conn() as conn:
            row = conn.execute(
                "SELECT validated FROM training_data WHERE training_id=?", (training_id,)
            ).fetchone()
        assert row[0] in (1, True), (
            f"validated flag not set to True in DB, got: {row[0]}"
        )
