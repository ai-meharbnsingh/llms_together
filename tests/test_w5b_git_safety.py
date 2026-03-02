"""
Wave 5b Tests — FER-AF-038 (parallel pull races) + FER-AF-027 (orphaned files on commit fail)
RED phase: these tests define expected behaviour and must FAIL before the fix is applied.

FER-AF-038: pull_latest() must be async and protected by a lock so concurrent pulls
            in the same asyncio wave never overlap.

FER-AF-027: atomic_commit() must call git reset on exception after git add, so the
            git index is left clean even when commit fails.
"""
import asyncio
import inspect
import sys
import os
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─── FER-AF-038: pull_latest must be async and serialised ──────────────────

class TestPullLatestLock:
    """pull_latest() must be async and protected against concurrent execution."""

    def test_pull_latest_is_coroutine(self):
        """pull_latest must be async def so it can acquire the lock."""
        from orchestration.git_manager import GitManager
        assert inspect.iscoroutinefunction(GitManager.pull_latest), (
            "pull_latest must be async def to serialise concurrent pulls (FER-AF-038)"
        )

    def test_pull_latest_protected_by_commit_lock(self):
        """
        pull_latest() must hold _commit_lock while running git commands.
        We verify this by checking that _commit_lock is locked during the call.
        """
        from orchestration.git_manager import GitManager

        gm = GitManager("/tmp/fake_project")
        lock_was_locked_during_pull = []

        async def _run():
            original_run_git = gm._run_git

            def fake_run_git(*args, **kwargs):
                # When _run_git is called from inside pull_latest, check lock state
                lock_was_locked_during_pull.append(gm._commit_lock.locked())
                return ""

            gm._run_git = fake_run_git
            await gm.pull_latest()

        asyncio.run(_run())

        assert len(lock_was_locked_during_pull) > 0, (
            "pull_latest did not call _run_git at all"
        )
        assert all(lock_was_locked_during_pull), (
            "pull_latest must hold _commit_lock while executing git commands (FER-AF-038). "
            f"Lock states during _run_git calls: {lock_was_locked_during_pull}"
        )

    def test_concurrent_pulls_serialised(self):
        """
        Three concurrent pull_latest() calls must not overlap inside the git section.
        At no point should more than one caller be executing _run_git simultaneously.
        """
        from orchestration.git_manager import GitManager

        gm = GitManager("/tmp/fake_project")
        concurrent_inside = []

        async def _run():
            inside = []

            def fake_run_git(*args, **kwargs):
                # Track concurrent depth inside _run_git across coroutines
                inside.append(1)
                concurrent_inside.append(len(inside))
                inside.pop()
                return ""

            gm._run_git = fake_run_git

            async def pull_task():
                await gm.pull_latest()

            await asyncio.gather(
                pull_task(),
                pull_task(),
                pull_task(),
            )

        asyncio.run(_run())

        assert len(concurrent_inside) > 0, "pull_latest never called _run_git"
        assert all(c <= 1 for c in concurrent_inside), (
            f"FER-AF-038: concurrent pull depth exceeded 1 — "
            f"concurrent_inside counts: {concurrent_inside}. "
            "pull_latest must serialise via _commit_lock."
        )


# ─── FER-AF-027: atomic_commit must unstage on exception ───────────────────

class TestAtomicCommitUnstagesOnFailure:
    """
    If git commit fails after git add, atomic_commit must call
    'git reset HEAD --' to unstage changes before re-raising / returning.
    """

    def test_atomic_commit_calls_reset_after_commit_failure(self):
        """
        Mock _run_git to succeed on 'add' and 'status' but raise GitError on 'commit'.
        Verify that 'reset' is called with 'HEAD' and '--' as part of cleanup.
        """
        from orchestration.git_manager import GitManager, GitError

        gm = GitManager("/tmp/fake_project")
        calls_made = []

        def fake_run_git(*args, **kwargs):
            calls_made.append(args)
            if args[0] == "commit":
                raise GitError("simulated commit failure")
            if args[0] == "status":
                return "M some_file.py"  # pretend there are staged changes
            return ""

        gm._run_git = fake_run_git

        async def _run():
            result = await gm.atomic_commit("task_001", "test commit message")
            return result

        result = asyncio.run(_run())

        # Verify reset was called
        reset_calls = [c for c in calls_made if c[0] == "reset"]
        assert len(reset_calls) > 0, (
            "FER-AF-027: atomic_commit did not call 'git reset' after commit failure. "
            "Staged files were orphaned in the git index."
        )

        # Verify the reset call uses HEAD and -- to unstage everything
        reset_args = reset_calls[0]
        assert "HEAD" in reset_args, (
            f"FER-AF-027: git reset call missing 'HEAD': got args {reset_args}"
        )
        assert "--" in reset_args, (
            f"FER-AF-027: git reset call missing '--': got args {reset_args}"
        )

    def test_atomic_commit_returns_none_after_commit_failure(self):
        """
        Even after the reset cleanup, atomic_commit must return None
        (not raise) so callers don't crash.
        """
        from orchestration.git_manager import GitManager, GitError

        gm = GitManager("/tmp/fake_project")

        def fake_run_git(*args, **kwargs):
            if args[0] == "commit":
                raise GitError("simulated commit failure")
            if args[0] == "status":
                return "M some_file.py"
            return ""

        gm._run_git = fake_run_git

        async def _run():
            return await gm.atomic_commit("task_001", "test commit message")

        result = asyncio.run(_run())
        assert result is None, (
            f"FER-AF-027: atomic_commit should return None on failure, got {result!r}"
        )

    def test_atomic_commit_no_reset_when_add_not_called(self):
        """
        If _run_git is never called with 'add' (e.g. no files argument and add fails
        at the very first step), the reset is still acceptable — but must not
        cause a secondary exception.
        """
        from orchestration.git_manager import GitManager, GitError

        gm = GitManager("/tmp/fake_project")

        def fake_run_git(*args, **kwargs):
            if args[0] == "add":
                raise GitError("nothing to add")
            return ""

        gm._run_git = fake_run_git

        async def _run():
            # Should not raise — must return None gracefully
            return await gm.atomic_commit("task_002", "fail at add stage")

        result = asyncio.run(_run())
        # Just confirm it doesn't blow up; return value is None
        assert result is None
