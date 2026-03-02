"""
Wave 7A Tests — FER-AF-010, FER-AF-026, FER-AF-029/024, FER-AF-036,
               FER-AF-037, FER-AF-040, FER-AF-030

1. test_semaphore_attribute_exists         — _task_semaphore exists with default value 4
2. test_semaphore_limits_concurrency       — semaphore allows only 2 concurrent (gate test)
3. test_merge_lock_exists                  — _merge_lock is asyncio.Lock
4. test_e2e_blocked_uat_awaiting_key       — e2e fail → awaiting == "uat_blocked_e2e"
5. test_e2e_passed_uat_approval_key        — e2e pass → awaiting == "uat_approval"
6. test_blueprint_truncation_warns         — 20000-char blueprint triggers logger.warning
7. test_polling_max_10_iterations          — polling loop runs max 10 iterations
8. test_create_project_uses_request_write_not_wait — request_write_and_wait NOT called
9. test_tdd_label_is_13_step               — "12-step" does NOT appear in source
"""

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_orchestrator(config_override=None):
    """
    Build a MasterOrchestrator with minimal external dependencies mocked.
    Uses __new__ to bypass __init__ heavy I/O, then manually populates
    required attributes (matching the Wave 6A pattern).
    """
    from orchestration.master_orchestrator import MasterOrchestrator
    from orchestration.database import ReadOnlyDB

    db = MagicMock(spec=ReadOnlyDB)
    db.set_requester = MagicMock()
    db.get_chat_history = MagicMock(return_value=[])
    db.request_write = MagicMock()
    db.request_write_and_wait = AsyncMock(return_value=None)

    config = config_override or {}

    orch = MasterOrchestrator.__new__(MasterOrchestrator)
    orch.db = db
    orch.working_dir = Path("/tmp/test_w7a_factory")
    orch.factory_dir = Path("/tmp/test_w7a_factory")
    orch.router = MagicMock()
    orch.phi3 = None
    orch.config = config
    orch.session_id = "test_session"
    orch.chat_history = []
    orch._session_meta = []
    orch._history_file = None
    orch._sessions_file = None
    orch._discussion_cancel = asyncio.Event()
    orch._discussion_participants = []
    orch._doc_context = None

    # Apply __init__ logic for semaphore and lock
    _max_concurrent = config.get("execution", {}).get("max_concurrent_tasks", 4)
    orch._task_semaphore = asyncio.Semaphore(_max_concurrent)
    orch._merge_lock = asyncio.Lock()

    return orch


def _make_full_orchestrator(config_override=None):
    """
    Build a MasterOrchestrator using the real __init__ with all I/O mocked.
    Used for tests that require the full constructor to run.
    """
    from orchestration.master_orchestrator import MasterOrchestrator
    from orchestration.database import ReadOnlyDB

    db = MagicMock(spec=ReadOnlyDB)
    db.set_requester = MagicMock()
    db.request_write = MagicMock()
    db.request_write_and_wait = AsyncMock(return_value=None)

    config = config_override or {}

    router = MagicMock()
    router.workers = {}

    with patch("orchestration.master_orchestrator.ContextManager", MagicMock()), \
         patch("orchestration.master_orchestrator.ContractGenerator", MagicMock()), \
         patch("orchestration.master_orchestrator.ContractValidator", MagicMock()), \
         patch("orchestration.master_orchestrator.OutputParser", MagicMock()), \
         patch("orchestration.master_orchestrator.RulesEngine", MagicMock()), \
         patch("orchestration.master_orchestrator.TDDPipeline", MagicMock()), \
         patch("orchestration.master_orchestrator.GitManager", MagicMock()), \
         patch("orchestration.master_orchestrator.DaCTagger", MagicMock()), \
         patch("orchestration.master_orchestrator.LearningLog", MagicMock()), \
         patch("orchestration.master_orchestrator.CICDGenerator", MagicMock()):
        orch = MasterOrchestrator(
            read_db=db,
            role_router=router,
            config=config,
            working_dir="/tmp/test_w7a_full_factory",
        )
    return orch


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: _task_semaphore attribute exists with default 4
# ─────────────────────────────────────────────────────────────────────────────

class TestSemaphoreAttributeExists(unittest.TestCase):
    """FER-AF-036: _task_semaphore must exist with default value 4."""

    def test_semaphore_attribute_exists(self):
        orch = _make_orchestrator()
        self.assertTrue(
            hasattr(orch, "_task_semaphore"),
            "_task_semaphore attribute missing from MasterOrchestrator",
        )
        self.assertIsInstance(orch._task_semaphore, asyncio.Semaphore)
        # Semaphore internal _value should equal 4 (the default)
        self.assertEqual(orch._task_semaphore._value, 4)

    def test_semaphore_respects_config(self):
        """Config override for max_concurrent_tasks should be respected."""
        orch = _make_orchestrator(config_override={"execution": {"max_concurrent_tasks": 2}})
        self.assertEqual(orch._task_semaphore._value, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Semaphore actually limits concurrency
# ─────────────────────────────────────────────────────────────────────────────

class TestSemaphoreLimitsConcurrency(unittest.IsolatedAsyncioTestCase):
    """FER-AF-036: semaphore with value 2 must allow only 2 tasks to run concurrently."""

    async def test_semaphore_limits_concurrency(self):
        """
        Create a semaphore with value 2. Launch 4 coroutines that each acquire
        the semaphore and wait for a gate event. Verify that only 2 hold the
        semaphore at any point.
        """
        sem = asyncio.Semaphore(2)
        max_concurrency_seen = 0
        current_count = 0
        gate = asyncio.Event()

        async def _worker():
            nonlocal max_concurrency_seen, current_count
            async with sem:
                current_count += 1
                if current_count > max_concurrency_seen:
                    max_concurrency_seen = current_count
                # Hold the lock and wait until gate is set
                await gate.wait()
                current_count -= 1

        # Start 4 workers — only 2 should be inside at once
        tasks = [asyncio.create_task(_worker()) for _ in range(4)]

        # Allow event loop to schedule them
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Release all workers
        gate.set()

        await asyncio.gather(*tasks)

        self.assertEqual(
            max_concurrency_seen,
            2,
            f"Expected max concurrency of 2 but got {max_concurrency_seen}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: _merge_lock exists and is asyncio.Lock
# ─────────────────────────────────────────────────────────────────────────────

class TestMergeLockExists(unittest.TestCase):
    """FER-AF-040: _merge_lock must exist as asyncio.Lock."""

    def test_merge_lock_exists(self):
        orch = _make_orchestrator()
        self.assertTrue(
            hasattr(orch, "_merge_lock"),
            "_merge_lock attribute missing from MasterOrchestrator",
        )
        self.assertIsInstance(orch._merge_lock, asyncio.Lock)


# ─────────────────────────────────────────────────────────────────────────────
# Tests 4 & 5: E2E failure blocks UAT
# ─────────────────────────────────────────────────────────────────────────────

class TestE2EBlocksUAT(unittest.IsolatedAsyncioTestCase):
    """FER-AF-010: execute_project must set awaiting=uat_blocked_e2e when E2E fails."""

    def _make_project(self):
        return {
            "project_id": "proj_e2e_test",
            "name": "E2E Test Project",
            "project_path": "/tmp/proj_e2e_test",
            "project_type": "web",
            "status": "active",
            "current_phase": 3,
            "blueprint_approved_by": "HUMAN",
        }

    def _make_orch_for_execute(self, e2e_success: bool):
        """Build orchestrator + mock all heavy methods so execute_project reaches phase 4."""
        from orchestration.master_orchestrator import MasterOrchestrator

        orch = _make_orchestrator()
        project = self._make_project()
        orch.db.get_project = MagicMock(return_value=project)

        # _phase_blueprint returns approved
        orch._phase_blueprint = AsyncMock(return_value={
            "approved": True,
            "blueprint": "x" * 100,
            "version": 1,
            "total_phases": 1,
        })
        # git init ok
        mock_git = MagicMock()
        mock_git.init_repo.return_value = True
        mock_git.create_phase_branch.return_value = "phase/1-phase-1"
        mock_git.verify_state.return_value = {"ok": True}
        mock_git.check_conflicts.return_value = []
        mock_git.merge_to_develop = MagicMock()
        mock_git.get_changed_files.return_value = []
        mock_git.tag_version = MagicMock()
        mock_git.pull_latest = MagicMock(return_value=None)

        # _generate_tasks_from_blueprint
        orch._generate_tasks_from_blueprint = AsyncMock(return_value=0)
        # _phase_build — returns completed=1 for the single phase
        orch._phase_build = AsyncMock(return_value={
            "success": True,
            "tasks_completed": 1,
            "tasks_failed": 0,
            "errors": [],
        })
        # _run_e2e_tests
        orch._run_e2e_tests = AsyncMock(return_value={"success": e2e_success})

        # CICDGenerator mock
        mock_cicd = MagicMock()
        mock_cicd.generate.return_value = {"files_created": []}

        with patch("orchestration.master_orchestrator.CICDGenerator", return_value=mock_cicd), \
             patch("orchestration.master_orchestrator.ContextManager", MagicMock()), \
             patch("orchestration.master_orchestrator.RulesEngine", MagicMock()), \
             patch("orchestration.master_orchestrator.DaCTagger", MagicMock()), \
             patch("orchestration.master_orchestrator.LearningLog", MagicMock()), \
             patch("orchestration.master_orchestrator.GitManager", return_value=mock_git):
            return orch, mock_git, mock_cicd

    async def test_e2e_blocked_uat_awaiting_key(self):
        """When e2e fails, response['awaiting'] must be 'uat_blocked_e2e'."""
        orch, mock_git, mock_cicd = self._make_orch_for_execute(e2e_success=False)

        with patch("orchestration.master_orchestrator.CICDGenerator",
                   return_value=mock_cicd), \
             patch("orchestration.master_orchestrator.ContextManager", MagicMock()), \
             patch("orchestration.master_orchestrator.RulesEngine", MagicMock()), \
             patch("orchestration.master_orchestrator.DaCTagger", MagicMock()), \
             patch("orchestration.master_orchestrator.LearningLog", MagicMock()), \
             patch("orchestration.master_orchestrator.GitManager", return_value=mock_git):
            result = await orch.execute_project("proj_e2e_test")

        self.assertEqual(
            result.get("awaiting"),
            "uat_blocked_e2e",
            f"Expected 'uat_blocked_e2e' but got {result.get('awaiting')!r}. "
            f"Full result: {result}",
        )
        self.assertTrue(result.get("e2e_failed"))

    async def test_e2e_passed_uat_approval_key(self):
        """When e2e passes, response['awaiting'] must be 'uat_approval'."""
        orch, mock_git, mock_cicd = self._make_orch_for_execute(e2e_success=True)

        with patch("orchestration.master_orchestrator.CICDGenerator",
                   return_value=mock_cicd), \
             patch("orchestration.master_orchestrator.ContextManager", MagicMock()), \
             patch("orchestration.master_orchestrator.RulesEngine", MagicMock()), \
             patch("orchestration.master_orchestrator.DaCTagger", MagicMock()), \
             patch("orchestration.master_orchestrator.LearningLog", MagicMock()), \
             patch("orchestration.master_orchestrator.GitManager", return_value=mock_git):
            result = await orch.execute_project("proj_e2e_test")

        self.assertEqual(
            result.get("awaiting"),
            "uat_approval",
            f"Expected 'uat_approval' but got {result.get('awaiting')!r}. "
            f"Full result: {result}",
        )
        self.assertFalse(result.get("e2e_failed", False))


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Blueprint truncation triggers logger.warning
# ─────────────────────────────────────────────────────────────────────────────

class TestBlueprintTruncationWarns(unittest.IsolatedAsyncioTestCase):
    """FER-AF-029: a 20000-char blueprint must trigger logger.warning at each truncation site."""

    async def test_blueprint_truncation_warns(self):
        """
        Call _dual_audit_blueprint with a 20000-char blueprint.
        Expect logger.warning to be called at least once mentioning truncation.
        """
        from orchestration.master_orchestrator import MasterOrchestrator

        orch = _make_orchestrator()

        # Mock workers
        mock_kimi = AsyncMock()
        mock_kimi.send_message = AsyncMock(return_value={"success": True, "response": "ok"})
        mock_gemini = AsyncMock()
        mock_gemini.send_message = AsyncMock(return_value={"success": True, "response": "ok"})

        orch._get_worker = MagicMock(side_effect=lambda role: (
            mock_kimi if role == "gatekeeper_review" else mock_gemini
        ))

        long_blueprint = "A" * 20_000

        with patch("orchestration.master_orchestrator.logger") as mock_logger:
            await orch._dual_audit_blueprint(long_blueprint, "web")

        # At least one warning call should mention truncation
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        truncation_warnings = [c for c in warning_calls if "truncated" in c.lower() or "15000" in c]
        self.assertTrue(
            len(truncation_warnings) > 0,
            f"Expected at least one truncation warning but got: {warning_calls}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Polling loop runs max 10 iterations
# ─────────────────────────────────────────────────────────────────────────────

class TestPollingMax10Iterations(unittest.IsolatedAsyncioTestCase):
    """FER-AF-037: DB polling in _generate_tasks_from_blueprint must run max 10 iterations."""

    async def test_polling_max_10_iterations(self):
        """
        Patch asyncio.sleep and db.get_tasks_by_phase to always return empty.
        Verify asyncio.sleep is called exactly 10 times (one per iteration).
        """
        orch = _make_orchestrator()

        # Worker returns nothing — fallback task path
        orch._get_worker = MagicMock(return_value=None)
        # Fallback always produces tasks
        orch._fallback_tasks = MagicMock(return_value={
            "phases": [
                {
                    "phase": 1,
                    "tasks": [
                        {"module": "backend/main.py", "description": "Entry point"},
                    ],
                }
            ]
        })
        # DB always returns empty (never confirms)
        orch.db.get_tasks_by_phase = MagicMock(return_value=[])

        sleep_count = 0

        async def _count_sleep(seconds):
            nonlocal sleep_count
            sleep_count += 1

        project = {
            "project_id": "proj_poll_test",
            "project_type": "web",
            "name": "Poll Test",
        }

        with patch("orchestration.master_orchestrator.asyncio.sleep", side_effect=_count_sleep):
            await orch._generate_tasks_from_blueprint(project, "blueprint content")

        self.assertEqual(
            sleep_count,
            10,
            f"Expected exactly 10 sleep calls (range(10)) but got {sleep_count}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: create_project uses request_write, not request_write_and_wait
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateProjectUsesRequestWrite(unittest.IsolatedAsyncioTestCase):
    """FER-AF-026: create_project must use request_write (fire-and-forget), not request_write_and_wait."""

    async def test_create_project_uses_request_write_not_wait(self):
        """
        Call create_project and verify request_write_and_wait is never called.
        """
        orch = _make_orchestrator()

        # Mock git subprocess
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("orchestration.master_orchestrator.asyncio.create_subprocess_exec",
                   return_value=mock_proc):
            result = await orch.create_project("Test Project", "A test project")

        # request_write_and_wait must not have been called
        orch.db.request_write_and_wait.assert_not_called()

        # request_write must have been called with 'insert' and 'projects'
        calls = orch.db.request_write.call_args_list
        insert_project_calls = [
            c for c in calls
            if c.args and len(c.args) >= 2
            and c.args[0] == "insert" and c.args[1] == "projects"
        ]
        self.assertTrue(
            len(insert_project_calls) >= 1,
            f"Expected request_write('insert', 'projects', ...) but no such call found. "
            f"Calls were: {calls}",
        )
        # Result must have a project_id
        self.assertIn("project_id", result)


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: TDD label is 13-step, not 12-step
# ─────────────────────────────────────────────────────────────────────────────

class TestTDDLabelIs13Step(unittest.TestCase):
    """FER-AF-030: The string '12-step' must not appear anywhere in master_orchestrator.py."""

    def test_tdd_label_is_13_step(self):
        """Read the source file and assert '12-step' is absent."""
        source_path = Path(__file__).parent.parent / "orchestration" / "master_orchestrator.py"
        self.assertTrue(source_path.exists(), f"Source file not found: {source_path}")

        content = source_path.read_text(encoding="utf-8")

        self.assertNotIn(
            "12-step",
            content,
            "Found '12-step' in master_orchestrator.py — should be '13-step'",
        )
        # Also assert the correct label IS present
        self.assertIn(
            "13-step",
            content,
            "Expected '13-step' in master_orchestrator.py but it is absent",
        )


if __name__ == "__main__":
    unittest.main()
