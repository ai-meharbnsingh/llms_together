"""
Wave 2 RED Tests — FER-AF-013, FER-AF-011, FER-AF-001
All three tests must FAIL against unpatched code.
"""
import asyncio
import json
import logging
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-013: LLM JSON parse failure in _plan_tasks_gsd must NOT store raw
#             response as task description — must return an error dict instead.
# ─────────────────────────────────────────────────────────────────────────────

class TestFerAF013PlanTasksGsdParseFail(unittest.IsolatedAsyncioTestCase):
    """
    FER-AF-013: When GSD task-planner LLM returns non-JSON (e.g. a long prose
    response), _plan_tasks_gsd must NOT fall back to a task whose description
    is the raw LLM output.  It must return an error result.
    """

    def _make_orchestrator(self):
        """Create a minimal MasterOrchestrator with mocked dependencies."""
        from orchestration.master_orchestrator import MasterOrchestrator
        from orchestration.database import ReadOnlyDB

        db = MagicMock(spec=ReadOnlyDB)
        router = MagicMock()
        config = {
            "factory": {"working_dir": "/tmp/test_factory", "factory_state_dir": None},
        }
        orch = MasterOrchestrator.__new__(MasterOrchestrator)
        orch.db = db
        orch.router = router
        orch.config = config
        orch.working_dir = Path("/tmp/test_factory")
        orch.phi3 = None
        orch._history_file = None
        orch._sessions_file = None
        orch.chat_history = []
        orch.current_project = None
        return orch

    async def test_parse_fail_returns_error_not_raw_response(self):
        """
        When the planner LLM returns prose (no JSON array), the method must
        return a dict with "error" set — NOT a tasks list where description is
        the raw 10K response.
        """
        orch = self._make_orchestrator()
        raw_prose = "I am a verbose LLM. " * 500  # ~10 KB non-JSON response

        mock_worker = MagicMock()
        mock_worker.send_message = AsyncMock(return_value={
            "success": True,
            "response": raw_prose,
        })

        with patch.object(orch, "_get_worker", return_value=mock_worker), \
             patch.object(orch, "_get_worker_name", return_value="test_planner"):
            result = await orch.plan_tasks_gsd(1, "Build a web app")

        # Must NOT succeed silently with raw prose as description
        self.assertIn("error", result,
                      "Expected 'error' key when parse fails, got: " + str(list(result.keys())))

        # Must NOT have a tasks list containing the raw response
        tasks = result.get("tasks", [])
        for t in tasks:
            desc = t.get("description", "")
            self.assertLess(
                len(desc), 500,
                f"Task description is suspiciously long ({len(desc)} chars) — "
                "raw LLM response stored as description!"
            )

    async def test_valid_json_still_works(self):
        """Control: valid JSON array response still produces normal task list."""
        orch = self._make_orchestrator()
        valid_json = json.dumps([
            {"module": "backend/api.py", "description": "Build REST API"},
            {"module": "frontend/app.tsx", "description": "Build React frontend"},
        ])

        mock_worker = MagicMock()
        mock_worker.send_message = AsyncMock(return_value={
            "success": True,
            "response": valid_json,
        })

        with patch.object(orch, "_get_worker", return_value=mock_worker), \
             patch.object(orch, "_get_worker_name", return_value="test_planner"):
            result = await orch.plan_tasks_gsd(1, "Build a web app")

        self.assertNotIn("error", result)
        self.assertEqual(len(result.get("tasks", [])), 2)


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-011: Cost tracking failure must be logged at WARNING, not DEBUG.
# ─────────────────────────────────────────────────────────────────────────────

class TestFerAF011CostTrackingLogLevel(unittest.TestCase):
    """
    FER-AF-011: Cost tracking write failures must be logged at WARNING level,
    not DEBUG.  In production the log level is INFO, so DEBUG messages are
    silently swallowed — failures become invisible.
    """

    def test_cost_tracking_failure_logged_at_warning(self):
        """
        Inspect the source of master_orchestrator.py and confirm that the
        cost-tracking exception handler uses logger.warning (not logger.debug).
        """
        src = Path(__file__).parent.parent / "orchestration" / "master_orchestrator.py"
        text = src.read_text()

        # Find the specific line that catches cost tracking failures
        lines = text.splitlines()
        offending_lines = []
        for i, line in enumerate(lines, 1):
            if "Cost tracking write failed" in line and "logger.debug" in line:
                offending_lines.append((i, line.strip()))

        self.assertEqual(
            offending_lines, [],
            f"Cost tracking failure is still logged at DEBUG on line(s): {offending_lines}\n"
            "Must be logger.warning() so it is visible at production log level."
        )

    def test_cost_tracking_uses_warning_level(self):
        """Positive check: warning-level log IS present for cost tracking failures."""
        src = Path(__file__).parent.parent / "orchestration" / "master_orchestrator.py"
        text = src.read_text()
        self.assertIn(
            "Cost tracking write failed",
            text,
            "Cost tracking failure message not found in orchestrator source."
        )
        # Find the line and check it uses warning
        for line in text.splitlines():
            if "Cost tracking write failed" in line:
                self.assertIn(
                    "logger.warning",
                    line,
                    f"Expected logger.warning but found: {line.strip()}"
                )


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-001: factory_state_dir path must resolve consistently between
#             Orchestrator and Watchdog — both must land in the same directory.
# ─────────────────────────────────────────────────────────────────────────────

class TestFerAF001StateDirectoryConsistency(unittest.TestCase):
    """
    FER-AF-001: Both MasterOrchestrator and MasterWatchdog must resolve
    factory_state_dir to the same absolute path for a given working_dir.

    Config has: "factory_state_dir": "factory_state", "working_dir": "~/working"
    Watchdog resolves to: working_dir / "autonomous_factory" / "factory_state"
    Orchestrator used to use: Path("factory_state")  ← relative, CWD-dependent

    After fix: Orchestrator must resolve to the same absolute path as Watchdog.
    """

    def _watchdog_state_dir(self, working_dir: str) -> Path:
        """Replicate Watchdog's state_dir formula."""
        return Path(working_dir).expanduser() / "autonomous_factory" / "factory_state"

    def test_orchestrator_state_dir_matches_watchdog(self):
        """
        Given the same config, Orchestrator's actual _history_file parent dir
        must equal Watchdog's state_dir formula.
        """
        working_dir = "/tmp/test_af_wd_match"
        config = {
            "factory": {
                "working_dir": working_dir,
                "factory_state_dir": "factory_state",
                "log_level": "INFO",
            }
        }

        watchdog_dir = self._watchdog_state_dir(working_dir)

        from orchestration.master_orchestrator import MasterOrchestrator
        from orchestration.database import ReadOnlyDB

        db = MagicMock(spec=ReadOnlyDB)
        db.set_requester = MagicMock()
        db.get_chat_history = MagicMock(return_value=[])
        router = MagicMock()

        orch = MasterOrchestrator(
            read_db=db,
            role_router=router,
            config=config,
            working_dir=working_dir,
        )

        self.assertIsNotNone(
            orch._history_file,
            "_history_file must be set when factory_state_dir is configured"
        )
        orch_dir = orch._history_file.parent.resolve()

        self.assertEqual(
            watchdog_dir.resolve(),
            orch_dir,
            f"Path mismatch!\n  Watchdog expects: {watchdog_dir.resolve()}\n"
            f"  Orchestrator uses: {orch_dir}\n"
            "Orchestrator must resolve relative factory_state_dir against working_dir."
        )

    def test_orchestrator_init_uses_absolute_path(self):
        """
        After fix: the _history_file path in a real Orchestrator instance must
        be absolute and must start with working_dir.
        """
        import tempfile, os
        working_dir = "/tmp/test_af_root"
        config = {
            "factory": {
                "working_dir": working_dir,
                "factory_state_dir": "factory_state",
                "log_level": "INFO",
            }
        }

        from orchestration.master_orchestrator import MasterOrchestrator
        from orchestration.database import ReadOnlyDB

        db = MagicMock(spec=ReadOnlyDB)
        db.set_requester = MagicMock()
        db.get_chat_history = MagicMock(return_value=[])
        router = MagicMock()

        orch = MasterOrchestrator(
            read_db=db,
            role_router=router,
            config=config,
            working_dir=working_dir,
        )

        self.assertIsNotNone(orch._history_file,
                             "_history_file must be set when factory_state_dir is configured")
        self.assertTrue(
            orch._history_file.is_absolute(),
            f"_history_file must be an absolute path, got: {orch._history_file}"
        )
        self.assertTrue(
            str(orch._history_file).startswith(working_dir),
            f"_history_file must be under working_dir ({working_dir}), "
            f"got: {orch._history_file}"
        )


if __name__ == "__main__":
    unittest.main()
