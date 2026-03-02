"""
FER Wave 3 Tests — Async correctness, git integrity, task lifecycle.

Covers:
  FER-AF-018  time.sleep() in queue_write() / ReadOnlyDB.request_write() blocks event loop
  FER-AF-019  time.sleep() in ProcessReaper._force_kill() blocks event loop
  FER-AF-017  git init_repo() return value never checked — silent git failure
  FER-AF-042  Task never set to in_progress — watchdog stuck-task detection never fires
"""

import asyncio
import inspect
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-018: queue_write and ReadOnlyDB.request_write must not use time.sleep
# ─────────────────────────────────────────────────────────────────────────────

class TestNoTimeSleepInWriteQueue:
    """queue_write and request_write are called from async contexts.
    time.sleep() there blocks the event loop and prevents the Watchdog drain
    loop from running — making QueueFull retries self-defeating."""

    def test_queue_write_no_time_sleep(self):
        """Module-level queue_write must not call time.sleep."""
        from orchestration.database import queue_write
        src = inspect.getsource(queue_write)
        assert "time.sleep" not in src, (
            "queue_write() must not call time.sleep — use asyncio.sleep or "
            "remove the backoff (sleeping blocks the event loop that would "
            "drain the queue)."
        )

    def test_readonly_db_request_write_no_time_sleep(self):
        """ReadOnlyDB.request_write must not call time.sleep."""
        from orchestration.database import ReadOnlyDB
        src = inspect.getsource(ReadOnlyDB.request_write)
        assert "time.sleep" not in src, (
            "ReadOnlyDB.request_write() must not call time.sleep — "
            "it is invoked from async task execution paths."
        )


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-019: ProcessReaper._force_kill must not use time.sleep
# ─────────────────────────────────────────────────────────────────────────────

class TestNoTimeSleepInProcessReaper:
    """_force_kill() is called from async methods (check_all, _kill_children,
    kill_subprocess).  time.sleep() there stalls the event loop up to 700ms."""

    def test_force_kill_no_time_sleep(self):
        """_force_kill must not use time.sleep directly."""
        from orchestration.process_reaper import ProcessReaper
        src = inspect.getsource(ProcessReaper._force_kill)
        assert "time.sleep" not in src, (
            "_force_kill() must not call time.sleep. Async callers should use "
            "await asyncio.to_thread(self._force_kill, pid) so the event loop "
            "remains unblocked while waiting for the signal to be processed."
        )

    def test_check_all_uses_to_thread_for_force_kill(self):
        """Async callers of _force_kill must wrap it in asyncio.to_thread."""
        from orchestration.process_reaper import ProcessReaper
        # check_all and _kill_children are the primary async callers
        src_check_all = inspect.getsource(ProcessReaper.check_all)
        src_kill_children = inspect.getsource(ProcessReaper._kill_children)
        # Either uses to_thread OR the _force_kill is itself async-compatible
        combined = src_check_all + src_kill_children
        assert "to_thread" in combined or "asyncio.sleep" in combined, (
            "Async callers of _force_kill (check_all, _kill_children) must use "
            "await asyncio.to_thread(self._force_kill, pid) to avoid blocking "
            "the event loop."
        )


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-017: init_repo() return value must be checked
# ─────────────────────────────────────────────────────────────────────────────

class TestGitInitReturnChecked:
    """When git init fails, execute_project must abort, not continue silently."""

    def test_execute_project_checks_init_repo_result(self):
        """The return value of git_mgr.init_repo() must be checked.
        A bare  git_mgr.init_repo()  call (return value ignored) must not
        appear — it must be guarded by an if or assigned."""
        import ast
        import textwrap
        from orchestration import master_orchestrator

        src = textwrap.dedent(
            inspect.getsource(master_orchestrator.MasterOrchestrator.execute_project)
        )
        tree = ast.parse(src)

        for node in ast.walk(tree):
            # An Expr node wrapping a Call means the return value is discarded
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                call = node.value
                if (isinstance(call.func, ast.Attribute) and
                        call.func.attr == "init_repo"):
                    pytest.fail(
                        "git_mgr.init_repo() return value is discarded "
                        "(bare call with no assignment or if-check). "
                        "A False return must abort execute_project before "
                        "any phase runs."
                    )
        # If we reach here no bare call found — but verify it IS called
        called = any(
            isinstance(node, ast.Attribute) and node.attr == "init_repo"
            for node in ast.walk(tree)
        )
        assert called, "init_repo() not called at all in execute_project"


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-042: Task must be set to in_progress when execution begins
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskInProgressStatus:
    """When _execute_single_task starts, it must write status='in_progress'
    to the tasks table so the Watchdog stuck-task detector can fire."""

    @pytest.fixture
    def base_task(self):
        return {
            "task_id": "t001",
            "project_id": "proj_x",
            "description": "Build API endpoint",
            "phase": 1,
            "module": "backend/api",
            "dependencies": "[]",
            "complexity": "low",
        }

    @pytest.fixture
    def base_project(self, tmp_path):
        return {
            "project_id": "proj_x",
            "name": "Test Project",
            "project_type": "web",
            "project_path": str(tmp_path / "projects" / "proj_x"),
        }

    def test_execute_single_task_sets_in_progress_status(self):
        """_execute_single_task source must set status='in_progress' early."""
        from orchestration import master_orchestrator
        src = inspect.getsource(
            master_orchestrator.MasterOrchestrator._execute_single_task
        )
        assert "in_progress" in src, (
            "_execute_single_task must write status='in_progress' to the tasks "
            "table at the start of execution. Without this, the Watchdog "
            "stuck-task detection (which looks for tasks stuck in 'in_progress' "
            ">10min) never fires."
        )

    async def test_execute_single_task_writes_in_progress_to_db(
        self, base_task, base_project, tmp_path
    ):
        """DB must receive a request_write for status='in_progress' before
        any worker call is made."""
        from orchestration.database import ReadOnlyDB, WatchdogDB
        from orchestration.master_orchestrator import MasterOrchestrator

        db_path = str(tmp_path / "factory.db")
        WatchdogDB(db_path)
        read_db = ReadOnlyDB(db_path)
        read_db.set_requester("test")

        mock_router = MagicMock()
        config = {
            "factory": {
                "factory_state_dir": str(tmp_path / "state"),
                "working_dir": str(tmp_path),
            }
        }
        (tmp_path / "state").mkdir(parents=True, exist_ok=True)
        orch = MasterOrchestrator(read_db, mock_router, config, str(tmp_path))

        written_statuses = []
        original_request_write = read_db.request_write

        def capture_write(op, table, params, **kw):
            if table == "tasks" and params.get("status"):
                written_statuses.append(params["status"])
            return original_request_write(op, table, params, **kw)

        read_db.request_write = capture_write

        project_path = str(tmp_path / "projects" / "proj_x")
        Path(project_path).mkdir(parents=True, exist_ok=True)

        mock_git = MagicMock()
        mock_git.pull_latest = MagicMock()
        mock_git.atomic_commit = AsyncMock()

        with patch.object(orch, "_classify_task", new=AsyncMock(return_value="low")), \
             patch.object(orch, "_get_worker", return_value=None), \
             patch.object(orch, "_get_worker_name", return_value="none"):
            try:
                await orch._execute_single_task(
                    base_task, base_project, project_path,
                    MagicMock(), MagicMock(), MagicMock(), mock_git,
                    on_progress=None,
                )
            except Exception:
                pass  # Task may fail — we only care that in_progress was written

        assert "in_progress" in written_statuses, (
            "Expected status='in_progress' to be written to tasks table at "
            "start of _execute_single_task, but it was not. "
            f"Statuses written: {written_statuses}"
        )
