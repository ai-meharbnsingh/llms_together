"""
Wave 5a — FER-AF-002: requirements.txt completeness
=====================================================
Verifies that every third-party package directly imported by the codebase
(orchestration/, workers/, dashboard/, root *.py, scripts/) is:

  1. Importable in the current environment (import test)
  2. Listed in requirements.txt (presence test)
  3. CLI tools (flake8, bandit, pip-audit) are on PATH (shutil.which)

Rules:
- Tests are ground truth — never modify this file to fix failures.
- Fix requirements.txt (source) to make RED → GREEN.
"""

import importlib
import re
import shutil
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE = Path(__file__).parent.parent  # autonomous_factory/
REQUIREMENTS = BASE / "requirements.txt"


def _load_requirements() -> set[str]:
    """Return the set of normalised package names listed in requirements.txt."""
    if not REQUIREMENTS.exists():
        return set()
    names = set()
    for line in REQUIREMENTS.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip version specifiers: aiohttp>=3.9 → aiohttp
        name = re.split(r"[>=<!;\[#]", line)[0].strip().lower().replace("-", "_")
        if name:
            names.add(name)
    return names


LISTED = _load_requirements()


def assert_in_requirements(pkg_name: str) -> None:
    """Assert normalised pkg_name appears in requirements.txt."""
    normalised = pkg_name.lower().replace("-", "_")
    assert normalised in LISTED, (
        f"'{pkg_name}' is directly imported by the codebase but is NOT listed "
        f"in requirements.txt. Add it with an appropriate >=version pin."
    )


# ---------------------------------------------------------------------------
# 1. Import tests — every directly-imported third-party package
# ---------------------------------------------------------------------------

class TestPackagesImportable:
    """Each package must import without error."""

    def test_aiohttp_importable(self):
        """aiohttp — used in workers/adapters.py, dashboard/dashboard_server.py,
        orchestration/phi3_manager.py, scripts/trigger_phi3_summarize.py."""
        mod = importlib.import_module("aiohttp")
        assert mod is not None

    def test_requests_importable(self):
        """requests — used in scripts/model_conversation.py."""
        mod = importlib.import_module("requests")
        assert mod is not None

    def test_pytest_importable(self):
        """pytest — used in all test files."""
        mod = importlib.import_module("pytest")
        assert mod is not None

    def test_pytest_asyncio_importable(self):
        """pytest-asyncio — async test infrastructure."""
        mod = importlib.import_module("pytest_asyncio")
        assert mod is not None

    def test_pytest_cov_importable(self):
        """pytest-cov — coverage plugin."""
        mod = importlib.import_module("pytest_cov")
        assert mod is not None

    def test_playwright_importable(self):
        """playwright — used in run_demo.py and scripts/test_sessions_playwright.py."""
        mod = importlib.import_module("playwright")
        assert mod is not None

    def test_pytest_playwright_importable(self):
        """pytest-playwright — Playwright integration for pytest."""
        mod = importlib.import_module("pytest_playwright")
        assert mod is not None

    def test_flake8_importable(self):
        """flake8 — invoked via subprocess in orchestration/static_analysis.py (BC step)."""
        mod = importlib.import_module("flake8")
        assert mod is not None

    def test_bandit_importable(self):
        """bandit — invoked via subprocess in orchestration/static_analysis.py (SEA/DS steps)."""
        mod = importlib.import_module("bandit")
        assert mod is not None

    def test_pip_audit_importable(self):
        """pip-audit — invoked via subprocess in orchestration/static_analysis.py (DS step)."""
        mod = importlib.import_module("pip_audit")
        assert mod is not None


# ---------------------------------------------------------------------------
# 2. requirements.txt presence tests — every importable package must be listed
# ---------------------------------------------------------------------------

class TestPackagesListedInRequirements:
    """Every directly-used third-party package must appear in requirements.txt."""

    def test_aiohttp_listed(self):
        assert_in_requirements("aiohttp")

    def test_requests_listed(self):
        assert_in_requirements("requests")

    def test_pytest_listed(self):
        assert_in_requirements("pytest")

    def test_pytest_asyncio_listed(self):
        assert_in_requirements("pytest_asyncio")

    def test_pytest_cov_listed(self):
        assert_in_requirements("pytest_cov")

    def test_playwright_listed(self):
        assert_in_requirements("playwright")

    def test_pytest_playwright_listed(self):
        assert_in_requirements("pytest_playwright")

    def test_flake8_listed(self):
        assert_in_requirements("flake8")

    def test_bandit_listed(self):
        assert_in_requirements("bandit")

    def test_pip_audit_listed(self):
        assert_in_requirements("pip_audit")


# ---------------------------------------------------------------------------
# 3. CLI tool availability — tools invoked via subprocess must be on PATH
# ---------------------------------------------------------------------------

class TestCliToolsOnPath:
    """Tools that static_analysis.py invokes via subprocess must be discoverable."""

    def test_flake8_on_path(self):
        """flake8 CLI must be on PATH for BC (Bug Capture) step."""
        assert shutil.which("flake8") is not None, (
            "flake8 is not on PATH. Install it: pip install flake8>=7.0"
        )

    def test_bandit_on_path(self):
        """bandit CLI must be on PATH for SEA and DS steps."""
        assert shutil.which("bandit") is not None, (
            "bandit is not on PATH. Install it: pip install bandit>=1.7"
        )

    def test_pip_audit_on_path(self):
        """pip-audit CLI must be on PATH for DS (Security) step."""
        assert shutil.which("pip-audit") is not None, (
            "pip-audit is not on PATH. Install it: pip install pip-audit>=2.7"
        )


# ---------------------------------------------------------------------------
# 4. Version pin adequacy — installed versions must satisfy the >= pins
# ---------------------------------------------------------------------------

class TestVersionPinsAdequate:
    """Installed versions must be >= the pinned minimum in requirements.txt."""

    def _get_installed_version(self, import_name: str) -> tuple[int, ...]:
        try:
            mod = importlib.import_module(import_name)
            ver_str = getattr(mod, "__version__", "0.0.0") or "0.0.0"
            return tuple(int(x) for x in re.findall(r"\d+", ver_str)[:3])
        except (ImportError, ValueError):
            return (0, 0, 0)

    def _get_pinned_version(self, req_name: str) -> tuple[int, ...]:
        """Extract the >= pin from requirements.txt for the given package."""
        for line in REQUIREMENTS.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            norm = re.split(r"[>=<!;\[#]", line)[0].strip().lower().replace("-", "_")
            if norm == req_name.lower().replace("-", "_"):
                m = re.search(r">=\s*([\d.]+)", line)
                if m:
                    return tuple(int(x) for x in m.group(1).split(".")[:3])
        return (0, 0, 0)

    def test_aiohttp_version_pin(self):
        installed = self._get_installed_version("aiohttp")
        pinned = self._get_pinned_version("aiohttp")
        assert installed >= pinned, f"aiohttp installed {installed} < pinned {pinned}"

    def test_requests_version_pin(self):
        installed = self._get_installed_version("requests")
        pinned = self._get_pinned_version("requests")
        assert installed >= pinned, f"requests installed {installed} < pinned {pinned}"

    def test_pytest_version_pin(self):
        installed = self._get_installed_version("pytest")
        pinned = self._get_pinned_version("pytest")
        assert installed >= pinned, f"pytest installed {installed} < pinned {pinned}"

    def test_flake8_version_pin(self):
        installed = self._get_installed_version("flake8")
        pinned = self._get_pinned_version("flake8")
        assert installed >= pinned, f"flake8 installed {installed} < pinned {pinned}"

    def test_bandit_version_pin(self):
        installed = self._get_installed_version("bandit")
        pinned = self._get_pinned_version("bandit")
        assert installed >= pinned, f"bandit installed {installed} < pinned {pinned}"

    def test_pip_audit_version_pin(self):
        installed = self._get_installed_version("pip_audit")
        pinned = self._get_pinned_version("pip_audit")
        assert installed >= pinned, f"pip_audit installed {installed} < pinned {pinned}"
