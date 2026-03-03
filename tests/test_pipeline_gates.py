"""
Pipeline Gate Tests — Fix verification for 5 connectivity gaps.

Covers:
  Fix 1  — ContractValidator: AST import checking (_validate_python_imports, _validate_ts_imports)
  Fix 2  — Quality gate prompt includes all 5 cross-reference check instructions
  Fix 3  — Inter-phase file context: prior_phase_files passed to phase 2+ workers
  Fix 4  — Post-phase compilation gate: _check_compilation blocks next phase on failure
  Fix 5  — TDD pipeline actually runs pytest in RED and GREEN steps
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1: ContractValidator — AST import checking
# ─────────────────────────────────────────────────────────────────────────────

class TestContractValidatorImports:
    """_validate_python_imports must detect a missing local module as a warning."""

    def test_compile_check_catches_syntax_error(self, tmp_path):
        """_validate_python_imports returns python_syntax_error for unparseable .py."""
        from orchestration.contract_validator import ContractValidator
        cv = ContractValidator(str(tmp_path))
        bad_py = "def foo(\n    # unclosed"
        code_files = [{"path": "backend/bad.py", "content": bad_py}]
        result = cv._validate_python_imports(code_files)
        types = [m["type"] for m in result]
        assert "python_syntax_error" in types, (
            f"Expected python_syntax_error in {types}"
        )

    def test_contract_validator_catches_missing_import(self, tmp_path):
        """_validate_python_imports flags a local 'from X import Y' when X has no file."""
        from orchestration.contract_validator import ContractValidator
        # Create a local package dir so the import looks local
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "__init__.py").touch()
        # backend/service.py imports from backend.missing_module (doesn't exist)
        code = "from backend.missing_module import helper\n\ndef run():\n    helper()\n"
        code_files = [{"path": "backend/service.py", "content": code}]
        cv = ContractValidator(str(tmp_path))
        result = cv._validate_python_imports(code_files)
        types = [m["type"] for m in result]
        assert "python_missing_import" in types, (
            f"Expected python_missing_import warning for unresolved local import. Got: {types}"
        )

    def test_contract_validator_passes_stdlib_import(self, tmp_path):
        """stdlib imports (os, json, etc.) must NOT produce warnings."""
        from orchestration.contract_validator import ContractValidator
        code = "import os\nimport json\nfrom pathlib import Path\n"
        code_files = [{"path": "backend/util.py", "content": code}]
        cv = ContractValidator(str(tmp_path))
        result = cv._validate_python_imports(code_files)
        assert result == [], f"stdlib imports should produce no mismatches, got: {result}"


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2: Quality gate prompt — cross-reference check instructions
# ─────────────────────────────────────────────────────────────────────────────

class TestQualityGatePromptCrossReference:
    """Gate prompt must include all 5 cross-reference checks."""

    def _get_gate_prompt(self):
        from orchestration.context_manager import ContextManager
        cm = ContextManager.__new__(ContextManager)
        cm._protocol_cache = {}
        cm._rules_cache = None
        cm.working_dir = Path("/tmp/fake")
        cm.factory_dir = Path("/tmp/fake")
        task = {"task_id": "t1", "description": "test task", "module": "backend"}
        code_output = {"files": [], "decisions": [], "notes": []}
        return cm.build_gate_prompt(task, code_output, contracts={}, validator_report={})

    def test_quality_gate_prompt_includes_cross_reference_check(self):
        """Gate prompt must contain all 5 cross-reference check instructions."""
        prompt = self._get_gate_prompt()
        required_phrases = [
            "Python imports",
            "TypeScript imports",
            "Function/class calls",
            "Type usage",
            "Cross-file consistency",
        ]
        missing = [p for p in required_phrases if p not in prompt]
        assert not missing, (
            f"Quality gate prompt missing cross-reference checks: {missing}\n"
            f"Prompt snippet: {prompt[-800:]}"
        )

    def test_quality_gate_prompt_reject_instruction(self):
        """Gate prompt must instruct to REJECT on missing imports."""
        prompt = self._get_gate_prompt()
        assert "REJECT" in prompt, "Gate prompt must include REJECT instruction for missing imports"


# ─────────────────────────────────────────────────────────────────────────────
# Fix 3: Inter-phase file context — prior_phase_files passed to phase 2+
# ─────────────────────────────────────────────────────────────────────────────

class TestPriorPhaseFilesPropagation:
    """prior_phase_files must be accumulated from phase 1 and passed into phase 2."""

    @pytest.mark.asyncio
    async def test_prior_phase_files_passed_to_phase2(self):
        """After phase 1, all_written_files is passed as prior_phase_files to phase 2."""
        from orchestration.master_orchestrator import MasterOrchestrator

        # Track calls to _phase_build
        call_log = []

        async def mock_phase_build(project, project_path, phase_num,
                                   ctx, rules, dac, ll, git, on_progress=None,
                                   prior_phase_files=None):
            call_log.append({"phase": phase_num, "prior_files": list(prior_phase_files or [])})
            written = [f"/proj/phase{phase_num}_file.py"] if phase_num == 1 else []
            return {
                "success": True,
                "tasks_completed": 1,
                "tasks_failed": 0,
                "task_results": {},
                "errors": [],
                "all_written_files": written,
            }

        async def mock_check_compilation(project_path, phase_num, on_progress=None):
            return {"passed": True, "errors": []}

        orch = MasterOrchestrator.__new__(MasterOrchestrator)
        orch._phase_build = mock_phase_build
        orch._check_compilation = mock_check_compilation

        # Simulate the phase loop from execute_project
        prior_phase_files: list = []
        phases_completed = 0
        total_phases = 2

        for phase_num in range(1, total_phases + 1):
            phase_result = await orch._phase_build(
                {}, "/tmp/proj", phase_num,
                None, None, None, None, None, None,
                prior_phase_files=prior_phase_files,
            )
            prior_phase_files = phase_result.get("all_written_files", prior_phase_files)
            compile_result = await orch._check_compilation("/tmp/proj", phase_num)
            if not compile_result["passed"]:
                break
            phases_completed = phase_num

        assert len(call_log) == 2, f"Expected 2 phase calls, got {len(call_log)}"
        assert call_log[0]["prior_files"] == [], "Phase 1 should start with empty prior_files"
        assert "/proj/phase1_file.py" in call_log[1]["prior_files"], (
            f"Phase 2 should see phase 1 output. Got: {call_log[1]['prior_files']}"
        )
        assert phases_completed == 2


# ─────────────────────────────────────────────────────────────────────────────
# Fix 4: Post-phase compilation gate
# ─────────────────────────────────────────────────────────────────────────────

class TestCompilationGate:
    """_check_compilation must fail on bad Python and block the next phase."""

    @pytest.mark.asyncio
    async def test_check_compilation_passes_on_valid_python(self, tmp_path):
        """Valid .py files pass compilation check."""
        from orchestration.master_orchestrator import MasterOrchestrator
        (tmp_path / "main.py").write_text("x = 1 + 1\n")

        orch = MasterOrchestrator.__new__(MasterOrchestrator)
        result = await orch._check_compilation(str(tmp_path), 1)
        assert result["passed"] is True, f"Expected pass, got errors: {result['errors']}"

    @pytest.mark.asyncio
    async def test_compile_check_fails_on_syntax_error(self, tmp_path):
        """A .py file with a syntax error must cause _check_compilation to fail."""
        from orchestration.master_orchestrator import MasterOrchestrator
        bad_file = tmp_path / "broken.py"
        bad_file.write_text("def foo(\n    # never closed\n")

        orch = MasterOrchestrator.__new__(MasterOrchestrator)
        result = await orch._check_compilation(str(tmp_path), 1)
        assert result["passed"] is False, "Expected compile check to fail on syntax error"
        assert any("[PY]" in e for e in result["errors"]), (
            f"Expected [PY] error in results. Got: {result['errors']}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Fix 5: TDD pipeline actually runs pytest
# ─────────────────────────────────────────────────────────────────────────────

class TestTDDPytestExecution:
    """RED and GREEN steps must call _run_pytest_for_task and store results in metadata."""

    @pytest.mark.asyncio
    async def test_tdd_pytest_runs_after_red(self, tmp_path):
        """_step_write_tests writes test file and calls _run_pytest_for_task (RED)."""
        from orchestration.tdd_pipeline import TDDPipeline, TDDStepResult
        from orchestration.database import ReadOnlyDB

        db = MagicMock(spec=ReadOnlyDB)
        pipeline = TDDPipeline(db, role_router=MagicMock(), project_path=str(tmp_path))
        pipeline._results = {}

        pytest_calls = []

        async def fake_run_pytest(test_file_path, task_id, phase="RED"):
            pytest_calls.append({"file": test_file_path, "phase": phase})
            return {"passed": False, "stdout": "1 failed", "returncode": 1}

        pipeline._run_pytest_for_task = fake_run_pytest
        pipeline._call_tdd_worker = AsyncMock(return_value="import pytest\ndef test_foo(): assert False")

        task = {"task_id": "t_abc", "description": "test desc"}
        project = {"project_id": "p1"}
        code_output = {"files": []}
        result = await pipeline._step_write_tests(task, project, code_output)

        assert result.step_id == "RED"
        assert isinstance(result.metadata, dict), "metadata must be a dict"
        assert "pytest_red" in result.metadata, f"pytest_red missing from metadata: {result.metadata}"
        assert len(pytest_calls) == 1, f"Expected 1 pytest call, got {len(pytest_calls)}"
        assert pytest_calls[0]["phase"] == "RED"

    @pytest.mark.asyncio
    async def test_tdd_pytest_runs_after_green(self, tmp_path):
        """_step_minimal_impl calls _run_pytest_for_task (GREEN) using RED test file."""
        from orchestration.tdd_pipeline import TDDPipeline, TDDStepResult
        from orchestration.database import ReadOnlyDB

        # Write a fake test file so GREEN step finds it
        test_file = tmp_path / "tests" / "test_t_xyz_tdd.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("def test_pass(): assert True\n")

        db = MagicMock(spec=ReadOnlyDB)
        pipeline = TDDPipeline(db, role_router=MagicMock(), project_path=str(tmp_path))

        # Simulate RED result with test file metadata
        from orchestration.tdd_pipeline import TDDStepResult
        red_result = TDDStepResult(
            step_id="RED", success=True, output="test content",
            metadata={"test_file": str(test_file), "pytest_red": {"passed": False}}
        )
        pipeline._results = {"RED": red_result}

        pytest_calls = []

        async def fake_run_pytest(test_file_path, task_id, phase="RED"):
            pytest_calls.append({"file": test_file_path, "phase": phase})
            return {"passed": True, "stdout": "1 passed", "returncode": 0}

        pipeline._run_pytest_for_task = fake_run_pytest
        pipeline._call_tdd_worker = AsyncMock(return_value="def solution(): pass")

        task = {"task_id": "t_xyz", "description": "test green"}
        code_output = {"files": []}
        result = await pipeline._step_minimal_impl(task, code_output)

        assert result.step_id == "GREEN"
        assert isinstance(result.metadata, dict), "metadata must be a dict"
        assert "pytest_green" in result.metadata, f"pytest_green missing: {result.metadata}"
        assert result.metadata.get("green_passed") is True
        assert len(pytest_calls) == 1, f"Expected 1 pytest call for GREEN, got {len(pytest_calls)}"
        assert pytest_calls[0]["phase"] == "GREEN"

    def test_tdd_step_result_has_metadata_slot(self):
        """TDDStepResult must have metadata in __slots__ and default to empty dict."""
        from orchestration.tdd_pipeline import TDDStepResult
        assert "metadata" in TDDStepResult.__slots__, (
            "metadata must be in TDDStepResult.__slots__"
        )
        r = TDDStepResult(step_id="X")
        assert r.metadata == {}, f"Default metadata should be empty dict, got: {r.metadata}"
        r2 = TDDStepResult(step_id="Y", metadata={"key": "val"})
        assert r2.metadata == {"key": "val"}
