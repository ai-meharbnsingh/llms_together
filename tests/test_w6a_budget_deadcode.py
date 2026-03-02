"""
Wave 6A Tests — FER-AF-006 (Budget Cap Enforcement) + FER-AF-043 (Remove Dead API Methods)

FER-AF-006: Budget cap enforcement
  - If the sum of estimated_cost_usd for a project >= max_api_cost_per_project (50.0),
    _execute_single_task must return success=False with a budget-exceeded error.
  - When under budget the task must proceed normally (not short-circuit).

FER-AF-043: Dead API methods removed
  - create_phase_tasks(), classify_task(), send_to_tdd(), gatekeeper_review()
    and their private helper _save_task_file() must not exist on MasterOrchestrator.
"""

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers shared across test classes
# ─────────────────────────────────────────────────────────────────────────────

def _make_minimal_orchestrator(cost_rows=None, max_cost=50.0):
    """
    Build a MasterOrchestrator with all external dependencies mocked.

    cost_rows: list of dicts that get_cost_summary returns. Defaults to [].
    max_cost:  value injected into config["cost_controls"]["max_api_cost_per_project"].
    """
    from orchestration.master_orchestrator import MasterOrchestrator
    from orchestration.database import ReadOnlyDB

    if cost_rows is None:
        cost_rows = []

    db = MagicMock(spec=ReadOnlyDB)
    db.set_requester = MagicMock()
    db.get_chat_history = MagicMock(return_value=[])
    db.request_write = MagicMock()
    db.get_cost_summary = MagicMock(return_value=cost_rows)

    orch = MasterOrchestrator.__new__(MasterOrchestrator)
    orch.db = db
    orch.working_dir = Path("/tmp/test_w6a_factory")
    orch.factory_dir = Path("/tmp/test_w6a_factory")
    orch.router = MagicMock()
    orch.phi3 = None
    orch.config = {
        "cost_controls": {
            "max_api_cost_per_project": max_cost,
        }
    }
    return orch


def _make_task_and_project():
    task = {
        "task_id": "proj_budget_p1_001",
        "project_id": "proj_budget",
        "module": "backend/models.py",
        "description": "Create backend models",
        "phase": 1,
    }
    project = {
        "project_id": "proj_budget",
        "name": "Budget Test Project",
        "project_path": "/tmp/proj_budget",
        "project_type": "web",
    }
    return task, project


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-006: Budget cap enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestBudgetCapEnforcement(unittest.IsolatedAsyncioTestCase):
    """FER-AF-006: _execute_single_task must abort when project cost >= cap."""

    async def test_budget_exceeded_aborts_task(self):
        """
        When get_cost_summary returns rows that sum >= 50.0, the task must be
        aborted immediately with success=False and an error mentioning 'budget'.
        """
        cost_rows = [
            {"worker": "deepseek", "total_cost": 30.0, "calls": 10, "total_tokens": 50000},
            {"worker": "kimi",     "total_cost": 25.0, "calls":  5, "total_tokens": 20000},
        ]
        # total = 55.0 >= 50.0 → must abort
        orch = _make_minimal_orchestrator(cost_rows=cost_rows, max_cost=50.0)
        task, project = _make_task_and_project()

        mock_context_mgr = MagicMock()
        mock_context_mgr.build_task_prompt = MagicMock(return_value="task prompt")
        mock_rules_engine = MagicMock()
        mock_dac_tagger = MagicMock()
        mock_git_mgr = MagicMock()
        mock_git_mgr.pull_latest = MagicMock(return_value=None)

        result = await orch._execute_single_task(
            task=task,
            project=project,
            project_path="/tmp/proj_budget",
            context_mgr=mock_context_mgr,
            rules_engine=mock_rules_engine,
            dac_tagger=mock_dac_tagger,
            git_mgr=mock_git_mgr,
            on_progress=None,
        )

        self.assertFalse(result.get("success"),
                         f"Task must fail when over budget, got: {result}")
        error_lower = result.get("error", "").lower()
        self.assertIn("budget", error_lower,
                      f"Error must mention 'budget', got: {result.get('error')!r}")

    async def test_budget_at_exact_cap_aborts_task(self):
        """
        Boundary condition: when total_cost == max_api_cost_per_project exactly,
        the task must still be aborted (>= comparison).
        """
        cost_rows = [
            {"worker": "deepseek", "total_cost": 50.0, "calls": 20, "total_tokens": 100000},
        ]
        orch = _make_minimal_orchestrator(cost_rows=cost_rows, max_cost=50.0)
        task, project = _make_task_and_project()

        mock_context_mgr = MagicMock()
        mock_context_mgr.build_task_prompt = MagicMock(return_value="task prompt")
        mock_rules_engine = MagicMock()
        mock_dac_tagger = MagicMock()
        mock_git_mgr = MagicMock()
        mock_git_mgr.pull_latest = MagicMock(return_value=None)

        result = await orch._execute_single_task(
            task=task,
            project=project,
            project_path="/tmp/proj_budget",
            context_mgr=mock_context_mgr,
            rules_engine=mock_rules_engine,
            dac_tagger=mock_dac_tagger,
            git_mgr=mock_git_mgr,
            on_progress=None,
        )

        self.assertFalse(result.get("success"),
                         "Task must fail when cost exactly equals cap")
        self.assertIn("budget", result.get("error", "").lower(),
                      "Error must mention 'budget'")

    async def test_budget_not_exceeded_task_proceeds(self):
        """
        When cost is below the cap, the budget check must NOT abort. The task
        should reach the git-pull / classify step (mocked to complete normally).
        """
        cost_rows = [
            {"worker": "deepseek", "total_cost": 10.0, "calls": 5, "total_tokens": 20000},
        ]
        # total = 10.0 < 50.0 → must NOT abort
        orch = _make_minimal_orchestrator(cost_rows=cost_rows, max_cost=50.0)
        task, project = _make_task_and_project()

        mock_worker = AsyncMock()
        mock_worker.send_message = AsyncMock(return_value={
            "success": True,
            "response": '{"files": [], "decisions": []}',
            "tokens": {"prompt": 100, "completion": 50},
            "elapsed_ms": 200,
        })

        orch._get_worker = MagicMock(return_value=mock_worker)
        orch._get_worker_name = MagicMock(return_value="deepseek-mock")
        orch._classify_task = AsyncMock(return_value="low")
        orch._quality_gate = AsyncMock(return_value={
            "verdict": "APPROVED", "issues": [], "dac_tags": [], "confidence": 0.9,
            "by": "kimi",
        })

        mock_context_mgr = MagicMock()
        mock_context_mgr.build_task_prompt = MagicMock(return_value="task prompt")

        mock_rules_engine = MagicMock()
        mock_rules_engine.check_automated_rules = MagicMock()

        mock_dac_tagger = MagicMock()
        mock_dac_tagger.tag = MagicMock()
        mock_dac_tagger.tag_from_tdd_result = MagicMock()
        mock_dac_tagger.tag_gate_rejection = MagicMock()

        mock_git_mgr = MagicMock()
        mock_git_mgr.pull_latest = MagicMock(return_value=None)
        mock_git_mgr.atomic_commit = AsyncMock(return_value={"success": True})

        no_violations_summary = {
            "files_written": [],
            "decisions_logged": [],
            "escalations": [],
            "notes": [],
            "tests_needed": [],
            "scope_violations": [],
        }

        with patch("orchestration.master_orchestrator.OutputParser") as MockParser, \
             patch("orchestration.master_orchestrator.ContractValidator") as MockValidator, \
             patch("orchestration.master_orchestrator.TDDPipeline") as MockTDD, \
             patch("orchestration.database.queue_write"):

            mock_parser_instance = MagicMock()
            mock_parser_instance.parse_and_apply = MagicMock(
                return_value=(no_violations_summary, [])
            )
            MockParser.return_value = mock_parser_instance

            mock_validator_instance = MagicMock()
            mock_validator_instance.load_contracts = MagicMock(return_value=False)
            MockValidator.return_value = mock_validator_instance

            mock_tdd_instance = MagicMock()
            mock_tdd_instance.execute = AsyncMock(return_value={
                "steps_passed": 13, "steps_failed": 0, "passed": True,
            })
            MockTDD.return_value = mock_tdd_instance

            result = await orch._execute_single_task(
                task=task,
                project=project,
                project_path="/tmp/proj_budget",
                context_mgr=mock_context_mgr,
                rules_engine=mock_rules_engine,
                dac_tagger=mock_dac_tagger,
                git_mgr=mock_git_mgr,
                on_progress=None,
            )

        # The task should NOT have failed due to budget
        error = result.get("error", "")
        self.assertNotIn("budget", error.lower(),
                         f"Task should not have been aborted for budget, got error: {error!r}")

    async def test_budget_check_uses_config_fallback_50(self):
        """
        If config has no cost_controls key at all, default cap of 50.0 is used.
        A project spending 60.0 must still be aborted.
        """
        cost_rows = [
            {"worker": "gemini", "total_cost": 60.0, "calls": 30, "total_tokens": 200000},
        ]
        orch = _make_minimal_orchestrator(cost_rows=cost_rows)
        # Override config to have NO cost_controls
        orch.config = {}
        task, project = _make_task_and_project()

        mock_context_mgr = MagicMock()
        mock_rules_engine = MagicMock()
        mock_dac_tagger = MagicMock()
        mock_git_mgr = MagicMock()
        mock_git_mgr.pull_latest = MagicMock(return_value=None)

        result = await orch._execute_single_task(
            task=task,
            project=project,
            project_path="/tmp/proj_budget",
            context_mgr=mock_context_mgr,
            rules_engine=mock_rules_engine,
            dac_tagger=mock_dac_tagger,
            git_mgr=mock_git_mgr,
            on_progress=None,
        )

        self.assertFalse(result.get("success"),
                         "Must abort even when config has no cost_controls key (fallback 50.0)")
        self.assertIn("budget", result.get("error", "").lower(),
                      "Error must mention 'budget'")

    async def test_budget_check_writes_failed_status_on_abort(self):
        """
        When budget is exceeded, a DB write must be queued to mark the task as
        status='failed' with current_step='BUDGET_EXCEEDED'.
        """
        cost_rows = [
            {"worker": "deepseek", "total_cost": 55.0, "calls": 25, "total_tokens": 150000},
        ]
        orch = _make_minimal_orchestrator(cost_rows=cost_rows, max_cost=50.0)
        task, project = _make_task_and_project()

        mock_context_mgr = MagicMock()
        mock_rules_engine = MagicMock()
        mock_dac_tagger = MagicMock()
        mock_git_mgr = MagicMock()
        mock_git_mgr.pull_latest = MagicMock(return_value=None)

        await orch._execute_single_task(
            task=task,
            project=project,
            project_path="/tmp/proj_budget",
            context_mgr=mock_context_mgr,
            rules_engine=mock_rules_engine,
            dac_tagger=mock_dac_tagger,
            git_mgr=mock_git_mgr,
            on_progress=None,
        )

        # Find the DB write that marks task as failed/BUDGET_EXCEEDED
        write_calls = orch.db.request_write.call_args_list
        budget_write = None
        for call in write_calls:
            args, kwargs = call
            # request_write("update", "tasks", {...})
            if len(args) >= 3 and args[1] == "tasks":
                params = args[2]
                if params.get("status") == "failed" and \
                   params.get("current_step") == "BUDGET_EXCEEDED":
                    budget_write = params
                    break

        self.assertIsNotNone(
            budget_write,
            "Must queue a DB write with status='failed' and "
            "current_step='BUDGET_EXCEEDED' when budget is exceeded"
        )


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-043: Dead API methods must not exist on MasterOrchestrator
# ─────────────────────────────────────────────────────────────────────────────

class TestDeadMethodsRemoved(unittest.TestCase):
    """FER-AF-043: Verify that all four dead API methods (and their helper) are gone."""

    def _make_orchestrator(self):
        from orchestration.master_orchestrator import MasterOrchestrator
        from orchestration.database import ReadOnlyDB

        db = MagicMock(spec=ReadOnlyDB)
        db.set_requester = MagicMock()

        orch = MasterOrchestrator.__new__(MasterOrchestrator)
        orch.db = db
        orch.working_dir = Path("/tmp/test_w6a_factory")
        orch.factory_dir = Path("/tmp/test_w6a_factory")
        orch.router = MagicMock()
        orch.phi3 = None
        orch.config = {}
        return orch

    def test_create_phase_tasks_removed(self):
        """create_phase_tasks() is dead code — must not exist."""
        orch = self._make_orchestrator()
        self.assertFalse(
            hasattr(orch, "create_phase_tasks"),
            "create_phase_tasks() is dead code and must be removed from MasterOrchestrator"
        )

    def test_classify_task_removed(self):
        """
        The PUBLIC classify_task() (dead API wrapper) must not exist.
        Note: the PRIVATE _classify_task() is live and must still exist.
        """
        orch = self._make_orchestrator()
        self.assertFalse(
            hasattr(orch, "classify_task"),
            "classify_task() (public dead wrapper) must be removed from MasterOrchestrator"
        )

    def test_private_classify_task_still_exists(self):
        """
        Regression guard: _classify_task() (private, live) must NOT have been
        accidentally removed alongside the dead public classify_task().
        """
        orch = self._make_orchestrator()
        self.assertTrue(
            hasattr(orch, "_classify_task"),
            "_classify_task() is a live private method and must NOT have been removed"
        )

    def test_send_to_tdd_removed(self):
        """send_to_tdd() is dead code — must not exist."""
        orch = self._make_orchestrator()
        self.assertFalse(
            hasattr(orch, "send_to_tdd"),
            "send_to_tdd() is dead code and must be removed from MasterOrchestrator"
        )

    def test_gatekeeper_review_method_removed(self):
        """
        The PUBLIC gatekeeper_review() METHOD is dead code — must not exist.
        Note: 'gatekeeper_review' as a worker ROLE string is unaffected.
        """
        orch = self._make_orchestrator()
        self.assertFalse(
            hasattr(orch, "gatekeeper_review"),
            "gatekeeper_review() method is dead code and must be removed from MasterOrchestrator"
        )

    def test_save_task_file_helper_removed(self):
        """
        _save_task_file() was only used by the dead classify_task() — must also be gone.
        """
        orch = self._make_orchestrator()
        self.assertFalse(
            hasattr(orch, "_save_task_file"),
            "_save_task_file() was only used by dead classify_task() and must be removed"
        )
