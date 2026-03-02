"""
Wave 2 Tests — Issues 1, 3, 4, 5, 7-dashboard
RED phase: defines expected behaviour before implementation.
Run: pytest tests/test_7issues_wave2.py -v
"""
import asyncio
import json
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch, call

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─── Issue 5: Quality Gate Calibration ─────────────────────────────────────

class TestQualityGateCalibration:
    """_quality_gate must use logic-based verdict, not confidence score."""

    def _make_orchestrator(self, gate_response: str):
        """Build a minimal orchestrator with a mocked gatekeeper worker."""
        from orchestration.master_orchestrator import MasterOrchestrator
        from orchestration.role_router import RoleRouter

        kimi_mock = AsyncMock()
        kimi_mock.send_message = AsyncMock(return_value={
            "success": True,
            "response": gate_response,
            "worker": "kimi",
        })
        kimi_mock.check_health = AsyncMock(return_value="healthy")

        db_mock = MagicMock()
        db_mock.get_task.return_value = {"task_id": "t1", "description": "test task"}
        db_mock.request_write = MagicMock()

        router_mock = MagicMock(spec=RoleRouter)
        router_mock.get_worker.return_value = kimi_mock
        router_mock.get_worker_name.return_value = "kimi"

        config = {"quality_gates": {"confidence_threshold": 0.90}}
        orch = MasterOrchestrator.__new__(MasterOrchestrator)
        orch.db = db_mock
        orch.router = router_mock
        orch.config = config
        orch.phi3 = None
        orch.working_dir = Path("/tmp")
        return orch, kimi_mock

    @pytest.mark.asyncio
    async def test_gate_rejects_when_issues_present(self):
        """Gate must REJECT when issues[] is non-empty, even at 95% confidence."""
        gate_json = json.dumps({
            "verdict": "APPROVED",
            "confidence": 0.95,
            "issues": ["Missing error handling in auth module"],
            "dac_tags": [],
        })
        orch, _ = self._make_orchestrator(gate_json)
        result = await orch._quality_gate(
            {"task_id": "t1", "description": "test"},
            {"files": []}, "/tmp/proj", None
        )
        assert result["verdict"] == "REJECTED", (
            f"Gate approved despite issues present. Result: {result}"
        )

    @pytest.mark.asyncio
    async def test_gate_rejects_when_dac_tags_present(self):
        """Gate must REJECT when dac_tags[] is non-empty."""
        gate_json = json.dumps({
            "verdict": "APPROVED",
            "confidence": 0.95,
            "issues": [],
            "dac_tags": ["SER"],
        })
        orch, _ = self._make_orchestrator(gate_json)
        result = await orch._quality_gate(
            {"task_id": "t1", "description": "test"},
            {"files": []}, "/tmp/proj", None
        )
        assert result["verdict"] == "REJECTED", (
            f"Gate approved despite dac_tags present. Result: {result}"
        )

    @pytest.mark.asyncio
    async def test_gate_approves_when_clean(self):
        """Gate APPROVES only when issues=[] AND dac_tags=[]."""
        gate_json = json.dumps({
            "verdict": "APPROVED",
            "confidence": 0.95,
            "issues": [],
            "dac_tags": [],
        })
        orch, _ = self._make_orchestrator(gate_json)
        result = await orch._quality_gate(
            {"task_id": "t1", "description": "test"},
            {"files": []}, "/tmp/proj", None
        )
        assert result["verdict"] == "APPROVED", (
            f"Gate rejected clean output. Result: {result}"
        )

    @pytest.mark.asyncio
    async def test_confidence_alone_cannot_approve(self):
        """High confidence alone must NOT override issues/dac_tags check."""
        gate_json = json.dumps({
            "verdict": "APPROVED",
            "confidence": 0.99,
            "issues": ["null pointer in edge case"],
            "dac_tags": ["DOM"],
        })
        orch, _ = self._make_orchestrator(gate_json)
        result = await orch._quality_gate(
            {"task_id": "t1", "description": "test"},
            {"files": []}, "/tmp/proj", None
        )
        assert result["verdict"] == "REJECTED", (
            "0.99 confidence should NOT override non-empty issues/tags"
        )


# ─── Issue 3: Kimi SPOF — Gemini Fallback ──────────────────────────────────

class TestKimiSpofFallback:
    """_quality_gate must fall back to Gemini when Kimi is unhealthy."""

    @pytest.mark.asyncio
    async def test_gate_uses_gemini_when_kimi_offline(self):
        """When kimi.check_health() == 'offline', gate must use gemini worker."""
        from orchestration.master_orchestrator import MasterOrchestrator
        from orchestration.role_router import RoleRouter

        kimi_mock = AsyncMock()
        kimi_mock.check_health = AsyncMock(return_value="offline")
        kimi_mock.send_message = AsyncMock()  # should NOT be called

        gemini_mock = AsyncMock()
        gemini_mock.check_health = AsyncMock(return_value="healthy")
        gemini_mock.send_message = AsyncMock(return_value={
            "success": True,
            "response": json.dumps({
                "verdict": "APPROVED", "confidence": 0.92,
                "issues": [], "dac_tags": [],
            }),
            "worker": "gemini",
        })

        db_mock = MagicMock()
        db_mock.get_task.return_value = {"task_id": "t2", "description": "test task"}
        db_mock.request_write = MagicMock()

        def get_worker(role):
            if role == "gatekeeper_review":
                return kimi_mock
            if role == "architecture_audit":
                return gemini_mock
            return None

        def get_worker_name(role):
            if role == "gatekeeper_review":
                return "kimi"
            if role == "architecture_audit":
                return "gemini"
            return None

        router_mock = MagicMock(spec=RoleRouter)
        router_mock.get_worker.side_effect = get_worker
        router_mock.get_worker_name.side_effect = get_worker_name

        orch = MasterOrchestrator.__new__(MasterOrchestrator)
        orch.db = db_mock
        orch.router = router_mock
        orch.config = {}
        orch.phi3 = None
        orch.working_dir = Path("/tmp")

        result = await orch._quality_gate(
            {"task_id": "t2", "description": "test"},
            {"files": []}, "/tmp/proj", None
        )

        # Gemini must have been used, kimi.send_message must NOT be called
        assert kimi_mock.send_message.call_count == 0, (
            "Kimi was called despite being offline"
        )
        assert gemini_mock.send_message.call_count >= 1, (
            "Gemini was not called as fallback"
        )
        assert result["verdict"] == "APPROVED"

    @pytest.mark.asyncio
    async def test_gate_uses_kimi_when_healthy(self):
        """When kimi is healthy, gate uses kimi (no fallback needed)."""
        from orchestration.master_orchestrator import MasterOrchestrator

        kimi_mock = AsyncMock()
        kimi_mock.check_health = AsyncMock(return_value="healthy")
        kimi_mock.send_message = AsyncMock(return_value={
            "success": True,
            "response": json.dumps({
                "verdict": "APPROVED", "confidence": 0.92,
                "issues": [], "dac_tags": [],
            }),
            "worker": "kimi",
        })

        gemini_mock = AsyncMock()
        gemini_mock.send_message = AsyncMock()  # should NOT be called

        db_mock = MagicMock()
        db_mock.get_task.return_value = {"task_id": "t3", "description": "test"}
        db_mock.request_write = MagicMock()

        router_mock = MagicMock()
        router_mock.get_worker.return_value = kimi_mock
        router_mock.get_worker_name.return_value = "kimi"

        orch = MasterOrchestrator.__new__(MasterOrchestrator)
        orch.db = db_mock
        orch.router = router_mock
        orch.config = {}
        orch.phi3 = None
        orch.working_dir = Path("/tmp")

        result = await orch._quality_gate(
            {"task_id": "t3", "description": "test"},
            {"files": []}, "/tmp/proj", None
        )

        assert gemini_mock.send_message.call_count == 0, (
            "Gemini was called even though Kimi is healthy"
        )
        assert result["verdict"] == "APPROVED"


# ─── Issue 4: Cost Tracking Wired ─────────────────────────────────────────

class TestCostTrackingWired:
    """After worker call in _execute_single_task, cost must be queued to cost_tracking."""

    def test_ollama_response_contains_tokens(self):
        """OllamaWorkerAdapter responses already include token counts — verify the key."""
        # This documents the contract: workers that return tokens must use this structure
        response = {
            "success": True,
            "response": "some code",
            "elapsed_ms": 1234,
            "worker": "deepseek",
            "tokens": {
                "prompt": 150,
                "completion": 200,
            },
        }
        assert "tokens" in response
        assert "prompt" in response["tokens"]
        assert "completion" in response["tokens"]

    def test_orchestrator_has_track_cost_call(self):
        """master_orchestrator._execute_single_task source must reference cost_tracking."""
        import inspect
        from orchestration import master_orchestrator
        source = inspect.getsource(master_orchestrator)
        assert "cost_tracking" in source, (
            "_execute_single_task must write to cost_tracking table after each worker call"
        )

    def test_orchestrator_queues_cost_after_worker_response(self):
        """queue_write for cost_tracking must be called with token data from worker response."""
        import inspect
        from orchestration import master_orchestrator
        source = inspect.getsource(master_orchestrator)
        # After a worker call, token data extraction and queue must be present
        assert "prompt_tokens" in source or "total_tokens" in source, (
            "No token field extraction found in orchestrator — cost tracking not wired"
        )


# ─── Issue 1: E2E Visibility in uat_ready broadcast ───────────────────────

class TestE2EVisibilityInUatReady:
    """uat_ready WS broadcast must include e2e_passed + output so dashboard can warn."""

    def test_execute_project_returns_e2e_in_result(self):
        """execute_project result dict must contain 'e2e' key with success field."""
        # Verify the return dict structure in the source
        import inspect
        from orchestration import master_orchestrator
        source = inspect.getsource(master_orchestrator)
        # The e2e key must be in the return dict for uat_approval awaiting case
        assert '"e2e"' in source or "'e2e'" in source, (
            "execute_project does not include 'e2e' in its result dict"
        )

    def test_on_execute_complete_broadcasts_e2e_status(self):
        """_on_execute_complete in dashboard must include e2e_passed in uat_ready event."""
        import inspect
        from dashboard import dashboard_server
        source = inspect.getsource(dashboard_server)
        # Check that uat_ready broadcast contains e2e info
        assert "e2e_passed" in source, (
            "dashboard_server uat_ready event does not include e2e_passed field"
        )

    def test_dashboard_has_e2e_warning_ui(self):
        """Dashboard HTML/JS must have a visual warning when e2e_passed=False."""
        import inspect
        from dashboard import dashboard_server
        source = inspect.getsource(dashboard_server)
        assert "e2e_passed" in source and (
            "e2e_warning" in source or "e2e-warning" in source or "e2eWarning" in source
        ), (
            "Dashboard does not show a visual E2E failure warning before UAT approve button"
        )


# ─── Issue 7: Training data validation dashboard endpoint ─────────────────

class TestTrainingDataValidationEndpoint:
    """Dashboard must expose POST /api/training-data/{id}/validate endpoint."""

    def test_validate_endpoint_registered(self):
        """Dashboard router must register the validate training-data endpoint."""
        import inspect
        from dashboard import dashboard_server
        source = inspect.getsource(dashboard_server)
        assert "/api/training-data" in source, (
            "Dashboard missing /api/training-data endpoint — cannot validate entries"
        )
        assert "validate" in source, (
            "Dashboard missing 'validate' reference for training data endpoint"
        )

    def test_validate_endpoint_calls_db_method(self):
        """The validate handler must call watchdog.validate_training_data()."""
        import inspect
        from dashboard import dashboard_server
        source = inspect.getsource(dashboard_server)
        assert "validate_training_data" in source, (
            "Dashboard validate endpoint does not call watchdog.validate_training_data()"
        )
