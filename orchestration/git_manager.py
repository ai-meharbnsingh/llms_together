"""
Git Workflow Manager — Autonomous Factory
══════════════════════════════════════════
Manages git operations for autonomous project execution.
Branch per phase → PR → merge to develop → main.
Conflicts → DaC tag SER → escalation.
══════════════════════════════════════════
"""

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from orchestration.database import queue_write

logger = logging.getLogger("factory.git_manager")


class GitError(Exception):
    """Raised on git operation failure."""
    pass


class GitManager:
    """
    Manages git workflow for autonomous project execution.

    Branch strategy:
    - main: production releases (tagged)
    - develop: integration branch
    - phase/{N}-{name}: per-phase work branches
    """

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        # asyncio.Lock serialises concurrent atomic_commit calls.
        # Multiple asyncio tasks may finish in the same wave; this prevents
        # git index.lock collisions (Risk M1).
        self._commit_lock = asyncio.Lock()

    def _run_git(self, *args, check: bool = True) -> str:
        """Run a git command in the project directory."""
        cmd = ["git"] + list(args)
        try:
            result = subprocess.run(
                cmd, cwd=str(self.project_path),
                capture_output=True, text=True, timeout=60,
            )
            if check and result.returncode != 0:
                raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            raise GitError(f"git {' '.join(args)} timed out (60s)")
        except FileNotFoundError:
            raise GitError("git not found in PATH")

    def _has_commits(self) -> bool:
        """Return True if the repo has at least one commit."""
        import subprocess as _sp
        r = _sp.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=str(self.project_path), capture_output=True, text=True
        )
        return r.returncode == 0 and r.stdout.strip() != "0"

    def _branch_exists(self, name: str) -> bool:
        import subprocess as _sp
        r = _sp.run(
            ["git", "branch", "--list", name],
            cwd=str(self.project_path), capture_output=True, text=True
        )
        return name in r.stdout

    def init_repo(self) -> bool:
        """Initialize git repo if not already initialized."""
        git_dir = self.project_path / ".git"
        already_exists = git_dir.exists()

        if already_exists and self._has_commits():
            # Fully initialised — just ensure develop branch exists
            if not self._branch_exists("develop"):
                try:
                    self._run_git("checkout", "-b", "develop")
                except GitError:
                    pass
            logger.info(f"Git repo already exists at {self.project_path}")
            return True

        # Either fresh init or repo with 0 commits — complete the setup
        if already_exists:
            logger.info("Git repo exists but has no commits — completing setup")

        try:
            if not already_exists:
                self._run_git("init")
                self._run_git("checkout", "-b", "main")

            # Create .gitignore
            gitignore = self.project_path / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text(
                    "# Factory artifacts\n"
                    "docs/planning/\n"
                    "docs/analysis/\n"
                    "*.protocol.md\n"
                    "__pycache__/\n"
                    "*.pyc\n"
                    ".env\n"
                    ".env.*\n"
                    "node_modules/\n"
                    ".DS_Store\n"
                    "*.log\n"
                    "venv/\n"
                    ".venv/\n"
                )

            self._run_git("add", "-A")
            self._run_git("commit", "-m", "Initial commit — project scaffold")

            # Create develop branch
            self._run_git("checkout", "-b", "develop")

            logger.info(f"Git repo initialized at {self.project_path}")
            return True
        except GitError as e:
            logger.error(f"Git init failed: {e}")
            return False

    def create_phase_branch(self, phase: int, name: str) -> str:
        """Create a branch for a phase. Returns branch name."""
        branch = f"phase/{phase}-{name.lower().replace(' ', '-')}"
        try:
            # Ensure we're on develop
            self._run_git("checkout", "develop")
            self._run_git("pull", "--rebase", check=False)  # May fail if no remote
            # Force-delete branch if it already exists (e.g. re-run scenario)
            try:
                self._run_git("branch", "-D", branch)
                logger.info(f"Deleted existing branch: {branch}")
            except GitError:
                pass  # Branch didn't exist, that's fine
            self._run_git("checkout", "-b", branch)
            logger.info(f"Created phase branch: {branch}")
            return branch
        except GitError as e:
            logger.error(f"Failed to create phase branch: {e}")
            raise

    async def pull_latest(self) -> bool:
        """
        Pull latest changes on current branch.

        Serialised via _commit_lock to prevent concurrent git pull calls from
        racing on the same repo within a parallel asyncio wave (FER-AF-038).
        """
        async with self._commit_lock:
            try:
                current = self._run_git("branch", "--show-current")
                self._run_git("pull", "--rebase", check=False)
                logger.info(f"Pulled latest on {current}")
                return True
            except GitError as e:
                logger.warning(f"Pull failed (may be local-only repo): {e}")
                return False

    async def atomic_commit(self, task_id: str, message: str,
                            files: List[str] = None) -> Optional[str]:
        """
        Create an atomic commit for a task.

        Serialised via asyncio.Lock to prevent git index.lock collisions
        when multiple tasks in the same wave finish concurrently (Risk M1).

        Args:
            task_id: Task ID (included in commit message per rule R002)
            message: Commit message
            files: Specific files to stage (None = stage all changes)

        Returns:
            Commit hash or None on failure.
        """
        async with self._commit_lock:
            staged = False
            try:
                if files:
                    for f in files:
                        self._run_git("add", f)
                else:
                    self._run_git("add", "-A")
                staged = True

                # Check if there are changes to commit
                status = self._run_git("status", "--porcelain")
                if not status:
                    logger.info(f"No changes to commit for {task_id}")
                    return None

                # Ensure task_id in message (rule R002)
                if task_id not in message:
                    message = f"[{task_id}] {message}"

                self._run_git("commit", "-m", message)
                commit_hash = self._run_git("rev-parse", "HEAD")

                # Log to DB
                self._queue_commit_record(task_id, commit_hash, files)

                logger.info(f"Committed {commit_hash[:8]} for {task_id}")
                return commit_hash

            except GitError as e:
                logger.error(f"Commit failed for {task_id}: {e}")
                # FER-AF-027: If git add already ran, unstage everything so the
                # git index is not left dirty (orphaned staged files).
                if staged:
                    try:
                        self._run_git("reset", "HEAD", "--")
                    except GitError:
                        logger.warning(f"Could not unstage files after commit failure for {task_id}")
                return None

    def check_conflicts(self, target_branch: str = "develop") -> List[str]:
        """Check for merge conflicts with target branch without actually merging."""
        try:
            current = self._run_git("branch", "--show-current")

            # Try merge with --no-commit --no-ff
            try:
                self._run_git("merge", "--no-commit", "--no-ff", target_branch)
                # No conflicts — abort the merge
                self._run_git("merge", "--abort")
                return []
            except GitError:
                # Conflicts detected
                conflicts_output = self._run_git("diff", "--name-only", "--diff-filter=U", check=False)
                self._run_git("merge", "--abort", check=False)

                conflicting_files = [f for f in conflicts_output.split('\n') if f.strip()]

                if conflicting_files:
                    self._queue_conflict_tag(current, target_branch, conflicting_files)

                return conflicting_files

        except GitError as e:
            logger.error(f"Conflict check failed: {e}")
            return []

    def merge_to_develop(self, phase_branch: str, message: str = None) -> bool:
        """Merge a phase branch into develop."""
        try:
            self._run_git("checkout", "develop")
            self._run_git("pull", "--rebase", check=False)

            if not message:
                message = f"Merge {phase_branch} into develop"

            self._run_git("merge", "--no-ff", phase_branch, "-m", message)
            logger.info(f"Merged {phase_branch} → develop")
            return True
        except GitError as e:
            logger.error(f"Merge failed: {e}")
            self._run_git("merge", "--abort", check=False)
            return False

    def merge_to_main(self, message: str = None) -> bool:
        """Merge develop into main (production release). Handles both 'main' and 'master'."""
        try:
            # Support both 'main' and 'master' naming conventions
            try:
                self._run_git("checkout", "main")
            except GitError:
                self._run_git("checkout", "master")
            self._run_git("pull", "--rebase", check=False)

            if not message:
                message = "Merge develop into main — production release"

            self._run_git("merge", "--no-ff", "develop", "-m", message)
            logger.info("Merged develop → main")
            return True
        except GitError as e:
            logger.error(f"Merge to main failed: {e}")
            self._run_git("merge", "--abort", check=False)
            return False

    def tag_version(self, version: str, message: str = None) -> bool:
        """Create a git tag (force-replace if it already exists — re-run safe)."""
        try:
            if not message:
                message = f"Release {version}"
            self._run_git("tag", "-fa", version, "-m", message)
            logger.info(f"Tagged: {version}")
            return True
        except GitError as e:
            logger.error(f"Tagging failed: {e}")
            return False

    def get_current_branch(self) -> str:
        """Get the current branch name."""
        try:
            return self._run_git("branch", "--show-current")
        except GitError:
            return "unknown"

    def verify_state(self) -> dict:
        """
        FER-AF-016 FIX: Verify git is in a safe state for phase execution.
        Returns {"ok": True} or {"ok": False, "issue": str}.
        Checks:
          1. Not in detached HEAD state (branch --show-current returns empty string)
        """
        try:
            branch = self._run_git("branch", "--show-current")
        except GitError as e:
            return {"ok": False, "issue": f"git branch query failed: {e}"}

        if not branch.strip():
            try:
                head_ref = self._run_git("rev-parse", "--short", "HEAD", check=False)
            except GitError:
                head_ref = "unknown"
            return {
                "ok": False,
                "issue": (
                    f"Repository is in detached HEAD state (at {head_ref.strip()}). "
                    "Checkout a branch before running a phase."
                ),
            }

        return {"ok": True, "branch": branch.strip()}

    def get_changed_files(self, since_branch: str = "develop") -> List[str]:
        """Get files changed between current branch and target."""
        try:
            output = self._run_git("diff", "--name-only", since_branch, check=False)
            return [f for f in output.split('\n') if f.strip()]
        except GitError:
            return []

    def get_log(self, count: int = 10) -> List[dict]:
        """Get recent commits as structured data."""
        try:
            output = self._run_git(
                "log", f"-{count}", "--format=%H|%s|%an|%ai"
            )
            commits = []
            for line in output.split('\n'):
                if '|' in line:
                    parts = line.split('|', 3)
                    commits.append({
                        "hash": parts[0],
                        "message": parts[1],
                        "author": parts[2],
                        "date": parts[3] if len(parts) > 3 else "",
                    })
            return commits
        except GitError:
            return []

    def checkout(self, branch: str) -> bool:
        """Checkout a branch."""
        try:
            self._run_git("checkout", branch)
            return True
        except GitError as e:
            logger.error(f"Checkout {branch} failed: {e}")
            return False

    def _queue_commit_record(self, task_id: str, commit_hash: str,
                              files: List[str] = None):
        """Queue a commit record to the DB via message bus."""
        try:
            branch = self.get_current_branch()
            queue_write(
                operation="insert", table="commits",
                params={
                    "task_id": task_id,
                    "git_commit_hash": commit_hash,
                    "branch": branch,
                    "files_changed": json.dumps(files) if files else None,
                    "conflict_detected": False,
                },
                requester="git_manager",
            )
        except RuntimeError as e:
            logger.error(f"Failed to queue commit record: {e}")

    def _queue_conflict_tag(self, source_branch: str, target_branch: str,
                             conflicting_files: List[str]):
        """Queue a SER DaC tag for merge conflicts."""
        try:
            queue_write(
                operation="insert", table="escalations",
                params={
                    "task_id": f"merge_{source_branch}",
                    "escalation_type": "merge_conflict",
                    "escalated_by": "git_manager",
                    "escalation_reason": (
                        f"Merge conflict: {source_branch} → {target_branch}. "
                        f"Conflicting files: {', '.join(conflicting_files)}"
                    ),
                    "context_data": json.dumps({
                        "source": source_branch,
                        "target": target_branch,
                        "files": conflicting_files,
                    }),
                    "status": "pending",
                },
                requester="git_manager",
            )
        except RuntimeError as e:
            logger.error(f"Failed to queue conflict escalation: {e}")
