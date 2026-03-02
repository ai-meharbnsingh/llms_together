"""
Wave 3 RED Tests — FER-AF-016, FER-AF-003
All tests must FAIL against unpatched code.

FER-AF-016: Git state not verified before phase execution
            → detached HEAD / dirty index should surface early, not mid-build
FER-AF-003: learning_log.inject_learnings() is never called in execute pipeline
            → wired but silent; past fixes never reach workers
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-016: GitManager must have a verify_state() method that detects
#             detached HEAD, and _phase_build must call it before execution.
# ─────────────────────────────────────────────────────────────────────────────

class TestFerAF016GitStateVerification(unittest.TestCase):
    """
    FER-AF-016: Before any phase executes, git state must be verified.
    A detached HEAD must surface as an explicit, actionable error rather than
    a cryptic failure deep inside the build.
    """

    def test_git_manager_has_verify_state_method(self):
        """GitManager must expose a verify_state() method."""
        from orchestration.git_manager import GitManager
        self.assertTrue(
            hasattr(GitManager, "verify_state"),
            "GitManager is missing verify_state() method — add it (FER-AF-016)"
        )

    def test_verify_state_detects_detached_head(self):
        """
        verify_state() must return {"ok": False, "issue": <message>} when
        git reports a detached HEAD (branch --show-current returns empty).
        """
        import tempfile
        from orchestration.git_manager import GitManager

        with tempfile.TemporaryDirectory() as tmpdir:
            gm = GitManager(tmpdir)
            # Simulate detached HEAD: branch --show-current returns ""
            with patch.object(gm, "_run_git", side_effect=lambda *args, **kw: ""):
                result = gm.verify_state()

        self.assertIsInstance(result, dict, "verify_state() must return a dict")
        self.assertFalse(
            result.get("ok", True),
            "verify_state() must return ok=False for detached HEAD"
        )
        issue = result.get("issue", "")
        self.assertIn("detach", issue.lower(),
                      f"issue message must mention detached HEAD, got: {issue!r}")

    def test_verify_state_passes_on_normal_branch(self):
        """verify_state() must return {"ok": True} when on a named branch."""
        import tempfile
        from orchestration.git_manager import GitManager

        with tempfile.TemporaryDirectory() as tmpdir:
            gm = GitManager(tmpdir)
            # Normal branch: branch --show-current returns branch name
            with patch.object(gm, "_run_git", side_effect=lambda *args, **kw: "develop"):
                result = gm.verify_state()

        self.assertTrue(result.get("ok"), "verify_state() must return ok=True on a normal branch")

    def test_phase_build_calls_verify_state_before_execution(self):
        """
        _phase_build must call git_mgr.verify_state() before touching tasks.
        If verify_state returns ok=False, _phase_build must abort and return error.
        """
        from orchestration.master_orchestrator import MasterOrchestrator

        orch = _make_minimal_orchestrator()

        mock_git = MagicMock()
        mock_git.verify_state.return_value = {"ok": False, "issue": "detached HEAD at abc123"}
        mock_git.create_phase_branch.return_value = "phase/1-backend"

        mock_db = MagicMock()
        mock_db.get_tasks_by_phase.return_value = [
            {"task_id": "t1", "description": "Task 1", "phase": 1, "module": "backend/api.py"}
        ]
        orch.db = mock_db

        result = asyncio.run(
            orch._phase_build(
                project={"project_id": "proj1", "project_type": "web"},
                project_path="/tmp/proj",
                phase_num=1,
                context_mgr=MagicMock(),
                rules_engine=MagicMock(),
                dac_tagger=MagicMock(),
                learning_log=MagicMock(),
                git_mgr=mock_git,
                on_progress=None,
            )
        )

        mock_git.verify_state.assert_called_once()
        self.assertFalse(
            result.get("success", True),
            "_phase_build must return success=False when git state is invalid"
        )
        self.assertIn(
            "error", result,
            "_phase_build must include 'error' key on git state failure"
        )


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-003: inject_learnings() must be called in the execution pipeline.
# The result must be appended to the worker prompt.
# ─────────────────────────────────────────────────────────────────────────────

class TestFerAF003LearningInjection(unittest.IsolatedAsyncioTestCase):
    """
    FER-AF-003: learning_log.inject_learnings() must be called during task
    execution so workers benefit from past failures.  The injection text must
    reach the prompt sent to the worker.
    """

    async def test_inject_learnings_called_during_task_execution(self):
        """
        _execute_single_task must call learning_log.inject_learnings() with
        the task description and project type.
        """
        from orchestration.master_orchestrator import MasterOrchestrator
        from orchestration.learning_log import LearningLog

        orch = _make_minimal_orchestrator()

        mock_learning_log = MagicMock(spec=LearningLog)
        mock_learning_log.inject_learnings.return_value = ""  # empty = no learnings

        mock_worker = MagicMock()
        mock_worker.send_message = AsyncMock(return_value={"success": False, "error": "no worker"})
        mock_context_mgr = MagicMock()
        mock_context_mgr.build_task_prompt.return_value = "Base task prompt"

        task = {
            "task_id": "proj1_p1_t01",
            "description": "Build REST API",
            "phase": 1,
            "module": "backend/api.py",
        }
        project = {"project_id": "proj1", "project_type": "web", "name": "Test"}

        with patch.object(orch, "_get_worker", return_value=mock_worker), \
             patch.object(orch, "_classify_task", return_value="simple"), \
             patch.object(orch, "_get_worker_name", return_value="qwen"):
            await orch._execute_single_task(
                task=task,
                project=project,
                project_path="/tmp/proj",
                context_mgr=mock_context_mgr,
                rules_engine=MagicMock(),
                dac_tagger=MagicMock(),
                git_mgr=MagicMock(),
                learning_log=mock_learning_log,
                on_progress=None,
            )

        mock_learning_log.inject_learnings.assert_called_once()
        call_args = mock_learning_log.inject_learnings.call_args
        self.assertIn("Build REST API", str(call_args),
                      "inject_learnings must be called with task description")

    async def test_learning_text_included_in_worker_prompt(self):
        """
        When inject_learnings() returns non-empty text, it must appear in the
        prompt sent to the worker (so past mistakes actually reach the LLM).
        """
        from orchestration.master_orchestrator import MasterOrchestrator
        from orchestration.learning_log import LearningLog

        orch = _make_minimal_orchestrator()
        learning_text = (
            "## Past Learnings\n"
            "**Bug:** SQL injection via f-string\n"
            "**Fix:** Use parameterised queries"
        )

        mock_learning_log = MagicMock(spec=LearningLog)
        mock_learning_log.inject_learnings.return_value = learning_text

        captured_prompts = []

        async def capture_send(*args, **kwargs):
            captured_prompts.append(args[0] if args else kwargs.get("message", ""))
            return {"success": False, "error": "no worker"}

        mock_worker = MagicMock()
        mock_worker.send_message = AsyncMock(side_effect=capture_send)
        mock_context_mgr = MagicMock()
        mock_context_mgr.build_task_prompt.return_value = "Base task prompt"

        task = {
            "task_id": "proj1_p1_t02",
            "description": "Implement user login",
            "phase": 1,
            "module": "backend/auth.py",
        }
        project = {"project_id": "proj1", "project_type": "web", "name": "Test"}

        with patch.object(orch, "_get_worker", return_value=mock_worker), \
             patch.object(orch, "_classify_task", return_value="simple"), \
             patch.object(orch, "_get_worker_name", return_value="qwen"):
            await orch._execute_single_task(
                task=task,
                project=project,
                project_path="/tmp/proj",
                context_mgr=mock_context_mgr,
                rules_engine=MagicMock(),
                dac_tagger=MagicMock(),
                git_mgr=MagicMock(),
                learning_log=mock_learning_log,
                on_progress=None,
            )

        self.assertTrue(
            captured_prompts,
            "Worker send_message was never called — cannot verify prompt contents"
        )
        combined = " ".join(captured_prompts)
        self.assertIn(
            "Past Learnings", combined,
            "Learning injection text must appear in worker prompt"
        )

    async def test_execute_single_task_accepts_learning_log_param(self):
        """
        _execute_single_task must accept a learning_log keyword argument
        without raising TypeError.
        """
        from orchestration.master_orchestrator import MasterOrchestrator
        from orchestration.learning_log import LearningLog
        import inspect

        sig = inspect.signature(MasterOrchestrator._execute_single_task)
        self.assertIn(
            "learning_log", sig.parameters,
            "_execute_single_task must have a 'learning_log' parameter (FER-AF-003)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_minimal_orchestrator():
    """Build a MasterOrchestrator with all heavy deps mocked out."""
    from orchestration.master_orchestrator import MasterOrchestrator
    from orchestration.database import ReadOnlyDB

    db = MagicMock(spec=ReadOnlyDB)
    db.set_requester = MagicMock()
    db.get_chat_history = MagicMock(return_value=[])
    router = MagicMock()
    config = {
        "factory": {
            "working_dir": "/tmp/test_factory_w3",
            "factory_state_dir": None,
            "log_level": "INFO",
        }
    }
    orch = MasterOrchestrator(
        read_db=db,
        role_router=router,
        config=config,
        working_dir="/tmp/test_factory_w3",
    )
    return orch


if __name__ == "__main__":
    unittest.main()
