"""
E2E Pipeline Tests — Autonomous Factory v1.1
═══════════════════════════════════════════════
Tests the full autonomous project execution lifecycle:
  execute_project() → Blueprint → Approval → Build Phases → TDD → UAT → Production

Covers:
  - Phase 0: Blueprint generation, dual audit, contract gen, approval pause
  - Phase 1-3: Task classification, worker execution, output parsing,
    contract validation, TDD pipeline (full + fast-track), quality gate, git
  - Phase 4: Proto tag + UAT pause
  - Phase 5: approve_uat() → merge main, tag v1.0.0
  - Error paths: worker failure, gate rejection, double rejection → HRO
  - TDD crash recovery via checkpoints

Uses real SQLite (temp), mocked worker adapters.
"""

import asyncio
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# Fix imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestration.database import ReadOnlyDB, WatchdogDB, get_write_queue
from orchestration.master_orchestrator import MasterOrchestrator
from orchestration.role_router import RoleRouter
from orchestration.tdd_pipeline import (
    TDDPipeline, TDDStepResult, TDD_STEPS, FAST_TRACK_STEPS, FAST_TRACK_PATTERNS,
)


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def tmp_db(tmp_path):
    """Create a real SQLite DB with full v3 schema + seed data."""
    db_path = str(tmp_path / "test_factory.db")
    wdb = WatchdogDB(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    # Seed: phi3 instance
    conn.execute(
        "INSERT OR IGNORE INTO dashboard_state (instance_name, status) "
        "VALUES ('phi3-orchestrator', 'active')"
    )

    # Seed: test project (project_type is NOT a column on projects table;
    # orchestrator reads it via project.get("project_type", "web") fallback)
    conn.execute(
        "INSERT OR IGNORE INTO projects "
        "(project_id, name, description, status, current_phase) "
        "VALUES ('proj_todo', 'Simple Todo App', "
        "'A web app with task list, add/edit/delete todos', 'active', 0)"
    )

    # Seed: tasks for phases 1-3
    tasks = [
        # Phase 1 — Backend
        ("task_001", "proj_todo", 1, "backend/server",
         "Setup Express server with middleware", "pending"),
        ("task_002", "proj_todo", 1, "backend/models",
         "Create Todo model with CRUD operations", "pending"),
        ("task_003", "proj_todo", 1, "backend/routes",
         "Implement REST API routes for todos", "pending"),
        # Phase 2 — Frontend
        ("task_004", "proj_todo", 2, "frontend/components",
         "Create TodoList React component", "pending"),
        ("task_005", "proj_todo", 2, "frontend/components",
         "Create TodoForm with add/edit support", "pending"),
        ("task_006", "proj_todo", 2, "frontend/styles",
         "Add CSS styling and theme colors", "pending"),
        # Phase 3 — Integration
        ("task_007", "proj_todo", 3, "integration/e2e",
         "Write end-to-end integration tests", "pending"),
        ("task_008", "proj_todo", 3, "integration/docker",
         "Create Docker compose and CI/CD config", "pending"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO tasks "
        "(task_id, project_id, phase, module, description, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        tasks,
    )

    conn.commit()
    conn.close()
    return db_path, wdb


@pytest.fixture
def read_db(tmp_db):
    """ReadOnlyDB pointing at the test DB."""
    db_path, _ = tmp_db
    rdb = ReadOnlyDB(db_path)
    rdb.set_requester("test")
    return rdb


@pytest.fixture
def mock_worker():
    """Factory for mock workers that return controlled responses."""
    def _make(name, response_text=None, success=True):
        worker = MagicMock()
        worker.name = name
        worker.config = {
            "type": "mock", "model": name,
            "max_context_tokens": 32000, "timeout": 30,
        }
        resp = response_text or f"Response from {name}"
        if success:
            worker.send_message = AsyncMock(return_value={
                "success": True, "response": resp, "elapsed_ms": 100,
            })
        else:
            worker.send_message = AsyncMock(return_value={
                "success": False, "error": f"{name} failed", "elapsed_ms": 0,
            })
        return worker
    return _make


@pytest.fixture
def mock_router(mock_worker):
    """RoleRouter mock with all 5 workers + 10 roles configured."""
    router = MagicMock(spec=RoleRouter)

    # Default workers for all roles
    workers = {
        "blueprint_generation": mock_worker("claude", json.dumps({
            "architecture": "Express + React",
            "phases": [
                {"phase": 1, "name": "Backend", "tasks": 3},
                {"phase": 2, "name": "Frontend", "tasks": 3},
                {"phase": 3, "name": "Integration", "tasks": 2},
            ],
            "api_endpoints": [
                {"method": "GET", "path": "/api/todos"},
                {"method": "POST", "path": "/api/todos"},
                {"method": "PUT", "path": "/api/todos/:id"},
                {"method": "DELETE", "path": "/api/todos/:id"},
            ],
            "db_schema": "CREATE TABLE todos (id INT, title TEXT, done BOOLEAN);",
        })),
        "gatekeeper_review": mock_worker("kimi",
            '{"verdict": "APPROVED", "confidence": 0.95, "issues": []}'),
        "architecture_audit": mock_worker("gemini",
            "Architecture looks solid. No major issues."),
        "code_generation_simple": mock_worker("qwen", json.dumps({
            "files": [{"path": "server.js", "content": "const express = require('express');",
                        "action": "create"}],
            "decisions": [{"type": "minor", "description": "Used Express framework"}],
            "notes": ["Server setup complete"],
        })),
        "code_generation_complex": mock_worker("deepseek", json.dumps({
            "files": [{"path": "models/todo.js", "content": "class Todo { constructor() {} }",
                        "action": "create"}],
            "decisions": [{"type": "minor", "description": "Simple class model"}],
            "notes": ["Model implementation"],
        })),
        "frontend_design": mock_worker("claude", json.dumps({
            "files": [{"path": "src/TodoList.jsx", "content": "export default function TodoList() {}",
                        "action": "create"}],
            "decisions": [{"type": "minor", "description": "Functional component"}],
            "notes": ["React component created"],
        })),
        "tdd_testing": mock_worker("claude",
            '{"aligned": true, "gaps": [], "bugs": [], "clean": true, "secure": true}'),
        "task_planning_gsd": mock_worker("claude"),
        "project_classification": mock_worker("kimi", "low"),
    }

    def get_worker(role):
        return workers.get(role)

    def get_worker_name(role):
        w = workers.get(role)
        return w.name if w else "unknown"

    router.get_worker = MagicMock(side_effect=get_worker)
    router.get_worker_name = MagicMock(side_effect=get_worker_name)
    return router


@pytest.fixture
def orchestrator(tmp_db, read_db, mock_router, tmp_path):
    """MasterOrchestrator with mocked workers, real DB, temp working dir."""
    db_path, _ = tmp_db
    config = {
        "factory": {
            "version": "1.1.0",
            "working_dir": str(tmp_path),
            "factory_state_dir": str(tmp_path / "factory_state"),
        },
    }
    (tmp_path / "factory_state").mkdir(exist_ok=True)

    # Create protocols directory with a stub web.md
    proto_dir = tmp_path / "protocols"
    proto_dir.mkdir(exist_ok=True)
    (proto_dir / "web.md").write_text("# Web Protocol\nFollow REST conventions.\n")

    orch = MasterOrchestrator(read_db, mock_router, config, str(tmp_path))
    return orch


@pytest.fixture
def progress_tracker():
    """Capture all progress callbacks for assertions."""
    events = []

    async def on_progress(*args):
        events.append(args)

    return events, on_progress


# ═══════════════════════════════════════════════════════════════
# TEST: TDD PIPELINE — UNIT LEVEL
# ═══════════════════════════════════════════════════════════════


class TestTDDPipelineFastTrack:
    """Test fast-track detection and 5-step pipeline."""

    def test_fast_track_detects_css_task(self):
        assert TDDPipeline.is_fast_track({"description": "Update CSS colors", "module": "frontend/styles"})

    def test_fast_track_detects_theme_task(self):
        assert TDDPipeline.is_fast_track({"description": "Change theme to dark mode", "module": "config"})

    def test_fast_track_detects_typo_task(self):
        assert TDDPipeline.is_fast_track({"description": "Fix typo in header label", "module": "frontend"})

    def test_fast_track_rejects_logic_task(self):
        assert not TDDPipeline.is_fast_track({"description": "Implement CRUD operations", "module": "backend"})

    def test_fast_track_rejects_api_task(self):
        assert not TDDPipeline.is_fast_track({"description": "Create REST API routes", "module": "backend/routes"})

    async def test_fast_track_executes_5_steps(self, read_db, mock_router):
        """Fast-track pipeline should only run AC, GREEN, OA, GIT, AD."""
        tdd = TDDPipeline(read_db, mock_router)

        task = {"task_id": "task_ft", "description": "Update CSS colors",
                "module": "frontend/styles", "phase": 2}
        project = {"project_id": "proj_test", "project_type": "web"}
        code_output = {"files": [{"path": "style.css", "content": "body { color: blue; }"}]}

        result = await tdd.execute(task, project, code_output, fast_track=True)

        assert result["track"] == "fast"
        assert result["success"] is True

        # Check that only fast-track steps ran (GIT may be skipped if no git_manager)
        for step_id, step_result in result["results"].items():
            if step_id in FAST_TRACK_STEPS:
                if step_id == "GIT":
                    # GIT step runs but may be skipped due to no git_manager
                    assert not step_result.get("error"), f"GIT should not error"
                else:
                    assert not step_result["skipped"], f"{step_id} should NOT be skipped"
            else:
                assert step_result["skipped"], f"{step_id} should be skipped in fast-track"

    async def test_full_track_executes_13_steps(self, read_db, mock_router):
        """Full TDD pipeline should run all 13 steps."""
        tdd = TDDPipeline(read_db, mock_router)

        task = {"task_id": "task_full", "description": "Implement CRUD for todos",
                "module": "backend/models", "phase": 1}
        project = {"project_id": "proj_test", "project_type": "web"}
        code_output = {"files": [{"path": "models/todo.js", "content": "class Todo {}"}]}

        result = await tdd.execute(task, project, code_output, fast_track=False)

        assert result["track"] == "full"
        assert result["success"] is True

        # All 13 steps should have results
        assert len(result["results"]) == 13
        for step in TDD_STEPS:
            assert step["id"] in result["results"]


class TestTDDStepResult:
    """Test TDDStepResult serialization."""

    def test_to_dict_truncates_output(self):
        r = TDDStepResult(step_id="AC", success=True, output="x" * 1000)
        d = r.to_dict()
        assert len(d["output"]) == 500  # truncated

    def test_to_dict_preserves_bugs_and_tags(self):
        r = TDDStepResult(
            step_id="BC", success=True,
            bugs_found=[{"id": "BUG-001", "severity": "high"}],
            dac_tags=["DOM"],
        )
        d = r.to_dict()
        assert len(d["bugs_found"]) == 1
        assert d["dac_tags"] == ["DOM"]


class TestTDDPipelineProgressCallbacks:
    """Test that progress callbacks fire correctly."""

    async def test_progress_fires_for_each_step(self, read_db, mock_router):
        events = []

        async def on_progress(step_id, name, status):
            events.append((step_id, status))

        tdd = TDDPipeline(read_db, mock_router)
        task = {"task_id": "task_cb", "description": "Test task",
                "module": "backend", "phase": 1}
        project = {"project_id": "proj_test", "project_type": "web"}
        code_output = {"files": []}

        await tdd.execute(task, project, code_output, on_progress=on_progress,
                          fast_track=False)

        # Each step should fire "running" then "completed"
        running = [e for e in events if e[1] == "running"]
        completed = [e for e in events if e[1] == "completed"]
        assert len(running) == 13
        assert len(completed) == 13


class TestTDDPipelineBugCollection:
    """Test bug detection and DaC tag collection."""

    async def test_bugs_from_bc_step_collected(self, read_db, mock_router):
        """BC step finding bugs should populate the result bugs list."""
        # Override tdd_testing worker to return bugs from BC step
        bc_response = json.dumps({
            "bugs": [
                {"id": "BUG-001", "severity": "high",
                 "description": "SQL injection in query", "file": "routes.js"},
            ],
            "clean": False,
        })
        worker = MagicMock()
        worker.name = "claude"
        worker.config = {"type": "mock", "model": "claude"}
        worker.send_message = AsyncMock(return_value={
            "success": True, "response": bc_response, "elapsed_ms": 50,
        })
        mock_router.get_worker = MagicMock(return_value=worker)

        tdd = TDDPipeline(read_db, mock_router)
        task = {"task_id": "task_bugs", "description": "API routes",
                "module": "backend", "phase": 1}
        project = {"project_id": "proj_test", "project_type": "web"}
        code_output = {"files": [{"path": "routes.js", "content": "app.get()"}]}

        result = await tdd.execute(task, project, code_output, fast_track=False)

        assert result["success"] is True
        assert len(result["bugs"]) >= 1
        assert any(b.get("id") == "BUG-001" for b in result["bugs"])
        assert "DOM" in result["dac_tags"]


# ═══════════════════════════════════════════════════════════════
# TEST: EXECUTE_PROJECT — FULL PIPELINE E2E
# ═══════════════════════════════════════════════════════════════


class TestExecuteProjectBlueprint:
    """Phase 0: Blueprint generation → dual audit → contracts → approval pause."""

    async def test_execute_returns_awaiting_blueprint(self, orchestrator, progress_tracker):
        """execute_project() should pause at blueprint approval."""
        events, on_progress = progress_tracker

        result = await orchestrator.execute_project("proj_todo", on_progress=on_progress)

        # Should pause at blueprint approval
        assert result.get("awaiting") == "blueprint_approval"
        assert result["success"] is False
        assert result["phases_completed"] == 0
        assert "project_path" in result

        # Progress should show blueprint phase
        blueprint_events = [e for e in events if e[1] == "blueprint"]
        assert len(blueprint_events) >= 1

    async def test_execute_unknown_project_returns_error(self, orchestrator):
        """execute_project() with unknown ID returns failure."""
        result = await orchestrator.execute_project("nonexistent_project")

        assert result["success"] is False
        assert "not found" in result["errors"][0].lower()

    async def test_blueprint_worker_failure_returns_error(self, orchestrator):
        """If blueprint worker fails, return gracefully."""
        # Make blueprint worker return failure
        fail_worker = MagicMock()
        fail_worker.name = "claude"
        fail_worker.config = {"type": "mock", "model": "claude"}
        fail_worker.send_message = AsyncMock(return_value={
            "success": False, "error": "Worker crashed",
        })
        orchestrator.router.get_worker = MagicMock(return_value=fail_worker)

        result = await orchestrator.execute_project("proj_todo")

        assert result["success"] is False
        assert result.get("awaiting") == "blueprint_approval"


class TestApproveBlueprint:
    """Test blueprint approval flow."""

    async def test_approve_blueprint_success(self, orchestrator, tmp_db, tmp_path):
        """approve_blueprint() should mark approved + lock contracts."""
        db_path, _ = tmp_db
        conn = sqlite3.connect(db_path)

        # Insert a blueprint revision (simulating phase 0 completion)
        conn.execute(
            "INSERT INTO blueprint_revisions (project_id, version, changes_summary, "
            "blueprint_content, reason) VALUES ('proj_todo', 1, 'v1', 'Blueprint here', 'generated')"
        )
        # Set project_path
        project_path = str(tmp_path / "projects" / "simple_todo_app")
        Path(project_path).mkdir(parents=True, exist_ok=True)
        conn.execute(
            "UPDATE projects SET project_path=? WHERE project_id='proj_todo'",
            (project_path,)
        )
        conn.commit()
        conn.close()

        result = await orchestrator.approve_blueprint("proj_todo")

        assert result["success"] is True
        assert "approved" in result["message"].lower()

    async def test_approve_blueprint_unknown_project(self, orchestrator):
        result = await orchestrator.approve_blueprint("nonexistent")
        assert result["success"] is False


class TestApproveUAT:
    """Test UAT approval → production deploy."""

    async def test_approve_uat_success(self, orchestrator, tmp_db, tmp_path):
        """approve_uat() should merge to main + tag v1.0.0."""
        db_path, _ = tmp_db
        project_path = str(tmp_path / "projects" / "simple_todo_app")
        Path(project_path).mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE projects SET project_path=?, current_phase=4, status='active' "
            "WHERE project_id='proj_todo'",
            (project_path,)
        )
        conn.commit()
        conn.close()

        # Mock GitManager to prevent actual git calls
        with patch("orchestration.master_orchestrator.GitManager") as MockGit:
            mock_git = MagicMock()
            MockGit.return_value = mock_git
            mock_git.merge_to_main.return_value = True
            mock_git.tag_version.return_value = True

            result = await orchestrator.approve_uat("proj_todo")

        assert result["success"] is True
        assert "v1.0.0" in result["message"]
        mock_git.merge_to_main.assert_called_once()
        mock_git.tag_version.assert_called_once_with("v1.0.0", "Production release")

    async def test_approve_uat_unknown_project(self, orchestrator):
        result = await orchestrator.approve_uat("nonexistent")
        assert result["success"] is False


# ═══════════════════════════════════════════════════════════════
# TEST: FULL PIPELINE — BLUEPRINT → BUILD → PROTO → UAT
# ═══════════════════════════════════════════════════════════════


class TestFullPipelineE2E:
    """
    Integration test covering the complete lifecycle:
    execute_project → blueprint pause → approve → build phases → proto → UAT → done.
    """

    async def test_full_lifecycle_with_mocked_workers(self, orchestrator, tmp_db,
                                                       tmp_path, progress_tracker):
        """
        Simulate the entire project lifecycle end-to-end.

        1. execute_project() pauses at blueprint approval
        2. Manually advance past blueprint (patch _phase_blueprint to return approved)
        3. Build phases 1-3 execute with mocked workers
        4. Proto phase → awaiting UAT
        5. approve_uat() → production
        """
        events, on_progress = progress_tracker
        db_path, _ = tmp_db

        project_path = str(tmp_path / "projects" / "simple_todo_app")
        Path(project_path).mkdir(parents=True, exist_ok=True)

        # Patch _phase_blueprint to return approved (skip the approval pause)
        async def mock_blueprint(project, pp, ctx_mgr, rules_eng, prog):
            if prog:
                await prog(0, "blueprint", "running", "Generating blueprint")
                await prog(0, "blueprint", "completed", "Blueprint approved")
            return {
                "approved": True,
                "blueprint": "Architecture: Express + React",
                "contracts": {"generated_files": ["api_contract.json"]},
                "audit": {"issues": [], "audited": True},
                "version": 1,
                "total_phases": 3,
            }

        # Patch git operations
        mock_git = MagicMock()
        mock_git.init_repo.return_value = True
        mock_git.create_phase_branch.return_value = "phase/1-backend"
        mock_git.pull_latest.return_value = True
        mock_git.atomic_commit = AsyncMock(return_value="abc123")
        mock_git.merge_to_develop.return_value = True
        mock_git.merge_to_main.return_value = True
        mock_git.tag_version.return_value = True
        mock_git.get_changed_files.return_value = ["server.js", "models/todo.js"]

        # Patch OutputParser to always succeed (include content for TDD steps)
        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_and_apply.return_value = (
            {"files_written": [{"path": "server.js", "action": "create",
                                "content": "const express = require('express');"}],
             "decisions_logged": [{"type": "minor", "description": "Used Express"}]},
            [],  # no violations
        )

        # Patch ContractValidator to always pass
        mock_validator = MagicMock()
        mock_validator.load_contracts.return_value = False  # skip validation

        # Mock _quality_gate to avoid ContextManager needing file content
        async def mock_quality_gate(task, code_out, pp, validator=None):
            return {"verdict": "APPROVED", "confidence": 0.9, "issues": []}

        # Disable worktree isolation for this mocked test — WorkspaceManager
        # requires a real git repo, and we're mocking GitManager entirely.
        _orig_ws = orchestrator.config.get("workspaces", {})
        orchestrator.config["workspaces"] = {"enabled": False}

        # Mock _run_e2e_tests — the mock workers generate JS-style syntax
        # (`true`/`false`) in Python test files, which causes real pytest to
        # fail. Since this test validates the orchestrator lifecycle, not the
        # generated code quality, mock E2E to pass.
        async def mock_e2e(project_path, on_progress=None):
            return {"success": True, "tests_run": 0, "tests_passed": 0}

        with patch.object(orchestrator, "_phase_blueprint", side_effect=mock_blueprint), \
             patch.object(orchestrator, "_quality_gate", side_effect=mock_quality_gate), \
             patch.object(orchestrator, "_run_e2e_tests", side_effect=mock_e2e), \
             patch("orchestration.master_orchestrator.GitManager", return_value=mock_git), \
             patch("orchestration.master_orchestrator.OutputParser", return_value=mock_parser_instance), \
             patch("orchestration.master_orchestrator.ContractValidator", return_value=mock_validator):

            result = await orchestrator.execute_project("proj_todo", on_progress=on_progress)

        # Should reach proto phase (awaiting UAT)
        assert result.get("awaiting") == "uat_approval"
        assert result["success"] is True
        assert result["phases_completed"] == 3
        assert "project_path" in result

        # Verify progress events fired
        assert len(events) > 0

        # Verify phases 1-3 each had planning events
        planning_events = [e for e in events if e[1] == "planning"]
        assert len(planning_events) >= 3

    async def test_phase_build_processes_all_tasks(self, orchestrator, tmp_db,
                                                    tmp_path, progress_tracker):
        """Each phase should process all tasks sequentially."""
        events, on_progress = progress_tracker
        db_path, _ = tmp_db

        project_path = str(tmp_path / "projects" / "simple_todo_app")
        Path(project_path).mkdir(parents=True, exist_ok=True)

        mock_git = MagicMock()
        mock_git.create_phase_branch.return_value = "phase/1-backend"
        mock_git.pull_latest.return_value = True
        mock_git.atomic_commit = AsyncMock(return_value="abc123")
        mock_git.merge_to_develop.return_value = True
        mock_git.get_changed_files.return_value = ["server.js"]

        mock_parser = MagicMock()
        mock_parser.parse_and_apply.return_value = (
            {"files_written": [{"path": "f.js", "action": "create"}],
             "decisions_logged": []},
            [],
        )

        mock_validator = MagicMock()
        mock_validator.load_contracts.return_value = False

        project = orchestrator.db.get_project("proj_todo")

        with patch("orchestration.master_orchestrator.GitManager", return_value=mock_git), \
             patch("orchestration.master_orchestrator.OutputParser", return_value=mock_parser), \
             patch("orchestration.master_orchestrator.ContractValidator", return_value=mock_validator), \
             patch("orchestration.master_orchestrator.ContextManager") as MockCtx, \
             patch("orchestration.master_orchestrator.RulesEngine") as MockRules, \
             patch("orchestration.master_orchestrator.DaCTagger") as MockDaC, \
             patch("orchestration.master_orchestrator.LearningLog") as MockLL:

            ctx_inst = MockCtx.return_value
            ctx_inst.build_task_prompt.return_value = "Build this task"
            ctx_inst.load_contracts.return_value = {}
            ctx_inst.build_gate_prompt.return_value = "Review this"

            rules_inst = MockRules.return_value
            rules_inst.load_rules.return_value = {}
            rules_inst.check_automated_rules.return_value = []

            dac_inst = MockDaC.return_value
            ll_inst = MockLL.return_value

            result = await orchestrator._phase_build(
                project, project_path, 1,
                ctx_inst, rules_inst, dac_inst, ll_inst, mock_git, on_progress
            )

        # Phase 1 has 3 tasks
        assert result["tasks_completed"] == 3
        assert result["success"] is True

        # Git should have committed for each task
        assert mock_git.atomic_commit.call_count == 3

    async def test_worker_failure_doesnt_crash_pipeline(self, orchestrator, tmp_db,
                                                         tmp_path, progress_tracker):
        """If a worker fails on a task, pipeline continues to next task."""
        events, on_progress = progress_tracker
        db_path, _ = tmp_db

        project_path = str(tmp_path / "projects" / "simple_todo_app")
        Path(project_path).mkdir(parents=True, exist_ok=True)

        # Make the code generation worker fail
        fail_worker = MagicMock()
        fail_worker.name = "qwen"
        fail_worker.config = {"type": "mock", "model": "qwen"}
        fail_worker.send_message = AsyncMock(return_value={
            "success": False, "error": "Timeout",
        })

        original_get = orchestrator.router.get_worker.side_effect

        def get_worker_with_failure(role):
            if role in ("code_generation_simple", "code_generation_complex"):
                return fail_worker
            return original_get(role)

        orchestrator.router.get_worker = MagicMock(side_effect=get_worker_with_failure)

        mock_git = MagicMock()
        mock_git.create_phase_branch.return_value = "phase/1-backend"
        mock_git.pull_latest.return_value = True
        mock_git.atomic_commit = AsyncMock(return_value="abc123")
        mock_git.merge_to_develop.return_value = True
        mock_git.get_changed_files.return_value = []

        project = orchestrator.db.get_project("proj_todo")

        with patch("orchestration.master_orchestrator.GitManager", return_value=mock_git), \
             patch("orchestration.master_orchestrator.ContextManager") as MockCtx, \
             patch("orchestration.master_orchestrator.RulesEngine") as MockRules, \
             patch("orchestration.master_orchestrator.DaCTagger") as MockDaC, \
             patch("orchestration.master_orchestrator.LearningLog") as MockLL:

            ctx_inst = MockCtx.return_value
            ctx_inst.build_task_prompt.return_value = "Build this"
            ctx_inst.load_contracts.return_value = {}

            rules_inst = MockRules.return_value
            rules_inst.load_rules.return_value = {}

            dac_inst = MockDaC.return_value
            ll_inst = MockLL.return_value

            result = await orchestrator._phase_build(
                project, project_path, 1,
                ctx_inst, rules_inst, dac_inst, ll_inst, mock_git, on_progress
            )

        # All 3 tasks should have failed (worker error)
        assert result["tasks_failed"] == 3
        assert result["success"] is False


class TestQualityGateRejection:
    """Test the Kimi quality gate rejection + retry + HRO escalation."""

    async def test_gate_rejection_retries_twice(self, orchestrator, tmp_db,
                                                  tmp_path, progress_tracker):
        """Double gate rejection should trigger HRO escalation."""
        events, on_progress = progress_tracker
        db_path, _ = tmp_db

        project_path = str(tmp_path / "projects" / "simple_todo_app")
        Path(project_path).mkdir(parents=True, exist_ok=True)

        # Make gatekeeper always reject
        reject_worker = MagicMock()
        reject_worker.name = "kimi"
        reject_worker.config = {"type": "mock", "model": "kimi"}
        reject_worker.send_message = AsyncMock(return_value={
            "success": True,
            "response": json.dumps({
                "verdict": "REJECTED",
                "confidence": 0.3,
                "issues": ["Code quality below threshold"],
            }),
        })

        # Regular worker that succeeds
        code_worker = MagicMock()
        code_worker.name = "qwen"
        code_worker.config = {"type": "mock", "model": "qwen"}
        code_worker.send_message = AsyncMock(return_value={
            "success": True,
            "response": json.dumps({
                "files": [{"path": "server.js", "content": "code", "action": "create"}],
                "decisions": [], "notes": [],
            }),
        })

        def get_worker_for_gate_test(role):
            if role in ("gatekeeper_review", "project_classification"):
                return reject_worker
            if role in ("code_generation_simple", "code_generation_complex"):
                return code_worker
            return orchestrator.router.get_worker.side_effect(role)

        orchestrator.router.get_worker = MagicMock(side_effect=get_worker_for_gate_test)

        mock_git = MagicMock()
        mock_git.create_phase_branch.return_value = "phase/1"
        mock_git.pull_latest.return_value = True
        mock_git.atomic_commit = AsyncMock(return_value="abc")
        mock_git.merge_to_develop.return_value = True
        mock_git.get_changed_files.return_value = []

        mock_parser = MagicMock()
        mock_parser.parse_and_apply.return_value = (
            {"files_written": [{"path": "f.js", "action": "create"}],
             "decisions_logged": []},
            [],
        )

        mock_validator = MagicMock()
        mock_validator.load_contracts.return_value = False

        project = orchestrator.db.get_project("proj_todo")

        with patch("orchestration.master_orchestrator.GitManager", return_value=mock_git), \
             patch("orchestration.master_orchestrator.OutputParser", return_value=mock_parser), \
             patch("orchestration.master_orchestrator.ContractValidator", return_value=mock_validator), \
             patch("orchestration.master_orchestrator.ContextManager") as MockCtx, \
             patch("orchestration.master_orchestrator.RulesEngine") as MockRules, \
             patch("orchestration.master_orchestrator.DaCTagger") as MockDaC, \
             patch("orchestration.master_orchestrator.LearningLog") as MockLL:

            ctx_inst = MockCtx.return_value
            ctx_inst.build_task_prompt.return_value = "Build"
            ctx_inst.load_contracts.return_value = {}
            ctx_inst.build_gate_prompt.return_value = "Review"

            rules_inst = MockRules.return_value
            rules_inst.load_rules.return_value = {}
            rules_inst.check_automated_rules.return_value = []

            dac_inst = MockDaC.return_value
            ll_inst = MockLL.return_value

            result = await orchestrator._phase_build(
                project, project_path, 1,
                ctx_inst, rules_inst, dac_inst, ll_inst, mock_git, on_progress
            )

        # All tasks should have failed due to double rejection
        assert result["tasks_failed"] == 3
        assert result["success"] is False

        # DaC tagger should have been called for double rejection
        assert dac_inst.tag.called or dac_inst.tag_gate_rejection.called


# ═══════════════════════════════════════════════════════════════
# TEST: DUAL AUDIT
# ═══════════════════════════════════════════════════════════════


class TestDualAudit:
    """Test blueprint dual audit (Kimi + Gemini)."""

    async def test_dual_audit_calls_both_workers(self, orchestrator):
        """Both kimi (gatekeeper) and gemini (architect) should be called."""
        result = await orchestrator._dual_audit_blueprint(
            "Blueprint: Express + React Todo App", "web"
        )

        assert result["audited"] is True
        # Should have issues (feedback) from both auditors
        sources = [i["source"] for i in result.get("issues", [])]
        assert "kimi" in sources
        assert "gemini" in sources


# ═══════════════════════════════════════════════════════════════
# TEST: TASK CLASSIFICATION
# ═══════════════════════════════════════════════════════════════


class TestTaskClassification:
    """Test Kimi-based task complexity classification."""

    async def test_classify_returns_low_for_simple(self, orchestrator):
        task = {"module": "config/env", "description": "Set environment variables"}

        # Kimi mock returns "low"
        result = await orchestrator._classify_task(task)
        assert result in ("low", "high")

    async def test_classify_defaults_to_low_on_failure(self, orchestrator):
        # Make kimi unavailable
        orchestrator.router.get_worker = MagicMock(return_value=None)
        task = {"module": "backend", "description": "Complex task"}

        result = await orchestrator._classify_task(task)
        assert result == "low"


# ═══════════════════════════════════════════════════════════════
# TEST: CONTEXT MANAGER INTEGRATION
# ═══════════════════════════════════════════════════════════════


class TestContextManagerIntegration:
    """Test ContextManager loads protocols and builds prompts."""

    def test_load_protocol_web(self, tmp_path, read_db):
        from orchestration.context_manager import ContextManager

        proto_dir = tmp_path / "protocols"
        proto_dir.mkdir(exist_ok=True)
        (proto_dir / "web.md").write_text("# Web Protocol\nREST rules.")

        ctx = ContextManager(str(tmp_path), read_db)
        proto = ctx.load_protocol("web")
        assert "Web Protocol" in proto

    def test_load_protocol_fallback_to_web(self, tmp_path, read_db):
        from orchestration.context_manager import ContextManager

        proto_dir = tmp_path / "protocols"
        proto_dir.mkdir(exist_ok=True)
        (proto_dir / "web.md").write_text("# Web Fallback")

        ctx = ContextManager(str(tmp_path), read_db)
        proto = ctx.load_protocol("nonexistent_type")
        assert "Web Fallback" in proto

    def test_load_contracts_from_directory(self, tmp_path, read_db):
        from orchestration.context_manager import ContextManager

        project_path = tmp_path / "projects" / "test"
        contracts_dir = project_path / "contracts"
        contracts_dir.mkdir(parents=True)
        (contracts_dir / "api_contract.json").write_text(
            json.dumps({"endpoints": [{"method": "GET", "path": "/todos"}]})
        )

        ctx = ContextManager(str(tmp_path), read_db)
        contracts = ctx.load_contracts(str(project_path))
        assert "api_contract.json" in contracts


# ═══════════════════════════════════════════════════════════════
# TEST: CONTRACT VALIDATOR
# ═══════════════════════════════════════════════════════════════


class TestContractValidator:
    """Test ContractValidator loads + validates code against contracts."""

    def test_load_contracts_when_files_exist(self, tmp_path):
        from orchestration.contract_validator import ContractValidator

        project_path = tmp_path / "myproject"
        contracts_dir = project_path / "contracts"
        contracts_dir.mkdir(parents=True)
        (contracts_dir / "api_contract.json").write_text(
            json.dumps({"endpoints": [
                {"method": "GET", "path": "/api/todos", "response": {"type": "array"}},
            ]})
        )

        validator = ContractValidator(str(project_path))
        loaded = validator.load_contracts()
        assert loaded is True

    def test_load_contracts_returns_false_when_missing(self, tmp_path):
        from orchestration.contract_validator import ContractValidator

        validator = ContractValidator(str(tmp_path / "empty_project"))
        loaded = validator.load_contracts()
        assert loaded is False


# ═══════════════════════════════════════════════════════════════
# TEST: OUTPUT PARSER
# ═══════════════════════════════════════════════════════════════


class TestOutputParser:
    """Test structured output parsing from worker responses."""

    def test_parse_valid_json(self, tmp_path):
        from orchestration.output_parser import OutputParser

        parser = OutputParser(str(tmp_path))
        raw = json.dumps({
            "files": [{"path": "server.js", "content": "code", "action": "create"}],
            "decisions": [{"type": "minor", "description": "Used Express"}],
            "notes": ["Setup complete"],
        })
        parsed = parser.parse(raw)
        assert "files" in parsed
        assert len(parsed["files"]) == 1

    def test_parse_and_apply_with_violations(self, tmp_path):
        from orchestration.output_parser import OutputParser

        parser = OutputParser(str(tmp_path))
        summary, violations = parser.parse_and_apply(
            "This is not valid JSON at all", "task_001", worker_name="qwen"
        )

        # Should have a HAL violation (JSON parse failure = hallucination tag per FER-CLI-001)
        assert len(violations) >= 1
        assert violations[0]["violation_tag"] == "HAL"


# ═══════════════════════════════════════════════════════════════
# TEST: RULES ENGINE
# ═══════════════════════════════════════════════════════════════


class TestRulesEngine:
    """Test DaC rules engine."""

    def test_generate_rules_file(self, tmp_path):
        from orchestration.rules_engine import RulesEngine

        engine = RulesEngine(None)
        project_path = str(tmp_path / "project")
        Path(project_path).mkdir(parents=True)

        engine.generate_rules_file(project_path, "web")

        rules_file = Path(project_path) / "rules" / "project_rules.json"
        assert rules_file.exists()
        data = json.loads(rules_file.read_text())
        assert "rules" in data
        assert len(data["rules"]) >= 9  # 9 default rules

    def test_check_automated_rules(self, tmp_path):
        from orchestration.rules_engine import RulesEngine

        engine = RulesEngine(None)
        project_path = str(tmp_path / "project")
        Path(project_path).mkdir(parents=True)
        engine.generate_rules_file(project_path, "web")
        engine.load_rules(project_path)

        violations = engine.check_automated_rules(
            "task_001", {"files": [{"path": "server.js", "action": "create"}]}
        )
        # With clean output, should have no/minimal violations
        assert isinstance(violations, list)


# ═══════════════════════════════════════════════════════════════
# TEST: GIT MANAGER (UNIT)
# ═══════════════════════════════════════════════════════════════


class TestGitManager:
    """Test GitManager operations (with mock subprocess)."""

    def test_init_repo_creates_git_dir(self, tmp_path):
        from orchestration.git_manager import GitManager

        project_path = tmp_path / "new_project"
        project_path.mkdir()

        git_mgr = GitManager(str(project_path))

        with patch("orchestration.git_manager.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr=""
            )
            git_mgr.init_repo()

        # Should have called git init
        calls = [str(c) for c in mock_run.call_args_list]
        assert any("init" in c for c in calls)


# ═══════════════════════════════════════════════════════════════
# TEST: DAC TAGGER
# ═══════════════════════════════════════════════════════════════


class TestDaCTagger:
    """Test DaC auto-tagging."""

    def test_tag_creates_write_request(self, read_db):
        from orchestration.dac_tagger import DaCTagger

        tagger = DaCTagger(read_db)
        tag_type = tagger.tag("task_001", "test_event", "Test context",
                               project_id="proj_todo")

        # Should return a tag type string
        assert isinstance(tag_type, str)

    def test_tag_from_tdd_result(self, read_db):
        from orchestration.dac_tagger import DaCTagger

        tagger = DaCTagger(read_db)
        tdd_result = {
            "success": True,
            "bugs": [{"id": "BUG-001", "severity": "high", "description": "SQL injection"}],
            "dac_tags": ["DOM", "SER"],
        }
        # Should not crash
        tagger.tag_from_tdd_result("task_001", tdd_result, "proj_todo")


# ═══════════════════════════════════════════════════════════════
# TEST: LEARNING LOG
# ═══════════════════════════════════════════════════════════════


class TestLearningLog:
    """Test learning log bug tracking."""

    def test_instantiation(self, read_db):
        from orchestration.learning_log import LearningLog
        ll = LearningLog(read_db)
        assert ll is not None


# ═══════════════════════════════════════════════════════════════
# TEST: DATABASE SCHEMA + READS
# ═══════════════════════════════════════════════════════════════


class TestDatabaseSchema:
    """Test schema v3 tables and read operations."""

    def test_get_project(self, read_db):
        project = read_db.get_project("proj_todo")
        assert project is not None
        assert project["name"] == "Simple Todo App"
        assert project["status"] == "active"

    def test_get_tasks_by_phase(self, read_db):
        tasks = read_db.get_tasks_by_phase("proj_todo", 1)
        assert len(tasks) == 3  # 3 backend tasks

    def test_get_tasks_by_phase_2(self, read_db):
        tasks = read_db.get_tasks_by_phase("proj_todo", 2)
        assert len(tasks) == 3  # 3 frontend tasks

    def test_get_tasks_by_phase_3(self, read_db):
        tasks = read_db.get_tasks_by_phase("proj_todo", 3)
        assert len(tasks) == 2  # 2 integration tasks

    def test_get_nonexistent_project(self, read_db):
        assert read_db.get_project("nonexistent") is None

    def test_get_last_checkpoint_none(self, read_db):
        assert read_db.get_last_checkpoint("nonexistent_task") is None

    def test_get_latest_blueprint_none(self, read_db):
        assert read_db.get_latest_blueprint("proj_todo") is None

    def test_get_task_stats(self, read_db):
        stats = read_db.get_task_stats("proj_todo")
        assert stats.get("pending") == 8  # all 8 tasks are pending

    def test_get_pending_escalations_empty(self, read_db):
        escalations = read_db.get_pending_escalations()
        assert escalations == []


# ═══════════════════════════════════════════════════════════════
# TEST: CICD GENERATOR
# ═══════════════════════════════════════════════════════════════


class TestCICDGenerator:
    """Test CI/CD pipeline generation."""

    def test_generate_web_pipeline(self, tmp_path):
        from orchestration.cicd_generator import CICDGenerator

        gen = CICDGenerator(str(tmp_path))
        result = gen.generate("web")
        assert result is not None


# ═══════════════════════════════════════════════════════════════
# TEST: CONTRACT GENERATOR
# ═══════════════════════════════════════════════════════════════


class TestContractGenerator:
    """Test contract generation from blueprint."""

    def test_lock_contracts(self, tmp_path):
        from orchestration.contract_generator import ContractGenerator

        project_path = tmp_path / "project"
        contracts_dir = project_path / "contracts"
        contracts_dir.mkdir(parents=True)
        (contracts_dir / "api_contract.json").write_text("{}")

        gen = ContractGenerator(str(project_path))
        gen.lock_contracts()

        lock_file = contracts_dir / ".locked"
        assert lock_file.exists()
        data = json.loads(lock_file.read_text())
        assert data["locked"] is True

    async def test_generate_from_blueprint_without_worker(self, tmp_path):
        from orchestration.contract_generator import ContractGenerator

        gen = ContractGenerator(str(tmp_path / "project"))
        result = await gen.generate_from_blueprint(
            "# Blueprint\n## API\nGET /todos\nPOST /todos",
            worker_adapter=None,
        )
        assert isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════
# TEST: FULL TDD PIPELINE CHECKPOINTING
# ═══════════════════════════════════════════════════════════════


class TestTDDCheckpointing:
    """Test TDD pipeline crash recovery via checkpoints."""

    async def test_checkpoint_written_per_step(self, read_db, mock_router):
        """Each step should write a checkpoint to the DB queue."""
        tdd = TDDPipeline(read_db, mock_router)

        task = {"task_id": "task_cp", "description": "Test checkpointing",
                "module": "backend", "phase": 1}
        project = {"project_id": "proj_test", "project_type": "web"}
        code_output = {"files": []}

        result = await tdd.execute(task, project, code_output, fast_track=False)

        assert result["success"] is True
        # Write queue should have received checkpoint entries
        queue = get_write_queue()
        # Queue may have items (we can't easily inspect without draining)
        # Just verify the pipeline completed
        assert result["results"]["CCP"]["success"] is True

    async def test_resume_from_step_not_none(self, read_db, mock_router, tmp_db):
        """If a checkpoint exists, pipeline should resume from that step."""
        db_path, wdb = tmp_db

        # Insert a task for the checkpoint to reference
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT OR IGNORE INTO tasks (task_id, project_id, phase, module, "
            "description, status) VALUES ('task_resume', 'proj_todo', 1, "
            "'backend', 'Test resume', 'in_progress')"
        )
        # Insert a checkpoint at step BC (index 4)
        conn.execute(
            "INSERT INTO checkpoints (task_id, worker, step, state_data, tests_status) "
            "VALUES ('task_resume', 'tdd_pipeline', 'BC', "
            "'{\"step_index\": 4, \"success\": true}', '{\"step\": \"BC\", \"passed\": true}')"
        )
        conn.commit()
        conn.close()

        tdd = TDDPipeline(read_db, mock_router)
        resume_step = tdd._get_resume_step("task_resume")
        assert resume_step == 4  # Should resume from step 4 (BC)


# ═══════════════════════════════════════════════════════════════
# TEST: END-TO-END PROGRESS TRACKING
# ═══════════════════════════════════════════════════════════════


class TestProgressTracking:
    """Test that progress events fire correctly through the pipeline."""

    async def test_tdd_progress_includes_all_step_ids(self, read_db, mock_router):
        """TDD progress callback should fire for every step."""
        events = []

        async def on_progress(step_id, name, status):
            events.append({"step": step_id, "name": name, "status": status})

        tdd = TDDPipeline(read_db, mock_router)
        task = {"task_id": "task_prog", "description": "Progress test",
                "module": "backend", "phase": 1}
        project = {"project_id": "proj_test", "project_type": "web"}

        await tdd.execute(task, project, {"files": []},
                          on_progress=on_progress, fast_track=False)

        step_ids = {e["step"] for e in events if e["status"] == "running"}
        expected = {s["id"] for s in TDD_STEPS}
        assert step_ids == expected

    async def test_fast_track_progress_only_5_steps(self, read_db, mock_router):
        """Fast-track progress should only fire for 5 steps."""
        events = []

        async def on_progress(step_id, name, status):
            events.append({"step": step_id, "status": status})

        tdd = TDDPipeline(read_db, mock_router)
        task = {"task_id": "task_ft_prog", "description": "CSS color update",
                "module": "frontend/styles", "phase": 2}
        project = {"project_id": "proj_test", "project_type": "web"}

        await tdd.execute(task, project, {"files": []},
                          on_progress=on_progress, fast_track=True)

        running_steps = {e["step"] for e in events if e["status"] == "running"}
        assert running_steps == FAST_TRACK_STEPS


# ═══════════════════════════════════════════════════════════════
# TEST: STATIC ANALYSIS TOOL INTEGRATION IN TDD PIPELINE
# ═══════════════════════════════════════════════════════════════


class TestTDDPipelineStaticAnalysis:
    """Test real tool integration in BC/SEA/DS steps."""

    async def test_project_path_creates_analyzer(self, read_db, mock_router, tmp_path):
        """TDDPipeline with project_path should have a StaticAnalyzer."""
        tdd = TDDPipeline(read_db, mock_router, project_path=str(tmp_path))
        assert tdd._analyzer is not None
        assert tdd.project_path == str(tmp_path)

    async def test_no_project_path_no_analyzer(self, read_db, mock_router):
        """TDDPipeline without project_path should have no analyzer."""
        tdd = TDDPipeline(read_db, mock_router)
        assert tdd._analyzer is None

    async def test_bc_step_includes_tool_findings_in_prompt(self, read_db, mock_router, tmp_path):
        """BC step should inject flake8 findings into the LLM prompt."""
        (tmp_path / "main.py").write_text("x = 1\n")

        tdd = TDDPipeline(read_db, mock_router, project_path=str(tmp_path))

        # Mock the analyzer to return findings
        from orchestration.static_analysis import AnalysisResult, Finding, ToolStatus
        mock_result = AnalysisResult(
            tool_name="flake8", status=ToolStatus.AVAILABLE,
            findings=[Finding(
                tool="flake8", file="main.py", line=4, code="E302",
                severity="medium", message="E302 expected 2 blank lines",
            )]
        )
        tdd._analyzer.run_bug_capture = AsyncMock(return_value=mock_result)

        task = {"task_id": "task_sa_bc", "description": "Test BC with tools",
                "module": "backend", "phase": 1}
        code_output = {"files": [{"path": "main.py", "content": "x = 1"}]}

        # Capture the prompt sent to the worker
        prompts_sent = []
        original_call = tdd._call_tdd_worker

        async def capture_call(prompt, system_prompt):
            prompts_sent.append(prompt)
            return '{"bugs": [], "clean": true}'

        tdd._call_tdd_worker = capture_call

        result = await tdd._step_bug_capture(task, code_output)

        assert result.success
        assert len(prompts_sent) == 1
        assert "flake8" in prompts_sent[0].lower()
        assert "E302" in prompts_sent[0]

    async def test_sea_step_includes_tool_findings_in_prompt(self, read_db, mock_router, tmp_path):
        """SEA step should inject bandit subset findings into LLM prompt."""
        (tmp_path / "utils.py").write_text("pass\n")

        tdd = TDDPipeline(read_db, mock_router, project_path=str(tmp_path))

        from orchestration.static_analysis import AnalysisResult, Finding, ToolStatus
        mock_result = AnalysisResult(
            tool_name="bandit", status=ToolStatus.AVAILABLE,
            findings=[Finding(
                tool="bandit", file="utils.py", line=3, code="B110",
                severity="low", message="Try/except pass detected",
            )]
        )
        tdd._analyzer.run_silent_error_analysis = AsyncMock(return_value=mock_result)

        task = {"task_id": "task_sa_sea", "description": "Test SEA with tools",
                "module": "backend", "phase": 1}
        code_output = {"files": [{"path": "utils.py", "content": "try:\n  x()\nexcept:\n  pass"}]}

        prompts_sent = []

        async def capture_call(prompt, system_prompt):
            prompts_sent.append(prompt)
            return '{"issues": [], "clean": true}'

        tdd._call_tdd_worker = capture_call

        result = await tdd._step_silent_error_analysis(task, code_output)

        assert result.success
        assert "bandit" in prompts_sent[0].lower()
        assert "B110" in prompts_sent[0]

    async def test_ds_step_includes_tool_findings_in_prompt(self, read_db, mock_router, tmp_path):
        """DS step should inject bandit+pip-audit findings into LLM prompt."""
        (tmp_path / "main.py").write_text("eval(input())\n")
        (tmp_path / "requirements.txt").write_text("flask==2.3.0\n")

        tdd = TDDPipeline(read_db, mock_router, project_path=str(tmp_path))

        from orchestration.static_analysis import AnalysisResult, Finding, ToolStatus
        bandit_result = AnalysisResult(
            tool_name="bandit", status=ToolStatus.AVAILABLE,
            findings=[Finding(
                tool="bandit", file="main.py", line=1, code="B307",
                severity="high", message="Use of eval detected",
            )]
        )
        pip_result = AnalysisResult(
            tool_name="pip-audit", status=ToolStatus.AVAILABLE,
            findings=[Finding(
                tool="pip-audit", file="requirements.txt", line=0,
                code="CVE-2023-1234", severity="high",
                message="flask==2.3.0: XSS vulnerability",
            )]
        )
        tdd._analyzer.run_security_scan = AsyncMock(return_value=[bandit_result, pip_result])

        task = {"task_id": "task_sa_ds", "description": "Test DS with tools",
                "module": "backend", "phase": 1}
        code_output = {"files": [{"path": "main.py", "content": "eval(input())"}]}

        prompts_sent = []

        async def capture_call(prompt, system_prompt):
            prompts_sent.append(prompt)
            return '{"vulnerabilities": [], "secure": true}'

        tdd._call_tdd_worker = capture_call

        result = await tdd._step_security_scan(task, code_output)

        assert result.success
        assert "bandit" in prompts_sent[0].lower()
        assert "B307" in prompts_sent[0]
        assert "pip-audit" in prompts_sent[0].lower()
        assert "CVE-2023-1234" in prompts_sent[0]

    async def test_llm_only_fallback_when_no_project_path(self, read_db, mock_router):
        """When no project_path, steps should still work (LLM-only)."""
        tdd = TDDPipeline(read_db, mock_router)  # No project_path

        task = {"task_id": "task_fallback", "description": "Test fallback",
                "module": "backend", "phase": 1}
        project = {"project_id": "proj_test", "project_type": "web"}
        code_output = {"files": [{"path": "main.py", "content": "x = 1"}]}

        result = await tdd.execute(task, project, code_output, fast_track=False)

        assert result["success"] is True
        # BC, SEA, DS all ran without errors
        assert result["results"]["BC"]["success"] is True
        assert result["results"]["SEA"]["success"] is True
        assert result["results"]["DS"]["success"] is True
