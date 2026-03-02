"""
Static Analysis Tests — Autonomous Factory
════════════════════════════════════════════
Tests for orchestration/static_analysis.py:
  - Parser unit tests (flake8, bandit JSON, pip-audit JSON)
  - Tool-not-installed graceful fallback
  - Subprocess execution mocking
  - AnalysisResult.summary_for_llm() formatting
  - Integration with TDD pipeline steps
════════════════════════════════════════════
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestration.static_analysis import (
    AnalysisResult,
    Finding,
    StaticAnalyzer,
    ToolStatus,
)


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def project_dir(tmp_path):
    """Create a temporary project directory with sample .py files."""
    # Create sample Python files
    (tmp_path / "main.py").write_text(
        "import os\n\ndef run():\n    eval(input())\n    return True\n"
    )
    (tmp_path / "utils.py").write_text(
        "def helper():\n    try:\n        risky()\n    except Exception:\n        pass\n"
    )
    (tmp_path / "requirements.txt").write_text("flask==2.3.0\nrequests==2.28.0\n")
    # Directories that should be skipped
    venv = tmp_path / "venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "something.py").write_text("# should be skipped\n")
    return tmp_path


@pytest.fixture
def analyzer(project_dir):
    """StaticAnalyzer pointed at the test project."""
    return StaticAnalyzer(str(project_dir))


# ═══════════════════════════════════════════════════════════════
# PARSER UNIT TESTS
# ═══════════════════════════════════════════════════════════════


class TestFlake8Parser:
    """Test _parse_flake8_output."""

    def test_parses_standard_output(self, analyzer):
        output = (
            "main.py:4:5: E302 expected 2 blank lines, got 1\n"
            "utils.py:3:1: W291 trailing whitespace\n"
        )
        findings = analyzer._parse_flake8_output(output)
        assert len(findings) == 2
        assert findings[0].tool == "flake8"
        assert findings[0].file == "main.py"
        assert findings[0].line == 4
        assert findings[0].code == "E302"
        assert findings[0].severity == "medium"
        assert findings[1].severity == "low"  # W291

    def test_parses_fatal_error_code(self, analyzer):
        output = "broken.py:1:1: F821 undefined name 'foo'\n"
        findings = analyzer._parse_flake8_output(output)
        assert len(findings) == 1
        assert findings[0].severity == "high"  # F-codes are high

    def test_parses_syntax_error_code(self, analyzer):
        output = "broken.py:1:1: E999 SyntaxError: invalid syntax\n"
        findings = analyzer._parse_flake8_output(output)
        assert len(findings) == 1
        assert findings[0].severity == "high"  # E9xx are high

    def test_empty_output(self, analyzer):
        findings = analyzer._parse_flake8_output("")
        assert findings == []

    def test_malformed_lines_skipped(self, analyzer):
        output = "not a valid line\nstill bad\n"
        findings = analyzer._parse_flake8_output(output)
        assert findings == []

    def test_non_numeric_line_skipped(self, analyzer):
        output = "file.py:abc:1: E302 something\n"
        findings = analyzer._parse_flake8_output(output)
        assert findings == []


class TestBanditParser:
    """Test _parse_bandit_json."""

    def test_parses_valid_json(self, analyzer):
        data = {
            "results": [
                {
                    "filename": "main.py",
                    "line_number": 4,
                    "test_id": "B307",
                    "test_name": "eval",
                    "issue_severity": "HIGH",
                    "issue_text": "Use of possibly insecure function - eval",
                },
                {
                    "filename": "utils.py",
                    "line_number": 3,
                    "test_id": "B110",
                    "test_name": "try_except_pass",
                    "issue_severity": "LOW",
                    "issue_text": "Try/except with pass detected",
                },
            ]
        }
        findings = analyzer._parse_bandit_json(json.dumps(data))
        assert len(findings) == 2
        assert findings[0].tool == "bandit"
        assert findings[0].code == "B307"
        assert findings[0].severity == "high"
        assert findings[1].code == "B110"

    def test_empty_results(self, analyzer):
        findings = analyzer._parse_bandit_json('{"results": []}')
        assert findings == []

    def test_invalid_json(self, analyzer):
        findings = analyzer._parse_bandit_json("not json at all")
        assert findings == []

    def test_none_input(self, analyzer):
        findings = analyzer._parse_bandit_json(None)
        assert findings == []


class TestPipAuditParser:
    """Test _parse_pip_audit_json."""

    def test_parses_dependency_list(self, analyzer):
        data = [
            {
                "name": "flask",
                "version": "2.3.0",
                "vulns": [
                    {"id": "CVE-2023-1234", "description": "XSS vulnerability"}
                ],
            }
        ]
        findings = analyzer._parse_pip_audit_json(json.dumps(data))
        assert len(findings) == 1
        assert findings[0].tool == "pip-audit"
        assert findings[0].code == "CVE-2023-1234"
        assert "flask==2.3.0" in findings[0].message

    def test_no_vulns(self, analyzer):
        data = [{"name": "flask", "version": "3.0.0", "vulns": []}]
        findings = analyzer._parse_pip_audit_json(json.dumps(data))
        assert findings == []

    def test_invalid_json(self, analyzer):
        findings = analyzer._parse_pip_audit_json("broken")
        assert findings == []

    def test_dependencies_key_format(self, analyzer):
        """pip-audit sometimes uses {"dependencies": [...]} format."""
        data = {
            "dependencies": [
                {
                    "name": "requests",
                    "version": "2.28.0",
                    "vulns": [{"id": "CVE-2023-5678", "description": "SSRF"}],
                }
            ]
        }
        findings = analyzer._parse_pip_audit_json(json.dumps(data))
        assert len(findings) == 1
        assert findings[0].code == "CVE-2023-5678"


# ═══════════════════════════════════════════════════════════════
# FINDING / ANALYSISRESULT
# ═══════════════════════════════════════════════════════════════


class TestFinding:
    def test_to_dict(self):
        f = Finding(
            tool="flake8", file="x.py", line=10, code="E302",
            severity="medium", message="expected 2 blank lines",
            category="lint",
        )
        d = f.to_dict()
        assert d["tool"] == "flake8"
        assert d["line"] == 10

    def test_default_category(self):
        f = Finding(tool="t", file="f", line=1, code="C", severity="low", message="m")
        assert f.category == ""


class TestAnalysisResult:
    def test_summary_clean(self):
        r = AnalysisResult(tool_name="flake8", status=ToolStatus.AVAILABLE)
        assert "No issues found" in r.summary_for_llm()

    def test_summary_not_installed(self):
        r = AnalysisResult(tool_name="bandit", status=ToolStatus.NOT_INSTALLED)
        assert "not installed" in r.summary_for_llm().lower()

    def test_summary_exec_error(self):
        r = AnalysisResult(
            tool_name="flake8", status=ToolStatus.EXEC_ERROR, error="segfault"
        )
        s = r.summary_for_llm()
        assert "Execution error" in s
        assert "segfault" in s

    def test_summary_timeout(self):
        r = AnalysisResult(tool_name="bandit", status=ToolStatus.TIMEOUT)
        assert "Timed out" in r.summary_for_llm()

    def test_summary_with_findings(self):
        findings = [
            Finding(
                tool="flake8", file="a.py", line=i, code=f"E{i:03d}",
                severity="medium", message=f"issue {i}",
            )
            for i in range(3)
        ]
        r = AnalysisResult(
            tool_name="flake8", status=ToolStatus.AVAILABLE, findings=findings
        )
        s = r.summary_for_llm()
        assert "3 issue(s)" in s
        assert "a.py:0" in s

    def test_summary_caps_at_50(self):
        findings = [
            Finding(tool="f", file="a.py", line=i, code="E", severity="low", message="m")
            for i in range(60)
        ]
        r = AnalysisResult(
            tool_name="flake8", status=ToolStatus.AVAILABLE, findings=findings
        )
        s = r.summary_for_llm()
        assert "60 issue(s)" in s
        assert "and 10 more" in s


# ═══════════════════════════════════════════════════════════════
# TOOL AVAILABILITY
# ═══════════════════════════════════════════════════════════════


class TestToolAvailability:
    def test_check_tool_caches(self, analyzer):
        with patch("shutil.which", return_value="/usr/bin/flake8"):
            s1 = analyzer._check_tool("flake8")
            s2 = analyzer._check_tool("flake8")
        assert s1 == ToolStatus.AVAILABLE
        assert s2 == ToolStatus.AVAILABLE

    def test_check_tool_not_found(self, analyzer):
        analyzer._tool_cache.clear()
        with patch("shutil.which", return_value=None):
            assert analyzer._check_tool("nonexistent") == ToolStatus.NOT_INSTALLED


# ═══════════════════════════════════════════════════════════════
# FILE RESOLUTION
# ═══════════════════════════════════════════════════════════════


class TestResolveFiles:
    def test_filters_py_files(self, analyzer, project_dir):
        result = analyzer._resolve_py_files(["main.py", "readme.md", "utils.py"])
        assert "main.py" in result
        assert "utils.py" in result
        assert "readme.md" not in result

    def test_skips_nonexistent(self, analyzer):
        result = analyzer._resolve_py_files(["ghost.py"])
        assert result == []

    def test_fallback_scans_project(self, analyzer, project_dir):
        result = analyzer._resolve_py_files(None)
        assert "main.py" in result
        assert "utils.py" in result
        # venv files should be excluded
        assert not any("venv" in f for f in result)


# ═══════════════════════════════════════════════════════════════
# SUBPROCESS EXECUTION (MOCKED)
# ═══════════════════════════════════════════════════════════════


class TestRunBugCapture:
    async def test_returns_findings_when_tool_available(self, analyzer):
        flake8_output = "main.py:4:5: E302 expected 2 blank lines\n"
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(flake8_output.encode(), b"")
        )
        mock_proc.returncode = 1  # flake8 returns 1 when findings exist
        mock_proc.kill = MagicMock()

        with patch("shutil.which", return_value="/usr/bin/flake8"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("asyncio.wait_for", return_value=mock_proc.communicate.return_value):
            analyzer._tool_cache.clear()
            result = await analyzer.run_bug_capture(["main.py"])

        assert result.status == ToolStatus.AVAILABLE
        assert len(result.findings) == 1
        assert result.findings[0].code == "E302"

    async def test_fallback_when_not_installed(self, analyzer):
        analyzer._tool_cache.clear()
        with patch("shutil.which", return_value=None):
            result = await analyzer.run_bug_capture(["main.py"])
        assert result.status == ToolStatus.NOT_INSTALLED
        assert result.findings == []

    async def test_no_py_files(self, analyzer):
        with patch("shutil.which", return_value="/usr/bin/flake8"):
            analyzer._tool_cache.clear()
            result = await analyzer.run_bug_capture(["readme.md"])
        assert result.findings == []
        assert "No .py files" in result.raw_output


class TestRunSilentErrorAnalysis:
    async def test_returns_bandit_subset_findings(self, analyzer):
        bandit_output = json.dumps({
            "results": [
                {
                    "filename": "utils.py",
                    "line_number": 3,
                    "test_id": "B110",
                    "test_name": "try_except_pass",
                    "issue_severity": "LOW",
                    "issue_text": "Try/except pass detected",
                }
            ]
        })
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(bandit_output.encode(), b"")
        )
        mock_proc.returncode = 1
        mock_proc.kill = MagicMock()

        with patch("shutil.which", return_value="/usr/bin/bandit"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("asyncio.wait_for", return_value=mock_proc.communicate.return_value):
            analyzer._tool_cache.clear()
            result = await analyzer.run_silent_error_analysis(["utils.py"])

        assert result.status == ToolStatus.AVAILABLE
        assert len(result.findings) == 1
        assert result.findings[0].code == "B110"

    async def test_fallback_when_not_installed(self, analyzer):
        analyzer._tool_cache.clear()
        with patch("shutil.which", return_value=None):
            result = await analyzer.run_silent_error_analysis(["utils.py"])
        assert result.status == ToolStatus.NOT_INSTALLED


class TestRunSecurityScan:
    async def test_returns_combined_results(self, analyzer, project_dir):
        bandit_output = json.dumps({
            "results": [
                {
                    "filename": "main.py",
                    "line_number": 4,
                    "test_id": "B307",
                    "test_name": "eval",
                    "issue_severity": "HIGH",
                    "issue_text": "Use of eval detected",
                }
            ]
        })
        pip_audit_output = json.dumps([
            {
                "name": "flask",
                "version": "2.3.0",
                "vulns": [{"id": "CVE-2023-1234", "description": "vuln"}],
            }
        ])

        async def mock_exec_tool(cmd, cwd=None):
            if cmd[0] == "bandit":
                return (1, bandit_output, "")
            else:
                return (0, pip_audit_output, "")

        def mock_which(name):
            return f"/usr/bin/{name}"

        with patch("shutil.which", side_effect=mock_which):
            analyzer._tool_cache.clear()
            analyzer._exec_tool = mock_exec_tool
            results = await analyzer.run_security_scan(["main.py"])

        assert len(results) == 2  # bandit + pip-audit
        bandit_result = next(r for r in results if r.tool_name == "bandit")
        pip_result = next(r for r in results if r.tool_name == "pip-audit")
        assert len(bandit_result.findings) == 1
        assert len(pip_result.findings) == 1

    async def test_both_tools_missing(self, analyzer):
        analyzer._tool_cache.clear()
        with patch("shutil.which", return_value=None):
            results = await analyzer.run_security_scan(["main.py"])
        assert all(r.status == ToolStatus.NOT_INSTALLED for r in results)


class TestExecToolTimeout:
    async def test_timeout_returns_timeout_status(self, analyzer):
        mock_proc = AsyncMock()
        mock_proc.kill = MagicMock()

        with patch("shutil.which", return_value="/usr/bin/flake8"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            analyzer._tool_cache.clear()
            result = await analyzer.run_bug_capture(["main.py"])

        assert result.status == ToolStatus.TIMEOUT
