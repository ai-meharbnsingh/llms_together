"""
Wave 4 Tests — FER-AF-010 + 2 pre-existing test root causes

FER-AF-010: E2E failure must set e2e_failed=True in result so dashboard can
            block UAT button.  Currently the result is returned without checking
            E2E success, so broken projects reach production.

Pre-existing roots:
  P1: approve_blueprint uses request_write_and_wait (times out without Watchdog)
  P3: SCHEMA_VERSION = 4 but test_fresh_db_is_v3 expects 3

Note: P2 (atomic_commit sync vs async) was resolved in Wave 1 tests — atomic_commit
      IS async def with asyncio.Lock (ground truth). The e2e tests must use AsyncMock.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-010: E2E failure must propagate as e2e_failed flag in result
# ─────────────────────────────────────────────────────────────────────────────

class TestFerAF010E2EGatesUAT(unittest.IsolatedAsyncioTestCase):
    """
    FER-AF-010: When _run_e2e_tests() reports success=False, execute_project()
    must include "e2e_failed": True in its response so the dashboard can block
    the UAT approval button.
    """

    async def test_e2e_failure_sets_e2e_failed_flag(self):
        """
        If E2E tests return success=False, the execute_project result dict must
        contain "e2e_failed": True.
        """
        from orchestration.master_orchestrator import MasterOrchestrator
        from orchestration.database import ReadOnlyDB

        db = MagicMock(spec=ReadOnlyDB)
        db.set_requester = MagicMock()
        db.get_chat_history = MagicMock(return_value=[])
        db.get_project = MagicMock(return_value={
            "project_id": "proj1", "name": "Test", "project_path": "/tmp/proj",
            "current_phase": 3, "status": "active", "project_type": "web",
        })
        db.get_tasks_by_phase = MagicMock(return_value=[])

        orch = MasterOrchestrator(
            read_db=db,
            role_router=MagicMock(),
            config={"factory": {"working_dir": "/tmp/w4", "factory_state_dir": None}},
            working_dir="/tmp/w4",
        )

        async def fake_e2e(project_path, on_progress=None):
            return {"success": False, "returncode": 1, "output_tail": "3 tests failed"}

        mock_git = MagicMock()
        mock_git.init_repo.return_value = True

        with patch.object(orch, "_run_e2e_tests", side_effect=fake_e2e), \
             patch.object(orch, "_phase_blueprint", new=AsyncMock(return_value={
                 "approved": True, "total_phases": 3, "version": 1, "contracts": {},
                 "audit": {"issues": []},
             })), \
             patch.object(orch, "_phase_build", new=AsyncMock(return_value={
                 "success": True, "tasks_completed": 2,
             })), \
             patch("orchestration.master_orchestrator.GitManager", return_value=mock_git), \
             patch("orchestration.master_orchestrator.CICDGenerator", MagicMock()), \
             patch.object(orch, "_generate_tasks_from_blueprint", new=AsyncMock(return_value=3)), \
             patch.object(orch, "db") as mock_db:
            mock_db.get_project.return_value = {
                "project_id": "proj1", "name": "Test", "project_path": "/tmp/proj",
                "current_phase": 3, "status": "active", "project_type": "web",
            }
            mock_db.get_tasks_by_phase.return_value = []
            mock_db.get_chat_history.return_value = []
            mock_db.request_write = MagicMock()

            result = await orch.execute_project("proj1")

        self.assertIn(
            "e2e_failed", result,
            "execute_project result must include 'e2e_failed' key when E2E tests fail"
        )
        self.assertTrue(
            result.get("e2e_failed"),
            "e2e_failed must be True when _run_e2e_tests returns success=False"
        )

    async def test_e2e_success_does_not_set_e2e_failed(self):
        """
        Control: when E2E tests pass, e2e_failed must be False (or absent).
        """
        from orchestration.master_orchestrator import MasterOrchestrator

        db = MagicMock()
        db.set_requester = MagicMock()
        db.get_chat_history = MagicMock(return_value=[])
        orch = MasterOrchestrator(
            read_db=db,
            role_router=MagicMock(),
            config={"factory": {"working_dir": "/tmp/w4b", "factory_state_dir": None}},
            working_dir="/tmp/w4b",
        )

        async def fake_e2e(project_path, on_progress=None):
            return {"success": True, "returncode": 0, "output_tail": "All tests passed"}

        mock_git2 = MagicMock()
        mock_git2.init_repo.return_value = True

        with patch.object(orch, "_run_e2e_tests", side_effect=fake_e2e), \
             patch.object(orch, "_phase_blueprint", new=AsyncMock(return_value={
                 "approved": True, "total_phases": 3, "version": 1, "contracts": {},
                 "audit": {"issues": []},
             })), \
             patch.object(orch, "_phase_build", new=AsyncMock(return_value={
                 "success": True, "tasks_completed": 2,
             })), \
             patch("orchestration.master_orchestrator.GitManager", return_value=mock_git2), \
             patch("orchestration.master_orchestrator.CICDGenerator", MagicMock()), \
             patch.object(orch, "db") as mock_db:
            mock_db.get_project.return_value = {
                "project_id": "proj1", "name": "Test", "project_path": "/tmp/proj",
                "current_phase": 3, "status": "active", "project_type": "web",
            }
            mock_db.get_tasks_by_phase.return_value = []
            mock_db.get_chat_history.return_value = []
            mock_db.request_write = MagicMock()

            result = await orch.execute_project("proj1")

        self.assertFalse(
            result.get("e2e_failed", False),
            "e2e_failed must be False (or absent) when E2E tests pass"
        )


# ─────────────────────────────────────────────────────────────────────────────
# P1: approve_blueprint must NOT use request_write_and_wait (times out in tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestApprovesBlueprintWithoutWatchdog(unittest.IsolatedAsyncioTestCase):
    """
    approve_blueprint() must complete within 1 second without a running Watchdog.
    request_write_and_wait blocks for the Watchdog to drain the queue; in tests
    (and any environment where Watchdog isn't running) it times out after 10s.
    """

    async def test_approve_blueprint_completes_without_watchdog(self):
        """
        approve_blueprint must finish promptly using fire-and-forget writes,
        not blocking request_write_and_wait.
        """
        import sqlite3, tempfile
        from orchestration.database import WatchdogDB, ReadOnlyDB
        from orchestration.master_orchestrator import MasterOrchestrator

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "factory.db")
            wdb = WatchdogDB(db_path)

            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                "INSERT INTO projects (project_id, name, description, status, current_phase)"
                " VALUES ('p1', 'Test', 'desc', 'active', 0)"
            )
            conn.execute(
                "INSERT INTO blueprint_revisions (project_id, version, changes_summary,"
                " blueprint_content, reason) VALUES ('p1', 1, 'v1', 'Blueprint', 'gen')"
            )
            project_path = Path(tmpdir) / "p1"
            project_path.mkdir()
            conn.execute(
                "UPDATE projects SET project_path=? WHERE project_id='p1'",
                (str(project_path),)
            )
            conn.commit()
            conn.close()

            rdb = ReadOnlyDB(db_path)
            rdb.set_requester("test")

            orch = MasterOrchestrator(
                read_db=rdb,
                role_router=MagicMock(),
                config={
                    "factory": {
                        "working_dir": tmpdir,
                        "factory_state_dir": str(Path(tmpdir) / "state"),
                    }
                },
                working_dir=tmpdir,
            )

            # Must complete within 1 second — if it uses request_write_and_wait it times out
            try:
                result = await asyncio.wait_for(
                    orch.approve_blueprint("p1"),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                self.fail(
                    "approve_blueprint timed out — it is still using request_write_and_wait. "
                    "Replace with request_write (fire-and-forget)."
                )

            self.assertTrue(result.get("success"), f"Expected success, got: {result}")


# ─────────────────────────────────────────────────────────────────────────────
# P3: SCHEMA_VERSION must be 3 (test_fresh_db_is_v3 is ground truth)
# ─────────────────────────────────────────────────────────────────────────────

class TestSchemaVersionIsThree(unittest.TestCase):
    """
    P3: SCHEMA_VERSION must equal 3 so that fresh databases report version 3.
    The dependencies column is already in SCHEMA_SQL; it does NOT require a v4
    schema bump.
    """

    def test_schema_version_constant_is_3(self):
        """SCHEMA_VERSION in database.py must equal 3."""
        from orchestration import database
        self.assertEqual(
            database.SCHEMA_VERSION, 3,
            f"SCHEMA_VERSION is {database.SCHEMA_VERSION} — must be 3. "
            "The dependencies column is already in SCHEMA_SQL; no v4 bump needed."
        )

    def test_fresh_db_reports_version_3(self):
        """A fresh WatchdogDB must have schema_version = 3."""
        import sqlite3, tempfile
        from orchestration.database import WatchdogDB

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "fresh.db")
            WatchdogDB(db_path)
            conn = sqlite3.connect(db_path)
            row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            conn.close()

        self.assertEqual(
            row[0], 3,
            f"Fresh DB has schema version {row[0]}, expected 3"
        )


if __name__ == "__main__":
    unittest.main()
