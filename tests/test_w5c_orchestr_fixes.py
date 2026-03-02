"""
Wave 5c Tests — FER-AF-044, FER-AF-013, FER-AF-012

FER-AF-044: Quality gate approves when LLM returns unstructured text
  - If gate LLM response has no parseable JSON, gate={}, verdict silently becomes
    APPROVED. Fix: if gate=={} (parse failed / empty), verdict must be REJECTED.

FER-AF-013: LLM JSON parse failure uses raw response as task description
  - Verify _fallback_tasks() always returns tasks with non-empty descriptions.

FER-AF-012: TRAP scope violation does NOT abort task
  - After parse_and_apply() retry, if TRAP violations still remain, the task
    must be aborted (success=False) rather than continuing to commit files.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-044: Quality gate must REJECT when LLM returns no parseable JSON
# ─────────────────────────────────────────────────────────────────────────────

class TestQualityGateParseFail(unittest.IsolatedAsyncioTestCase):
    """FER-AF-044: gate=={} must produce REJECTED, not silent APPROVED."""

    def _make_orchestrator(self):
        """Build a minimal MasterOrchestrator with all external deps mocked."""
        from orchestration.master_orchestrator import MasterOrchestrator
        from orchestration.database import ReadOnlyDB

        db = MagicMock(spec=ReadOnlyDB)
        db.set_requester = MagicMock()
        db.get_chat_history = MagicMock(return_value=[])

        orch = MasterOrchestrator.__new__(MasterOrchestrator)
        orch.db = db
        orch.working_dir = Path("/tmp/test_factory")
        orch.factory_dir = Path("/tmp/test_factory")
        orch.router = MagicMock()
        orch.phi3 = None
        return orch

    def _patch_gate_worker(self, orch, gate_response: str):
        """
        Make _get_worker() and _get_worker_name() return mocked objects that
        simulate a gate LLM response.
        """
        mock_worker = AsyncMock()
        mock_worker.check_health = AsyncMock(return_value="online")
        mock_worker.send_message = AsyncMock(return_value={
            "success": True,
            "response": gate_response,
        })

        mock_context_mgr = MagicMock()
        mock_context_mgr.load_contracts = MagicMock(return_value={})
        mock_context_mgr.build_gate_prompt = MagicMock(return_value="gate prompt")

        orch._get_worker = MagicMock(return_value=mock_worker)
        orch._get_worker_name = MagicMock(return_value="kimi-mock")

        return mock_context_mgr

    async def test_quality_gate_rejects_on_empty_llm_response(self):
        """
        FER-AF-044 Test 1: Gate LLM returns plain text with no JSON object.
        verdict must be REJECTED and issues must mention 'no parseable JSON'.
        """
        orch = self._make_orchestrator()
        ctx = self._patch_gate_worker(orch, "I cannot review this code.")

        with patch("orchestration.master_orchestrator.ContextManager", return_value=ctx):
            result = await orch._quality_gate(
                task={"task_id": "t1", "module": "backend/db.py", "description": "desc"},
                code_output={"files": []},
                project_path="/tmp/proj",
            )

        self.assertEqual(result["verdict"], "REJECTED",
                         "Must REJECT when LLM returns no JSON")
        issues_text = " ".join(result.get("issues", [])).lower()
        self.assertIn("no parseable json", issues_text,
                      f"Issue message must mention 'no parseable JSON', got: {result.get('issues')}")

    async def test_quality_gate_rejects_on_json_parse_failure(self):
        """
        FER-AF-044 Test 2: Gate LLM returns a malformed JSON-like string.
        verdict must be REJECTED.
        """
        orch = self._make_orchestrator()
        ctx = self._patch_gate_worker(orch, "{ invalid json }")

        with patch("orchestration.master_orchestrator.ContextManager", return_value=ctx):
            result = await orch._quality_gate(
                task={"task_id": "t2", "module": "backend/db.py", "description": "desc"},
                code_output={"files": []},
                project_path="/tmp/proj",
            )

        self.assertEqual(result["verdict"], "REJECTED",
                         "Must REJECT when LLM returns malformed JSON")

    async def test_quality_gate_approves_on_valid_response(self):
        """
        FER-AF-044 Test 3 (control): Gate LLM returns valid APPROVED JSON.
        verdict must be APPROVED.
        """
        orch = self._make_orchestrator()
        valid_json = (
            '{"verdict": "APPROVED", "issues": [], "dac_tags": [], "confidence": 0.9}'
        )
        ctx = self._patch_gate_worker(orch, valid_json)

        with patch("orchestration.master_orchestrator.ContextManager", return_value=ctx):
            result = await orch._quality_gate(
                task={"task_id": "t3", "module": "backend/db.py", "description": "desc"},
                code_output={"files": []},
                project_path="/tmp/proj",
            )

        self.assertEqual(result["verdict"], "APPROVED",
                         "Must APPROVE when LLM returns valid APPROVED JSON")


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-012: TRAP scope violation must abort task (not retry-and-continue)
# ─────────────────────────────────────────────────────────────────────────────

class TestExecuteTaskAbortOnTrap(unittest.IsolatedAsyncioTestCase):
    """
    FER-AF-012: After parse_and_apply retry, if TRAP violations still exist,
    the task must fail with success=False and an error mentioning 'TRAP'.
    """

    async def test_execute_task_aborts_on_trap_violation(self):
        """
        If parse_and_apply returns TRAP violations and the retry ALSO returns
        TRAP violations, _execute_single_task must return success=False with
        an error message containing 'TRAP'.
        """
        from orchestration.master_orchestrator import MasterOrchestrator
        from orchestration.database import ReadOnlyDB

        db = MagicMock(spec=ReadOnlyDB)
        db.set_requester = MagicMock()
        db.get_chat_history = MagicMock(return_value=[])
        db.request_write = MagicMock()

        trap_violation = {
            "rule_id": "R010",
            "violation_tag": "TRAP",
            "tag_type": "TRAP",
            "detail": "out_of_scope_write: 'other_module/hack.py' (allowed prefix: 'backend/')",
            "path": "other_module/hack.py",
            "allowed_prefix": "backend/",
        }

        # parse_and_apply always returns TRAP violations (first call AND retry)
        trap_summary = {
            "files_written": [],
            "decisions_logged": [],
            "escalations": [],
            "notes": [],
            "tests_needed": [],
            "scope_violations": [trap_violation],
        }

        mock_worker = AsyncMock()
        mock_worker.send_message = AsyncMock(return_value={
            "success": True,
            "response": '{"files": [], "decisions": []}',
        })

        orch = MasterOrchestrator.__new__(MasterOrchestrator)
        orch.db = db
        orch.working_dir = Path("/tmp/test_factory")
        orch.factory_dir = Path("/tmp/test_factory")
        orch.router = MagicMock()
        orch.phi3 = None

        orch._get_worker = MagicMock(return_value=mock_worker)
        orch._get_worker_name = MagicMock(return_value="deepseek-mock")
        orch._classify_task = AsyncMock(return_value="low")
        orch._quality_gate = AsyncMock(return_value={
            "verdict": "APPROVED", "issues": [], "dac_tags": [], "confidence": 0.9,
            "by": "kimi"
        })

        task = {
            "task_id": "proj1_p1_t01",
            "project_id": "proj1",
            "module": "backend/models.py",
            "description": "Create backend models",
            "phase": 1,
        }
        project = {
            "project_id": "proj1",
            "name": "Test Project",
            "project_path": "/tmp/proj1",
            "project_type": "web",
        }

        mock_context_mgr = MagicMock()
        mock_context_mgr.build_task_prompt = MagicMock(return_value="task prompt")

        mock_rules_engine = MagicMock()
        mock_rules_engine.check_automated_rules = MagicMock()

        mock_dac_tagger = MagicMock()
        mock_dac_tagger.tag = MagicMock()

        mock_git_mgr = MagicMock()
        mock_git_mgr.pull_latest = MagicMock(return_value=None)

        with patch("orchestration.master_orchestrator.OutputParser") as MockParser, \
             patch("orchestration.master_orchestrator.ContractValidator") as MockValidator:

            mock_parser_instance = MagicMock()
            mock_parser_instance.parse_and_apply = MagicMock(
                return_value=(trap_summary, [trap_violation])
            )
            mock_parser_instance._get_allowed_prefix = MagicMock(return_value="backend/")
            MockParser.return_value = mock_parser_instance

            mock_validator_instance = MagicMock()
            mock_validator_instance.load_contracts = MagicMock(return_value=False)
            MockValidator.return_value = mock_validator_instance

            result = await orch._execute_single_task(
                task=task,
                project=project,
                project_path="/tmp/proj1",
                context_mgr=mock_context_mgr,
                rules_engine=mock_rules_engine,
                dac_tagger=mock_dac_tagger,
                git_mgr=mock_git_mgr,
                on_progress=None,
            )

        self.assertFalse(result.get("success"),
                         f"Task must fail on persistent TRAP violation, got: {result}")
        error_msg = result.get("error", "").upper()
        self.assertIn("TRAP", error_msg,
                      f"Error must mention TRAP, got: {result.get('error')}")


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-013: _fallback_tasks must return tasks with non-empty descriptions
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackTasksDescriptions(unittest.TestCase):
    """
    FER-AF-013: _fallback_tasks() must return non-empty description strings
    for every task in every phase.
    """

    def _make_orchestrator(self):
        from orchestration.master_orchestrator import MasterOrchestrator
        from orchestration.database import ReadOnlyDB

        db = MagicMock(spec=ReadOnlyDB)
        db.set_requester = MagicMock()

        orch = MasterOrchestrator.__new__(MasterOrchestrator)
        orch.db = db
        orch.working_dir = Path("/tmp/test_factory")
        orch.factory_dir = Path("/tmp/test_factory")
        orch.router = MagicMock()
        orch.phi3 = None
        return orch

    def test_fallback_tasks_have_non_empty_descriptions(self):
        """
        FER-AF-013: Every task returned by _fallback_tasks() must have a
        non-empty 'description' field (not None, not '', not whitespace-only).
        """
        orch = self._make_orchestrator()
        project = {
            "project_id": "test_fallback",
            "name": "Test Project",
            "project_type": "web",
        }

        result = orch._fallback_tasks(project)

        self.assertIn("phases", result, "_fallback_tasks must return dict with 'phases' key")
        phases = result["phases"]
        self.assertTrue(len(phases) > 0, "Must have at least one phase")

        for phase in phases:
            tasks = phase.get("tasks", [])
            self.assertTrue(len(tasks) > 0,
                            f"Phase {phase.get('phase')} must have at least one task")
            for task in tasks:
                desc = task.get("description", "")
                self.assertTrue(
                    desc and str(desc).strip(),
                    f"Task in phase {phase.get('phase')} has empty/missing description: {task}"
                )
                module = task.get("module", "")
                self.assertTrue(
                    module and str(module).strip(),
                    f"Task in phase {phase.get('phase')} has empty/missing module: {task}"
                )


if __name__ == "__main__":
    unittest.main()
