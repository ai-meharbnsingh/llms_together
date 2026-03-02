"""
Wave 6B Tests — FER-AF-035 (drain_write_queue non-blocking) + FER-AF-039 (get_project_total_tokens)

FER-AF-035: drain_write_queue must not block the event loop.
  - Synchronous SQLite work extracted into _drain_batch_sync.
  - drain_write_queue calls asyncio.to_thread(_drain_batch_sync, batch).
  - asyncio.Future.set_result() (resolve/reject) is NOT thread-safe, so futures
    must be resolved AFTER to_thread returns, in the async context.

FER-AF-039: ReadOnlyDB.get_project_total_tokens(project_id)
  - Sums prompt_tokens + completion_tokens from cost_tracking for a project.
  - Returns 0 for unknown project_id.
  - Returns 0 gracefully when cost_tracking table doesn't exist.
"""

import asyncio
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_in_memory_watchdog_db():
    """
    Return a WatchdogDB backed by a fresh temporary file-based SQLite DB.
    (WatchdogDB requires a file path because it uses uri=True for read conn.)
    """
    from orchestration.database import WatchdogDB

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = WatchdogDB(tmp.name)
    return db, tmp.name


def _make_readonly_db_with_schema() -> tuple:
    """
    Return (ReadOnlyDB, db_path) backed by a fully initialised WatchdogDB so
    the schema (including cost_tracking) is guaranteed to exist.
    """
    from orchestration.database import ReadOnlyDB

    _, db_path = _make_in_memory_watchdog_db()
    ro = ReadOnlyDB(db_path)
    return ro, db_path


def _insert_cost_row(db_path: str, project_id: str,
                     prompt_tokens: int, completion_tokens: int):
    """Insert a cost_tracking row directly via sqlite3 (bypasses bus for tests)."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO cost_tracking (task_id, project_id, worker, operation, "
        "prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("task-1", project_id, "deepseek", "generate",
         prompt_tokens, completion_tokens,
         prompt_tokens + completion_tokens, 0.01)
    )
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-035: drain_write_queue uses asyncio.to_thread
# ─────────────────────────────────────────────────────────────────────────────

class TestDrainWriteQueueUsesToThread(unittest.IsolatedAsyncioTestCase):
    """Test 1: drain_write_queue delegates sync work to asyncio.to_thread."""

    async def test_drain_uses_to_thread(self):
        from orchestration.database import WatchdogDB, WriteResultBus, DBWriteRequest

        db, db_path = _make_in_memory_watchdog_db()
        queue = asyncio.Queue()
        result_bus = WriteResultBus()

        # Put a valid write request into the queue
        req = DBWriteRequest(
            operation="insert",
            table="cost_tracking",
            params={
                "task_id": "t1",
                "project_id": "proj-1",
                "worker": "deepseek",
                "operation": "generate",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "estimated_cost_usd": 0.01,
            },
            requester="test",
            callback_id=None,
        )
        await queue.put(req)

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            # Return a fake empty batch result so drain doesn't blow up
            mock_to_thread.return_value = []
            await db.drain_write_queue(queue, result_bus, batch_size=50)

            mock_to_thread.assert_called_once()
            # First positional arg must be _drain_batch_sync (bound methods are
            # created fresh on each attribute access so assertIs would always fail;
            # compare by __func__ and __self__ instead).
            args = mock_to_thread.call_args[0]
            first_arg = args[0]
            self.assertTrue(
                hasattr(first_arg, "__func__") and
                first_arg.__func__ is db.__class__._drain_batch_sync and
                first_arg.__self__ is db,
                "drain_write_queue must pass self._drain_batch_sync to asyncio.to_thread"
            )

        # Cleanup
        import os; os.unlink(db_path)


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-035: Futures resolved in async context, not inside the thread
# ─────────────────────────────────────────────────────────────────────────────

class TestFuturesResolvedInAsyncContext(unittest.IsolatedAsyncioTestCase):
    """Test 2: resolve()/reject() called AFTER to_thread returns (in async context)."""

    async def test_resolve_called_after_to_thread(self):
        from orchestration.database import WatchdogDB, WriteResultBus, DBWriteRequest

        db, db_path = _make_in_memory_watchdog_db()
        result_bus = WriteResultBus()
        queue = asyncio.Queue()

        req = DBWriteRequest(
            operation="insert",
            table="cost_tracking",
            params={
                "task_id": "t2",
                "project_id": "proj-2",
                "worker": "qwen",
                "operation": "generate",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "estimated_cost_usd": 0.001,
            },
            requester="test",
            callback_id="cb-abc",
        )
        await queue.put(req)

        resolve_calls = []
        reject_calls = []

        original_resolve = result_bus.resolve
        original_reject = result_bus.reject

        def tracking_resolve(cb_id, result):
            resolve_calls.append(cb_id)
            original_resolve(cb_id, result)

        def tracking_reject(cb_id, err):
            reject_calls.append(cb_id)
            original_reject(cb_id, err)

        result_bus.resolve = tracking_resolve
        result_bus.reject = tracking_reject

        # _drain_batch_sync must NOT call resolve/reject; only drain_write_queue
        # (the async method) does, after to_thread returns.
        original_drain_batch = db._drain_batch_sync

        drain_batch_resolve_calls_inside = []

        def spy_drain_batch_sync(writes):
            # Track whether result_bus.resolve was called during sync execution
            pre = list(resolve_calls)
            result = original_drain_batch(writes)
            post = list(resolve_calls)
            if len(post) > len(pre):
                drain_batch_resolve_calls_inside.append("resolve_called_inside_thread")
            return result

        db._drain_batch_sync = spy_drain_batch_sync

        # Register a waiter so the callback_id is tracked
        fut = result_bus.create_waiter("cb-abc")

        await db.drain_write_queue(queue, result_bus, batch_size=50)

        # resolve must have been called (for the callback_id)
        self.assertIn("cb-abc", resolve_calls,
                      "result_bus.resolve must be called for requests with callback_id")

        # resolve must NOT have been called from inside _drain_batch_sync
        self.assertEqual(drain_batch_resolve_calls_inside, [],
                         "_drain_batch_sync must NOT call resolve() — futures must be "
                         "resolved in async context after to_thread returns")

        # Future must be fulfilled
        self.assertTrue(fut.done(), "Future for cb-abc must be resolved after drain")

        import os; os.unlink(db_path)


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-039: get_project_total_tokens — correct sum
# ─────────────────────────────────────────────────────────────────────────────

class TestGetProjectTotalTokens(unittest.TestCase):
    """Test 3: get_project_total_tokens returns correct sum."""

    def test_returns_correct_sum(self):
        from orchestration.database import ReadOnlyDB

        ro, db_path = _make_readonly_db_with_schema()

        # Insert two rows: 100+50=150 and 200+80=280 → total = 430
        _insert_cost_row(db_path, "proj-alpha", 100, 50)
        _insert_cost_row(db_path, "proj-alpha", 200, 80)

        result = ro.get_project_total_tokens("proj-alpha")
        self.assertEqual(result, 430,
                         "get_project_total_tokens must sum prompt_tokens + completion_tokens")

        import os; os.unlink(db_path)


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-039: get_project_total_tokens — unknown project returns 0
# ─────────────────────────────────────────────────────────────────────────────

class TestGetProjectTotalTokensUnknown(unittest.TestCase):
    """Test 4: get_project_total_tokens returns 0 for unknown project_id."""

    def test_returns_zero_for_unknown_project(self):
        from orchestration.database import ReadOnlyDB

        ro, db_path = _make_readonly_db_with_schema()

        result = ro.get_project_total_tokens("non-existent-project-xyz")
        self.assertEqual(result, 0,
                         "get_project_total_tokens must return 0 when project has no records")

        import os; os.unlink(db_path)


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-039: get_project_total_tokens — graceful when table missing
# ─────────────────────────────────────────────────────────────────────────────

class TestGetProjectTotalTokensTableMissing(unittest.TestCase):
    """Test 5: get_project_total_tokens returns 0 when cost_tracking table doesn't exist."""

    def test_returns_zero_when_table_missing(self):
        from orchestration.database import ReadOnlyDB

        # Create a bare SQLite DB with NO schema (no cost_tracking table)
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        # Touch it so the file exists but has no tables
        conn = sqlite3.connect(tmp.name)
        conn.close()

        ro = ReadOnlyDB(tmp.name)
        result = ro.get_project_total_tokens("some-project")
        self.assertEqual(result, 0,
                         "get_project_total_tokens must return 0 gracefully when "
                         "cost_tracking table does not exist")

        import os; os.unlink(tmp.name)


if __name__ == "__main__":
    unittest.main()
