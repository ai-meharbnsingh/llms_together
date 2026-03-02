"""
Forensic Fix Tests — Wave 1 RED
Covers the 4 real issues from forensic reports:
  1. Config load — no try/except in main.py (FER-CLI-002)
  2. Bare except in DB migrations (FER-AF-007)
  3. No message size limit in _sanitize_cli_input (FER-AF-012)
  4. playwright missing from requirements.txt (FER-AF-002)

Run: pytest tests/test_forensic_fixes.py -v
"""
import sqlite3
import sys
import os
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─── Issue 1: Config load error handling in main.py ────────────────────────

class TestConfigLoadErrorHandling:
    """main() must handle missing/malformed config with a clear message, not a raw crash."""

    @pytest.mark.asyncio
    async def test_missing_config_raises_systemexit(self):
        """main() with a nonexistent config path must call sys.exit(1), not raise FileNotFoundError."""
        from unittest.mock import patch
        import main as main_mod

        with patch("sys.exit") as mock_exit, \
             patch("builtins.print"), \
             patch("logging.getLogger"):
            try:
                await main_mod.main(config_path="/nonexistent/path/config.json")
            except SystemExit:
                pass
            except Exception as e:
                # Should NOT propagate raw FileNotFoundError / JSONDecodeError to caller
                pytest.fail(
                    f"main() propagated raw exception instead of handling it: "
                    f"{type(e).__name__}: {e}"
                )

    @pytest.mark.asyncio
    async def test_malformed_config_raises_systemexit(self, tmp_path):
        """main() with invalid JSON config must call sys.exit(1), not crash with JSONDecodeError."""
        bad_config = tmp_path / "bad.json"
        bad_config.write_text("{ this is not valid json !!!}")
        import main as main_mod

        with pytest.raises((SystemExit, Exception)) as exc_info:
            await main_mod.main(config_path=str(bad_config))

        # If it raises an exception, it must NOT be a raw json.JSONDecodeError
        import json
        if exc_info.type is not SystemExit:
            assert exc_info.type is not json.JSONDecodeError, (
                "main() must not propagate raw JSONDecodeError — wrap in try/except"
            )

    def test_load_config_helper_returns_none_on_missing(self):
        """A dedicated _load_config() helper must return None (not raise) on missing file."""
        import main as main_mod
        assert hasattr(main_mod, "_load_config"), (
            "main.py must expose a _load_config(path) helper that returns None on error"
        )
        result = main_mod._load_config("/nonexistent/path.json")
        assert result is None, (
            f"_load_config('/nonexistent') must return None, got {result!r}"
        )

    def test_load_config_helper_returns_none_on_bad_json(self, tmp_path):
        """_load_config() must return None on malformed JSON."""
        import main as main_mod
        bad = tmp_path / "bad.json"
        bad.write_text("not json")
        result = main_mod._load_config(str(bad))
        assert result is None, (
            f"_load_config with bad JSON must return None, got {result!r}"
        )

    def test_load_config_helper_succeeds_on_valid_config(self, tmp_path):
        """_load_config() must return the dict on valid JSON."""
        import json
        import main as main_mod
        good = tmp_path / "good.json"
        good.write_text(json.dumps({"factory": {"version": "1.0"}}))
        result = main_mod._load_config(str(good))
        assert result == {"factory": {"version": "1.0"}}, (
            f"_load_config must return parsed dict, got {result!r}"
        )


# ─── Issue 2: Bare except in DB migrations ─────────────────────────────────

class TestMigrationBareExcept:
    """Migration bare-except blocks must only swallow 'already exists' errors."""

    def _get_db_class(self):
        from orchestration.database import WatchdogDB
        return WatchdogDB

    def test_migrate_v2_v3_only_catches_already_exists(self, tmp_path):
        """_migrate_v2_to_v3 must re-raise OperationalError that is NOT 'already exists'."""
        from orchestration.database import WatchdogDB
        import unittest.mock as mock

        db = WatchdogDB.__new__(WatchdogDB)

        # _migrate_v2_to_v3 makes: 3 CREATE TABLE calls, then 2 ALTER TABLE, then 7 CREATE INDEX
        # We raise a real (non-already-exists) error on the first ALTER TABLE call (4th overall)
        real_error = sqlite3.OperationalError("database disk image is malformed")
        conn = mock.MagicMock()
        conn.execute.side_effect = [
            None,       # CREATE TABLE dac_tags
            None,       # CREATE TABLE learning_log
            None,       # CREATE TABLE cost_tracking
            real_error, # ALTER TABLE tasks ADD COLUMN project_type  ← real error, must propagate
        ]

        with pytest.raises(sqlite3.OperationalError, match="malformed"):
            db._migrate_v2_to_v3(conn)

    def test_migrate_v2_v3_swallows_already_exists(self, tmp_path):
        """_migrate_v2_to_v3 must silently continue when error message contains 'already exists'."""
        from orchestration.database import WatchdogDB
        import unittest.mock as mock

        db = WatchdogDB.__new__(WatchdogDB)
        conn = mock.MagicMock()

        # Simulate "already exists" errors on both ALTER TABLE calls — must be swallowed
        dup_type = sqlite3.OperationalError("duplicate column name: project_type")
        dup_tag  = sqlite3.OperationalError("duplicate column name: dac_tag")
        conn.execute.side_effect = [
            None,     # CREATE TABLE dac_tags
            None,     # CREATE TABLE learning_log
            None,     # CREATE TABLE cost_tracking
            dup_type, # ALTER TABLE ADD COLUMN project_type  ← swallowed
            dup_tag,  # ALTER TABLE ADD COLUMN dac_tag       ← swallowed
            None, None, None, None, None, None, None,  # 7 CREATE INDEX calls
        ]

        # Must NOT raise
        db._migrate_v2_to_v3(conn)

    def test_migrate_v3_v4_only_catches_already_exists(self):
        """_migrate_v3_to_v4 must re-raise OperationalError that is NOT 'already exists'."""
        from orchestration.database import WatchdogDB
        import unittest.mock as mock

        db = WatchdogDB.__new__(WatchdogDB)
        conn = mock.MagicMock()
        real_error = sqlite3.OperationalError("no such table: tasks")
        conn.execute.side_effect = real_error

        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            db._migrate_v3_to_v4(conn)

    def test_migrate_v3_v4_swallows_already_exists(self):
        """_migrate_v3_to_v4 must silently continue on duplicate column error."""
        from orchestration.database import WatchdogDB
        import unittest.mock as mock

        db = WatchdogDB.__new__(WatchdogDB)
        conn = mock.MagicMock()
        already_exists = sqlite3.OperationalError("duplicate column name: dependencies")
        conn.execute.side_effect = already_exists

        # Must NOT raise
        db._migrate_v3_to_v4(conn)


# ─── Issue 3: Message size limit in _sanitize_cli_input ────────────────────

class TestMessageSizeLimit:
    """CLIWorkerAdapter._sanitize_cli_input must reject messages over the size limit."""

    MAX_BYTES = 512 * 1024  # 512 KB — reasonable CLI arg limit

    def test_oversized_message_raises_value_error(self):
        """Messages over 512 KB must raise ValueError."""
        from workers.adapters import CLIWorkerAdapter
        cfg = {"type": "cli_login", "timeout": 30, "max_retries": 1}
        adapter = CLIWorkerAdapter("kimi", cfg)
        giant = "x" * (self.MAX_BYTES + 1)
        with pytest.raises(ValueError, match="[Mm]essage.*too large|[Ss]ize.*limit|[Tt]oo long"):
            adapter._sanitize_cli_input(giant)

    def test_normal_message_passes(self):
        """Normal-sized messages must pass through unchanged (except existing sanitization)."""
        from workers.adapters import CLIWorkerAdapter
        cfg = {"type": "cli_login", "timeout": 30, "max_retries": 1}
        adapter = CLIWorkerAdapter("kimi", cfg)
        msg = "Write a hello world function in Python."
        result = adapter._sanitize_cli_input(msg)
        assert result == msg

    def test_exactly_at_limit_passes(self):
        """A message exactly at the limit must NOT raise."""
        from workers.adapters import CLIWorkerAdapter
        cfg = {"type": "cli_login", "timeout": 30, "max_retries": 1}
        adapter = CLIWorkerAdapter("kimi", cfg)
        msg = "x" * self.MAX_BYTES
        # Must not raise
        result = adapter._sanitize_cli_input(msg)
        assert len(result) == self.MAX_BYTES

    def test_sanitize_still_strips_null_bytes(self):
        """Size-limit check must not break existing null-byte stripping."""
        from workers.adapters import CLIWorkerAdapter
        cfg = {"type": "cli_login", "timeout": 30, "max_retries": 1}
        adapter = CLIWorkerAdapter("kimi", cfg)
        msg = "hello\x00world"
        result = adapter._sanitize_cli_input(msg)
        assert "\x00" not in result

    def test_sanitize_still_handles_leading_dash(self):
        """Size-limit check must not break existing leading-dash handling."""
        from workers.adapters import CLIWorkerAdapter
        cfg = {"type": "cli_login", "timeout": 30, "max_retries": 1}
        adapter = CLIWorkerAdapter("kimi", cfg)
        msg = "--version"
        result = adapter._sanitize_cli_input(msg)
        # The result must NOT start with '-' (a space is prepended to prevent flag injection)
        assert not result.startswith("-"), (
            "Leading dash must be escaped — a space should be prepended"
        )


# ─── Issue 4: playwright in requirements.txt ───────────────────────────────

class TestPlaywrightInRequirements:
    """playwright must be declared as a dependency in requirements.txt."""

    def test_playwright_in_requirements(self):
        """requirements.txt must contain 'playwright'."""
        req_path = Path(__file__).parent.parent / "requirements.txt"
        assert req_path.exists(), "requirements.txt not found"
        content = req_path.read_text().lower()
        assert "playwright" in content, (
            "playwright is used in tests but not declared in requirements.txt — "
            "add 'playwright>=1.40' to requirements.txt"
        )

    def test_pytest_playwright_in_requirements(self):
        """pytest-playwright must also be declared (test runner plugin)."""
        req_path = Path(__file__).parent.parent / "requirements.txt"
        content = req_path.read_text().lower()
        assert "pytest-playwright" in content, (
            "pytest-playwright plugin must be in requirements.txt"
        )
