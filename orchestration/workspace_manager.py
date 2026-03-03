"""
Workspace Manager — Git Worktree Domain Isolation
══════════════════════════════════════════════════
Physical domain isolation via git worktrees so backend, frontend, database,
etc. each get their own working copy. Changes merge to develop only after
quality gate approval. Enables true parallel execution with zero file conflicts.

Disk layout:
    project_root/                      ← develop branch (blessed)
    ├── .autonomy/                     ← gitignored
    │   ├── backend/                   ← worktree: autonomy/backend
    │   ├── frontend/                  ← worktree: autonomy/frontend
    │   ├── frontend-design/           ← worktree: autonomy/frontend-design
    │   ├── database/                  ← worktree: autonomy/database
    │   └── testing/                   ← worktree: autonomy/testing
══════════════════════════════════════════════════
"""

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("factory.workspace_manager")

# Domain routing rules — longest prefix first (matched top-down)
DOMAIN_ROUTES: List[Tuple[str, str]] = [
    ("frontend/components/", "frontend-design"),
    ("frontend/",           "frontend"),
    ("backend/",            "backend"),
    ("api/",                "backend"),
    ("database/",           "database"),
    ("migrations/",         "database"),
    ("firmware/",           "firmware"),
    ("security/",           "security"),
    ("tests/",              "testing"),
]

# Domains by project type
DOMAINS_BY_TYPE: Dict[str, List[str]] = {
    "web":    ["backend", "frontend", "frontend-design", "database", "testing"],
    "iot":    ["backend", "frontend", "frontend-design", "database", "testing", "firmware"],
    "plm":    ["backend", "frontend", "frontend-design", "database", "testing"],
    "mobile": ["backend", "frontend", "frontend-design", "database", "testing"],
}


class WorkspaceManager:
    """
    Manages git worktree-based domain isolation for parallel task execution.

    Each domain (backend, frontend, database, etc.) gets its own worktree
    under .autonomy/<domain>. Tasks are routed to the appropriate worktree
    based on their module prefix. Merges back to develop happen after
    quality gate approval.
    """

    def __init__(self, project_path: str, project_type: str = "web"):
        self.project_path = Path(project_path)
        self.project_type = project_type
        self.domains = DOMAINS_BY_TYPE.get(project_type, DOMAINS_BY_TYPE["web"])
        self.autonomy_dir = self.project_path / ".autonomy"

        # Per-domain locks for sync/commit within one worktree
        self._domain_locks: Dict[str, asyncio.Lock] = {}
        # Global merge lock — one merge-to-develop at a time
        self._merge_lock = asyncio.Lock()
        # Maps domain → worktree path
        self._worktrees: Dict[str, str] = {}
        self._active = False

    def is_active(self) -> bool:
        """Return True if worktrees are set up and operational."""
        return self._active

    def setup_worktrees(self) -> Dict[str, str]:
        """
        Create .autonomy/<domain> worktrees. Idempotent — checks existing dirs.

        Returns:
            Dict mapping domain name → worktree absolute path.

        Raises:
            RuntimeError if git worktree operations fail unrecoverably.
        """
        # Ensure .autonomy dir exists
        self.autonomy_dir.mkdir(parents=True, exist_ok=True)

        # Ensure .autonomy/ is gitignored
        self._ensure_gitignored()

        # Check existing worktrees so we're idempotent
        existing = self._list_existing_worktrees()

        for domain in self.domains:
            worktree_path = self.autonomy_dir / domain
            branch_name = f"autonomy/{domain}"

            if str(worktree_path) in existing:
                logger.info(f"Worktree already exists: {domain} → {worktree_path}")
                self._worktrees[domain] = str(worktree_path)
                self._domain_locks[domain] = asyncio.Lock()
                continue

            try:
                # Create the branch from develop (or current HEAD) if it doesn't exist
                if not self._branch_exists(branch_name):
                    self._run_git_main("branch", branch_name, "HEAD")

                # Create worktree
                self._run_git_main(
                    "worktree", "add", str(worktree_path), branch_name
                )

                self._worktrees[domain] = str(worktree_path)
                self._domain_locks[domain] = asyncio.Lock()
                logger.info(f"Created worktree: {domain} → {worktree_path}")

            except Exception as e:
                logger.error(f"Failed to create worktree for {domain}: {e}")
                # Continue with other domains — partial setup is OK

        if self._worktrees:
            self._active = True
            logger.info(
                f"Worktree isolation active: {list(self._worktrees.keys())}"
            )
        else:
            logger.warning("No worktrees created — falling back to single-path mode")

        return dict(self._worktrees)

    def resolve_worktree(self, task: dict) -> Tuple[str, str]:
        """
        Route a task to the correct domain worktree.

        Args:
            task: Task dict with 'module', 'description', 'file_path' fields.

        Returns:
            (domain, worktree_path) tuple. Falls back to ("backend", project_path)
            if no match or worktrees not active.
        """
        if not self._active:
            return ("backend", str(self.project_path))

        # Try module field first (most reliable)
        module = task.get("module", "") or ""

        # Normalize to forward slashes
        module = module.replace("\\", "/")
        if not module.endswith("/"):
            module_with_slash = module + "/"
        else:
            module_with_slash = module

        # Longest prefix match
        for prefix, domain in DOMAIN_ROUTES:
            if module_with_slash.startswith(prefix) or module.startswith(prefix.rstrip("/")):
                if domain in self._worktrees:
                    return (domain, self._worktrees[domain])

        # Try file_path as secondary signal
        file_path = task.get("file_path", "") or ""
        file_path = file_path.replace("\\", "/")
        for prefix, domain in DOMAIN_ROUTES:
            if file_path.startswith(prefix):
                if domain in self._worktrees:
                    return (domain, self._worktrees[domain])

        # Try description keywords as last resort
        desc = (task.get("description", "") or "").lower()
        if any(kw in desc for kw in ["frontend", "ui ", "component", "react", "html", "css"]):
            if "frontend" in self._worktrees:
                return ("frontend", self._worktrees["frontend"])
        if any(kw in desc for kw in ["database", "migration", "schema", "sql", "table"]):
            if "database" in self._worktrees:
                return ("database", self._worktrees["database"])
        if any(kw in desc for kw in ["test", "spec", "e2e"]):
            if "testing" in self._worktrees:
                return ("testing", self._worktrees["testing"])
        if any(kw in desc for kw in ["firmware", "esp32", "mqtt", "sensor"]):
            if "firmware" in self._worktrees:
                return ("firmware", self._worktrees["firmware"])

        # Default fallback — backend worktree or project root
        if "backend" in self._worktrees:
            return ("backend", self._worktrees["backend"])

        return ("backend", str(self.project_path))

    async def sync_from_develop(self, domain: str) -> bool:
        """
        Merge develop → worktree branch to pick up latest changes.

        Uses per-domain lock so concurrent tasks in same domain don't collide.

        Returns:
            True if sync succeeded, False if merge conflict.
        """
        if domain not in self._worktrees:
            return True

        lock = self._domain_locks.get(domain)
        if not lock:
            return True

        async with lock:
            worktree_path = self._worktrees[domain]
            try:
                # Merge develop into this worktree's branch
                self._run_git_at(worktree_path, "merge", "develop", "--no-edit")
                logger.debug(f"Synced develop → {domain}")
                return True
            except Exception as e:
                err_str = str(e)
                if "CONFLICT" in err_str or "conflict" in err_str:
                    logger.warning(f"Merge conflict syncing develop → {domain}: {e}")
                    # Abort the merge to leave worktree clean
                    try:
                        self._run_git_at(worktree_path, "merge", "--abort")
                    except Exception:
                        pass
                    return False
                # Any other git error is also a sync failure — don't let
                # a task proceed with stale code.
                logger.warning(f"Sync develop → {domain} failed: {e}")
                return False

    async def merge_to_develop(self, domain: str, task_id: str) -> bool:
        """
        Merge worktree branch → develop after quality gate approval.

        Uses global merge lock so only one domain merges at a time.

        Args:
            domain: The domain name.
            task_id: Task ID for commit message context.

        Returns:
            True if merge succeeded, False if conflict.
        """
        if domain not in self._worktrees:
            return True

        branch_name = f"autonomy/{domain}"

        async with self._merge_lock:
            # Save current branch so we can restore it after merge.
            # _phase_build puts the main repo on a phase branch; we must not
            # leave it on develop or the end-of-phase merge workflow breaks.
            original_branch = self._run_git_main(
                "branch", "--show-current", check=False
            )
            try:
                # Checkout develop in main repo
                self._run_git_main("checkout", "develop")

                # Merge the domain branch
                self._run_git_main(
                    "merge", "--no-ff", branch_name,
                    "-m", f"[{task_id}] Merge {domain} → develop"
                )

                logger.info(f"Merged {domain} → develop (task {task_id})")
                return True

            except Exception as e:
                err_str = str(e)
                if "CONFLICT" in err_str or "conflict" in err_str:
                    logger.error(
                        f"Merge conflict: {domain} → develop (task {task_id}): {e}"
                    )
                    try:
                        self._run_git_main("merge", "--abort")
                    except Exception:
                        pass
                    return False
                logger.error(f"Merge {domain} → develop failed: {e}")
                try:
                    self._run_git_main("merge", "--abort")
                except Exception:
                    pass
                return False
            finally:
                # Restore original branch so _phase_build's end-of-phase
                # merge (check_conflicts + merge_to_develop) still works.
                if original_branch and original_branch.strip() != "develop":
                    try:
                        self._run_git_main("checkout", original_branch.strip())
                    except Exception as _restore_err:
                        logger.warning(
                            f"Could not restore branch {original_branch} "
                            f"after merge: {_restore_err}"
                        )

    async def commit_in_worktree(
        self, domain: str, task_id: str, message: str
    ) -> Optional[str]:
        """
        Stage all changes and commit in the specified domain worktree.

        Returns:
            Commit hash, or None if no changes / error.
        """
        if domain not in self._worktrees:
            return None

        lock = self._domain_locks.get(domain)
        if not lock:
            return None

        async with lock:
            worktree_path = self._worktrees[domain]
            try:
                # Stage all changes
                self._run_git_at(worktree_path, "add", "-A")

                # Check for changes
                status = self._run_git_at(worktree_path, "status", "--porcelain")
                if not status:
                    logger.info(f"No changes to commit in {domain} for {task_id}")
                    return None

                # Ensure task_id in message
                if task_id not in message:
                    message = f"[{task_id}] {message}"

                self._run_git_at(worktree_path, "commit", "-m", message)
                commit_hash = self._run_git_at(
                    worktree_path, "rev-parse", "HEAD"
                )

                logger.info(
                    f"Committed {commit_hash[:8]} in {domain} for {task_id}"
                )
                return commit_hash

            except Exception as e:
                logger.error(f"Commit failed in {domain} for {task_id}: {e}")
                # Reset staged changes on failure
                try:
                    self._run_git_at(worktree_path, "reset", "HEAD", "--")
                except Exception:
                    pass
                return None

    def cleanup_worktrees(self):
        """Remove all .autonomy worktrees. Safe to call multiple times."""
        for domain, wt_path in list(self._worktrees.items()):
            try:
                self._run_git_main("worktree", "remove", wt_path, "--force")
                logger.info(f"Removed worktree: {domain}")
            except Exception as e:
                logger.warning(f"Failed to remove worktree {domain}: {e}")

            # Also delete the branch
            branch_name = f"autonomy/{domain}"
            try:
                self._run_git_main("branch", "-D", branch_name)
            except Exception:
                pass

        self._worktrees.clear()
        self._active = False

        # Remove .autonomy dir if empty
        try:
            if self.autonomy_dir.exists():
                # Only rmdir if empty (worktree remove should have cleaned files)
                remaining = list(self.autonomy_dir.iterdir())
                if not remaining:
                    self.autonomy_dir.rmdir()
        except Exception:
            pass

        logger.info("Worktree cleanup complete")

    # ── Internal helpers ─────────────────────────────────────────────

    def _run_git_main(self, *args, check: bool = True) -> str:
        """Run git command at the main project directory."""
        return self._run_git_at(str(self.project_path), *args, check=check)

    def _run_git_at(self, cwd: str, *args, check: bool = True) -> str:
        """Run git command at a specific directory."""
        cmd = ["git"] + list(args)
        try:
            result = subprocess.run(
                cmd, cwd=str(cwd),
                capture_output=True, text=True, timeout=60,
            )
            if check and result.returncode != 0:
                raise RuntimeError(
                    f"git {' '.join(args)} failed at {cwd}: {result.stderr.strip()}"
                )
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"git {' '.join(args)} timed out at {cwd}")

    def _branch_exists(self, name: str) -> bool:
        """Check if a git branch exists."""
        result = subprocess.run(
            ["git", "branch", "--list", name],
            cwd=str(self.project_path), capture_output=True, text=True,
        )
        return name in result.stdout

    def _list_existing_worktrees(self) -> set:
        """Return set of existing worktree paths."""
        try:
            output = self._run_git_main("worktree", "list", "--porcelain")
            paths = set()
            for line in output.split("\n"):
                if line.startswith("worktree "):
                    paths.add(line[len("worktree "):].strip())
            return paths
        except Exception:
            return set()

    def _ensure_gitignored(self):
        """Add .autonomy/ to .gitignore if not already present."""
        gitignore = self.project_path / ".gitignore"
        marker = ".autonomy/"

        if gitignore.exists():
            content = gitignore.read_text()
            if marker in content:
                return
            # Append
            if not content.endswith("\n"):
                content += "\n"
            content += f"# Worktree isolation\n{marker}\n"
            gitignore.write_text(content)
        else:
            gitignore.write_text(f"# Worktree isolation\n{marker}\n")

        logger.debug(f"Added {marker} to .gitignore")
