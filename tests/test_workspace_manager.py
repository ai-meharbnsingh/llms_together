"""
Tests for WorkspaceManager — Git Worktree Domain Isolation
═══════════════════════════════════════════════════════════
Covers: setup, routing, sync, merge, commit, cleanup, fallback, config paths.
═══════════════════════════════════════════════════════════
"""

import asyncio
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestration.workspace_manager import (
    DOMAIN_ROUTES,
    DOMAINS_BY_TYPE,
    WorkspaceManager,
)


# ── Helpers ──────────────────────────────────────────────────────────────

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "t@t.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "t@t.com",
}


def _init_git_repo(path: str) -> None:
    """Create a git repo with initial commit + develop branch at `path`."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=path, capture_output=True, check=True)
    (Path(path) / "README.md").write_text("# Test project\n")
    (Path(path) / ".gitignore").write_text("__pycache__/\n")
    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=path, capture_output=True, check=True, env=_GIT_ENV,
    )
    subprocess.run(["git", "checkout", "-b", "develop"], cwd=path, capture_output=True, check=True)


def _run(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, env=_GIT_ENV,
    )


@pytest.fixture
def git_project(tmp_path):
    """Create a temp git repo with initial commit + develop branch."""
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    _init_git_repo(str(project_dir))
    yield str(project_dir)
    # Cleanup worktrees before removing temp dir (prevents git lock issues)
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=str(project_dir), capture_output=True, text=True,
    )
    for line in result.stdout.split("\n"):
        if line.startswith("worktree ") and ".autonomy" in line:
            wt_path = line[len("worktree "):].strip()
            subprocess.run(
                ["git", "worktree", "remove", wt_path, "--force"],
                cwd=str(project_dir), capture_output=True,
            )


# ── Test: Domain Routing Constants ───────────────────────────────────────


class TestDomainRouting:
    """Verify DOMAIN_ROUTES is ordered longest-prefix-first and complete."""

    def test_frontend_components_before_frontend(self):
        """frontend/components/ must match before frontend/."""
        routes = [r[0] for r in DOMAIN_ROUTES]
        assert routes.index("frontend/components/") < routes.index("frontend/")

    def test_all_project_types_have_backend(self):
        for ptype, domains in DOMAINS_BY_TYPE.items():
            assert "backend" in domains, f"{ptype} missing backend domain"

    def test_iot_has_firmware(self):
        assert "firmware" in DOMAINS_BY_TYPE["iot"]

    def test_web_does_not_have_firmware(self):
        assert "firmware" not in DOMAINS_BY_TYPE["web"]


# ── Test: setup_worktrees ────────────────────────────────────────────────


class TestSetupWorktrees:
    def test_creates_worktrees_for_web_project(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        result = mgr.setup_worktrees()

        expected_domains = {"backend", "frontend", "frontend-design", "database", "testing"}
        assert set(result.keys()) == expected_domains
        assert mgr.is_active()

        # Verify worktree directories exist on disk
        for domain, wt_path in result.items():
            assert Path(wt_path).exists(), f"Worktree dir missing: {wt_path}"

        # Verify git worktree list includes them
        wt_list = _run(git_project, "worktree", "list", "--porcelain").stdout
        for domain in expected_domains:
            assert f"autonomy/{domain}" in wt_list

    def test_creates_worktrees_for_iot_project(self, git_project):
        mgr = WorkspaceManager(git_project, "iot")
        result = mgr.setup_worktrees()
        assert "firmware" in result
        assert mgr.is_active()

    def test_idempotent_setup(self, git_project):
        """Calling setup_worktrees twice should not error or duplicate."""
        mgr = WorkspaceManager(git_project, "web")
        result1 = mgr.setup_worktrees()
        # Create a new manager instance (simulates crash recovery)
        mgr2 = WorkspaceManager(git_project, "web")
        result2 = mgr2.setup_worktrees()

        assert set(result1.keys()) == set(result2.keys())
        assert mgr2.is_active()

    def test_gitignore_updated(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()

        gitignore = Path(git_project) / ".gitignore"
        content = gitignore.read_text()
        assert ".autonomy/" in content

    def test_gitignore_not_duplicated(self, git_project):
        """Running setup twice shouldn't add .autonomy/ twice to gitignore."""
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()
        mgr2 = WorkspaceManager(git_project, "web")
        mgr2.setup_worktrees()

        gitignore = Path(git_project) / ".gitignore"
        content = gitignore.read_text()
        assert content.count(".autonomy/") == 1

    def test_unknown_project_type_falls_back_to_web(self, git_project):
        mgr = WorkspaceManager(git_project, "unknown_type")
        result = mgr.setup_worktrees()
        assert set(result.keys()) == set(DOMAINS_BY_TYPE["web"])


# ── Test: resolve_worktree ───────────────────────────────────────────────


class TestResolveWorktree:
    @pytest.fixture(autouse=True)
    def setup_mgr(self, git_project):
        self.mgr = WorkspaceManager(git_project, "web")
        self.mgr.setup_worktrees()
        self.project_path = git_project

    def test_backend_module(self):
        domain, path = self.mgr.resolve_worktree({"module": "backend/api/routes.py"})
        assert domain == "backend"
        assert ".autonomy/backend" in path

    def test_api_module_routes_to_backend(self):
        domain, path = self.mgr.resolve_worktree({"module": "api/endpoints.py"})
        assert domain == "backend"

    def test_frontend_module(self):
        domain, path = self.mgr.resolve_worktree({"module": "frontend/App.tsx"})
        assert domain == "frontend"

    def test_frontend_components_routes_to_design(self):
        domain, path = self.mgr.resolve_worktree({"module": "frontend/components/Button.tsx"})
        assert domain == "frontend-design"

    def test_database_module(self):
        domain, path = self.mgr.resolve_worktree({"module": "database/models.py"})
        assert domain == "database"

    def test_migrations_routes_to_database(self):
        domain, path = self.mgr.resolve_worktree({"module": "migrations/001_init.sql"})
        assert domain == "database"

    def test_tests_module(self):
        domain, path = self.mgr.resolve_worktree({"module": "tests/test_api.py"})
        assert domain == "testing"

    def test_fallback_to_backend(self):
        domain, path = self.mgr.resolve_worktree({"module": "unknown/thing.py"})
        assert domain == "backend"

    def test_empty_module_falls_back(self):
        domain, path = self.mgr.resolve_worktree({"module": ""})
        assert domain == "backend"

    def test_none_module_falls_back(self):
        domain, path = self.mgr.resolve_worktree({})
        assert domain == "backend"

    def test_file_path_fallback(self):
        domain, path = self.mgr.resolve_worktree({
            "module": "",
            "file_path": "database/schema.sql",
        })
        assert domain == "database"

    def test_description_keyword_frontend(self):
        domain, path = self.mgr.resolve_worktree({
            "description": "Build the frontend login form with React components",
        })
        assert domain == "frontend"

    def test_description_keyword_database(self):
        domain, path = self.mgr.resolve_worktree({
            "description": "Create database migration for users table",
        })
        assert domain == "database"

    def test_description_keyword_testing(self):
        domain, path = self.mgr.resolve_worktree({
            "description": "Write e2e test for checkout flow",
        })
        assert domain == "testing"

    def test_inactive_manager_returns_project_path(self):
        mgr = WorkspaceManager(self.project_path, "web")
        domain, path = mgr.resolve_worktree({"module": "frontend/App.tsx"})
        assert domain == "backend"
        assert path == self.project_path

    def test_windows_backslash_module(self):
        domain, path = self.mgr.resolve_worktree({"module": "frontend\\components\\Button.tsx"})
        assert domain == "frontend-design"


# ── Test: sync_from_develop ──────────────────────────────────────────────


class TestSyncFromDevelop:
    @pytest.mark.asyncio
    async def test_sync_success(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()

        # Add a commit on develop
        _run(git_project, "checkout", "develop")
        (Path(git_project) / "new_file.txt").write_text("hello")
        _run(git_project, "add", "-A")
        _run(git_project, "commit", "-m", "New file on develop")
        # Switch back to phase branch (simulate _phase_build)
        _run(git_project, "checkout", "-b", "phase/1-build")

        result = await mgr.sync_from_develop("backend")
        assert result is True

        # Verify the file exists in the worktree now
        backend_wt = mgr._worktrees["backend"]
        assert (Path(backend_wt) / "new_file.txt").exists()

    @pytest.mark.asyncio
    async def test_sync_nonexistent_domain_returns_true(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()
        result = await mgr.sync_from_develop("nonexistent_domain")
        assert result is True


# ── Test: commit_in_worktree ─────────────────────────────────────────────


class TestCommitInWorktree:
    @pytest.mark.asyncio
    async def test_commit_with_changes(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()

        backend_wt = mgr._worktrees["backend"]
        (Path(backend_wt) / "routes.py").write_text("# API routes\n")

        commit_hash = await mgr.commit_in_worktree("backend", "task-001", "Add routes")
        assert commit_hash is not None
        assert len(commit_hash) >= 7

        log = _run(backend_wt, "log", "-1", "--format=%s").stdout.strip()
        assert "task-001" in log

    @pytest.mark.asyncio
    async def test_commit_no_changes_returns_none(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()

        commit_hash = await mgr.commit_in_worktree("backend", "task-002", "No-op")
        assert commit_hash is None

    @pytest.mark.asyncio
    async def test_commit_nonexistent_domain_returns_none(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()

        commit_hash = await mgr.commit_in_worktree("nonexistent", "task-003", "Nope")
        assert commit_hash is None

    @pytest.mark.asyncio
    async def test_task_id_not_duplicated_in_message(self, git_project):
        """If task_id is already in the message, don't add it again."""
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()

        backend_wt = mgr._worktrees["backend"]
        (Path(backend_wt) / "file.py").write_text("# new\n")

        commit_hash = await mgr.commit_in_worktree(
            "backend", "task-004", "[task-004] Already has id"
        )
        log = _run(backend_wt, "log", "-1", "--format=%s").stdout.strip()
        assert log.count("task-004") == 1


# ── Test: merge_to_develop ───────────────────────────────────────────────


class TestMergeToDevelop:
    @pytest.mark.asyncio
    async def test_merge_success(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()

        # Simulate: create phase branch (as _phase_build does)
        _run(git_project, "checkout", "-b", "phase/1-test")

        # Write + commit in backend worktree
        backend_wt = mgr._worktrees["backend"]
        (Path(backend_wt) / "api.py").write_text("# API\n")
        _run(backend_wt, "add", "-A")
        _run(backend_wt, "commit", "-m", "[task-010] Add api.py")

        result = await mgr.merge_to_develop("backend", "task-010")
        assert result is True

        # Verify the file is on develop
        _run(git_project, "checkout", "develop")
        assert (Path(git_project) / "api.py").exists()

    @pytest.mark.asyncio
    async def test_merge_preserves_original_branch(self, git_project):
        """After merge, main repo must be back on the phase branch, not develop."""
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()

        _run(git_project, "checkout", "-b", "phase/1-preserve")

        frontend_wt = mgr._worktrees["frontend"]
        (Path(frontend_wt) / "App.tsx").write_text("// App\n")
        _run(frontend_wt, "add", "-A")
        _run(frontend_wt, "commit", "-m", "[task-011] Add App.tsx")

        await mgr.merge_to_develop("frontend", "task-011")

        current = _run(git_project, "branch", "--show-current").stdout.strip()
        assert current == "phase/1-preserve", (
            f"Expected phase/1-preserve but got {current}"
        )

    @pytest.mark.asyncio
    async def test_merge_nonexistent_domain_returns_true(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()
        result = await mgr.merge_to_develop("nonexistent", "task-012")
        assert result is True


# ── Test: cleanup_worktrees ──────────────────────────────────────────────


class TestCleanupWorktrees:
    def test_cleanup_removes_all_worktrees(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()
        assert mgr.is_active()

        mgr.cleanup_worktrees()

        assert not mgr.is_active()
        assert len(mgr._worktrees) == 0

        wt_list = _run(git_project, "worktree", "list").stdout
        assert ".autonomy" not in wt_list

    def test_cleanup_idempotent(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()
        mgr.cleanup_worktrees()
        mgr.cleanup_worktrees()
        assert not mgr.is_active()

    def test_cleanup_removes_branches(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()
        mgr.cleanup_worktrees()

        branches = _run(git_project, "branch").stdout
        assert "autonomy/" not in branches


# ── Test: is_active ──────────────────────────────────────────────────────


class TestIsActive:
    def test_inactive_before_setup(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        assert not mgr.is_active()

    def test_active_after_setup(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()
        assert mgr.is_active()

    def test_inactive_after_cleanup(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()
        mgr.cleanup_worktrees()
        assert not mgr.is_active()


# ── Test: Orchestrator Integration Paths ─────────────────────────────────


class TestOrchestratorIntegration:
    def test_config_disabled_skips_worktrees(self):
        config = {"workspaces": {"enabled": False, "fallback_on_failure": True}}
        ws_config = config.get("workspaces", {})
        workspace_mgr = None
        if ws_config.get("enabled", False):
            workspace_mgr = WorkspaceManager("/tmp/fake", "web")
        assert workspace_mgr is None

    def test_config_missing_workspaces_section(self):
        config = {"factory": {"version": "1.0"}}
        ws_config = config.get("workspaces", {})
        assert ws_config.get("enabled", False) is False

    def test_fallback_on_setup_failure(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        with patch.object(mgr, "_run_git_main", side_effect=RuntimeError("git broken")):
            result = mgr.setup_worktrees()
        assert result == {}
        assert not mgr.is_active()

    @pytest.mark.asyncio
    async def test_guarded_sync_failure_returns_failed_result(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()

        task = {"task_id": "task-100", "module": "backend/routes.py"}
        task_domain, effective_path = mgr.resolve_worktree(task)

        async def mock_sync(domain):
            return False

        with patch.object(mgr, "sync_from_develop", side_effect=mock_sync):
            sync_ok = await mgr.sync_from_develop(task_domain)
        assert sync_ok is False

        result = {
            "success": False,
            "task_id": task["task_id"],
            "files": 0,
            "tdd": False,
            "gate": None,
            "error": f"Worktree sync conflict in {task_domain}",
        }
        assert result["success"] is False
        assert "backend" in result["error"]


# ── Test: Edge Cases ─────────────────────────────────────────────────────


class TestEdgeCases:
    def test_module_without_slash(self):
        mgr = WorkspaceManager("/tmp/fake", "web")
        mgr._worktrees = {"backend": "/tmp/fake/.autonomy/backend"}
        mgr._active = True
        domain, path = mgr.resolve_worktree({"module": "backend"})
        assert domain == "backend"

    def test_module_with_deep_path(self):
        mgr = WorkspaceManager("/tmp/fake", "web")
        mgr._worktrees = {
            "frontend-design": "/tmp/fake/.autonomy/frontend-design",
            "frontend": "/tmp/fake/.autonomy/frontend",
        }
        mgr._active = True
        domain, _ = mgr.resolve_worktree({"module": "frontend/components/ui/Button.tsx"})
        assert domain == "frontend-design"

    def test_security_domain_routes_when_available(self, git_project):
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()
        domain, _ = mgr.resolve_worktree({"module": "security/auth.py"})
        assert domain == "backend"

    @pytest.mark.asyncio
    async def test_concurrent_commits_in_different_domains(self, git_project):
        """Two domains can commit concurrently without conflict."""
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()

        backend_wt = mgr._worktrees["backend"]
        frontend_wt = mgr._worktrees["frontend"]
        (Path(backend_wt) / "server.py").write_text("# server\n")
        (Path(frontend_wt) / "app.js").write_text("// app\n")

        h1 = await mgr.commit_in_worktree("backend", "task-A", "Backend work")
        h2 = await mgr.commit_in_worktree("frontend", "task-B", "Frontend work")

        assert h1 is not None
        assert h2 is not None
        assert h1 != h2

    @pytest.mark.asyncio
    async def test_full_lifecycle_commit_merge_cleanup(self, git_project):
        """Full lifecycle: setup → commit → merge → cleanup."""
        mgr = WorkspaceManager(git_project, "web")
        mgr.setup_worktrees()

        # Create phase branch (mimics _phase_build)
        _run(git_project, "checkout", "-b", "phase/1-full")

        # Write in backend
        backend_wt = mgr._worktrees["backend"]
        (Path(backend_wt) / "main.py").write_text("# main\n")

        # Commit
        h = await mgr.commit_in_worktree("backend", "task-50", "Add main.py")
        assert h is not None

        # Merge
        ok = await mgr.merge_to_develop("backend", "task-50")
        assert ok is True

        # Verify branch preserved
        current = _run(git_project, "branch", "--show-current").stdout.strip()
        assert current == "phase/1-full"

        # Verify file on develop
        _run(git_project, "stash")
        _run(git_project, "checkout", "develop")
        assert (Path(git_project) / "main.py").exists()
        _run(git_project, "checkout", "phase/1-full")

        # Cleanup
        mgr.cleanup_worktrees()
        assert not mgr.is_active()
        wt_list = _run(git_project, "worktree", "list").stdout
        assert ".autonomy" not in wt_list
