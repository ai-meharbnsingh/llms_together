"""
Static Analysis Tools — Autonomous Factory
════════════════════════════════════════════
Runs real security/lint tools (flake8, bandit, pip-audit) on project code
and feeds structured findings to LLM for interpretation.

Tools are ADDITIVE context — if a tool is not installed, the LLM-only
flow continues without it.

Used by TDD Pipeline steps:
  BC  → flake8 (lint/bug detection)
  SEA → bandit subset (error-handling patterns)
  DS  → bandit full + pip-audit (security)
════════════════════════════════════════════
"""

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("factory.static_analysis")


class ToolStatus(Enum):
    """Status of an external tool check."""
    AVAILABLE = "available"
    NOT_INSTALLED = "not_installed"
    EXEC_ERROR = "exec_error"
    TIMEOUT = "timeout"


@dataclass
class Finding:
    """A single finding from a static analysis tool."""
    tool: str
    file: str
    line: int
    code: str
    severity: str
    message: str
    category: str = ""

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "file": self.file,
            "line": self.line,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "category": self.category,
        }


@dataclass
class AnalysisResult:
    """Result of running one or more static analysis tools."""
    tool_name: str
    status: ToolStatus
    findings: List[Finding] = field(default_factory=list)
    raw_output: str = ""
    error: str = ""

    def summary_for_llm(self) -> str:
        """Format findings as structured text for LLM prompt injection."""
        if self.status == ToolStatus.NOT_INSTALLED:
            return f"[{self.tool_name}] Tool not installed — skipped.\n"
        if self.status == ToolStatus.EXEC_ERROR:
            return f"[{self.tool_name}] Execution error: {self.error[:200]}\n"
        if self.status == ToolStatus.TIMEOUT:
            return f"[{self.tool_name}] Timed out — skipped.\n"

        if not self.findings:
            return f"[{self.tool_name}] No issues found (clean).\n"

        lines = [f"[{self.tool_name}] {len(self.findings)} issue(s) found:\n"]
        for f in self.findings[:50]:  # Cap at 50 findings for LLM context
            lines.append(
                f"  - {f.file}:{f.line} [{f.severity}] {f.code}: {f.message}"
            )
        if len(self.findings) > 50:
            lines.append(f"  ... and {len(self.findings) - 50} more.")
        return "\n".join(lines) + "\n"


class StaticAnalyzer:
    """
    Runs real static analysis tools on project code.

    Usage:
        analyzer = StaticAnalyzer("/path/to/project")
        result = await analyzer.run_bug_capture(["file1.py", "file2.py"])
        llm_context = result.summary_for_llm()
    """

    EXEC_TIMEOUT = 60  # seconds

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        self._tool_cache: dict[str, ToolStatus] = {}

    def _check_tool(self, tool_name: str) -> ToolStatus:
        """Check if a tool is available on PATH (cached)."""
        if tool_name not in self._tool_cache:
            self._tool_cache[tool_name] = (
                ToolStatus.AVAILABLE if shutil.which(tool_name)
                else ToolStatus.NOT_INSTALLED
            )
        return self._tool_cache[tool_name]

    def _resolve_py_files(self, files: Optional[List[str]] = None) -> List[str]:
        """Filter to .py files and resolve paths relative to project."""
        if files:
            return [
                f for f in files
                if f.endswith(".py") and (self.project_path / f).exists()
            ]
        # Fallback: scan project for .py files (limited depth)
        found = []
        for p in self.project_path.rglob("*.py"):
            # Skip venv, node_modules, __pycache__
            parts = p.parts
            if any(skip in parts for skip in ("venv", "node_modules", "__pycache__", ".git")):
                continue
            found.append(str(p.relative_to(self.project_path)))
            if len(found) >= 200:  # Safety cap
                break
        return found

    async def _exec_tool(self, cmd: List[str], cwd: str = None) -> tuple[int, str, str]:
        """Run a subprocess and return (returncode, stdout, stderr)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd or str(self.project_path),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.EXEC_TIMEOUT
            )
            return (
                proc.returncode,
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace"),
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise
        except FileNotFoundError:
            raise

    # ─── Parsers ───

    def _parse_flake8_output(self, output: str) -> List[Finding]:
        """Parse flake8 default output format: file:line:col: CODE message"""
        findings = []
        for line in output.strip().splitlines():
            # Format: path/file.py:10:5: E302 expected 2 blank lines
            parts = line.split(":", 3)
            if len(parts) < 4:
                continue
            filepath = parts[0].strip()
            try:
                lineno = int(parts[1].strip())
            except ValueError:
                continue
            msg = parts[3].strip()
            # Extract code (e.g., E302) from message
            code = msg.split()[0] if msg else "E000"
            severity = self._flake8_severity(code)
            findings.append(Finding(
                tool="flake8",
                file=filepath,
                line=lineno,
                code=code,
                severity=severity,
                message=msg,
                category="lint",
            ))
        return findings

    @staticmethod
    def _flake8_severity(code: str) -> str:
        """Map flake8 codes to severity levels."""
        if code.startswith("E9") or code.startswith("F"):
            return "high"  # Syntax errors, undefined names
        if code.startswith("E"):
            return "medium"
        if code.startswith("W"):
            return "low"
        return "low"

    def _parse_bandit_json(self, output: str) -> List[Finding]:
        """Parse bandit JSON output."""
        findings = []
        try:
            data = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            return findings

        for result in data.get("results", []):
            severity = result.get("issue_severity", "MEDIUM").lower()
            findings.append(Finding(
                tool="bandit",
                file=result.get("filename", ""),
                line=result.get("line_number", 0),
                code=result.get("test_id", "B000"),
                severity=severity,
                message=result.get("issue_text", ""),
                category=result.get("test_name", "security"),
            ))
        return findings

    def _parse_pip_audit_json(self, output: str) -> List[Finding]:
        """Parse pip-audit JSON output."""
        findings = []
        try:
            data = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            return findings

        # pip-audit outputs a list of vulnerability dicts
        vulns = data if isinstance(data, list) else data.get("dependencies", [])
        for dep in vulns:
            name = dep.get("name", "unknown")
            version = dep.get("version", "?")
            for vuln in dep.get("vulns", []):
                vuln_id = vuln.get("id", "UNKNOWN")
                desc = vuln.get("description", vuln.get("fix_versions", ""))
                findings.append(Finding(
                    tool="pip-audit",
                    file="requirements.txt",
                    line=0,
                    code=vuln_id,
                    severity="high",
                    message=f"{name}=={version}: {desc}",
                    category="dependency",
                ))
        return findings

    # ─── Public Methods ───

    async def run_bug_capture(self, files: Optional[List[str]] = None) -> AnalysisResult:
        """Run flake8 on .py files for the BC (Bug Capture) step."""
        tool = "flake8"
        status = self._check_tool(tool)
        if status != ToolStatus.AVAILABLE:
            logger.info(f"{tool} not installed — BC will use LLM-only")
            return AnalysisResult(tool_name=tool, status=status)

        py_files = self._resolve_py_files(files)
        if not py_files:
            return AnalysisResult(
                tool_name=tool, status=ToolStatus.AVAILABLE,
                raw_output="No .py files to scan"
            )

        cmd = [tool, "--max-line-length=120", "--statistics"] + py_files

        try:
            rc, stdout, stderr = await self._exec_tool(cmd)
        except asyncio.TimeoutError:
            return AnalysisResult(tool_name=tool, status=ToolStatus.TIMEOUT)
        except FileNotFoundError:
            self._tool_cache[tool] = ToolStatus.NOT_INSTALLED
            return AnalysisResult(tool_name=tool, status=ToolStatus.NOT_INSTALLED)

        findings = self._parse_flake8_output(stdout)
        return AnalysisResult(
            tool_name=tool,
            status=ToolStatus.AVAILABLE,
            findings=findings,
            raw_output=stdout[:5000],
        )

    async def run_silent_error_analysis(self, files: Optional[List[str]] = None) -> AnalysisResult:
        """Run bandit error-handling subset for the SEA step."""
        tool = "bandit"
        status = self._check_tool(tool)
        if status != ToolStatus.AVAILABLE:
            logger.info(f"{tool} not installed — SEA will use LLM-only")
            return AnalysisResult(tool_name=tool, status=status)

        py_files = self._resolve_py_files(files)
        if not py_files:
            return AnalysisResult(
                tool_name=tool, status=ToolStatus.AVAILABLE,
                raw_output="No .py files to scan"
            )

        # B110=try_except_pass, B112=try_except_continue — error-handling subset
        cmd = [tool, "-f", "json", "-t", "B110,B112", "--"] + py_files

        try:
            rc, stdout, stderr = await self._exec_tool(cmd)
        except asyncio.TimeoutError:
            return AnalysisResult(tool_name=tool, status=ToolStatus.TIMEOUT)
        except FileNotFoundError:
            self._tool_cache[tool] = ToolStatus.NOT_INSTALLED
            return AnalysisResult(tool_name=tool, status=ToolStatus.NOT_INSTALLED)

        findings = self._parse_bandit_json(stdout)
        return AnalysisResult(
            tool_name=tool,
            status=ToolStatus.AVAILABLE,
            findings=findings,
            raw_output=stdout[:5000],
        )

    async def run_security_scan(self, files: Optional[List[str]] = None) -> List[AnalysisResult]:
        """Run bandit (full) + pip-audit for the DS (Security) step.

        Returns a list of AnalysisResult (one per tool).
        """
        results = await asyncio.gather(
            self._run_bandit_full(files),
            self._run_pip_audit(),
        )
        return list(results)

    async def _run_bandit_full(self, files: Optional[List[str]] = None) -> AnalysisResult:
        """Run bandit with all checks."""
        tool = "bandit"
        status = self._check_tool(tool)
        if status != ToolStatus.AVAILABLE:
            logger.info(f"{tool} not installed — DS will use LLM-only")
            return AnalysisResult(tool_name=tool, status=status)

        py_files = self._resolve_py_files(files)
        if not py_files:
            return AnalysisResult(
                tool_name=tool, status=ToolStatus.AVAILABLE,
                raw_output="No .py files to scan"
            )

        cmd = [tool, "-f", "json", "--"] + py_files

        try:
            rc, stdout, stderr = await self._exec_tool(cmd)
        except asyncio.TimeoutError:
            return AnalysisResult(tool_name=tool, status=ToolStatus.TIMEOUT)
        except FileNotFoundError:
            self._tool_cache[tool] = ToolStatus.NOT_INSTALLED
            return AnalysisResult(tool_name=tool, status=ToolStatus.NOT_INSTALLED)

        findings = self._parse_bandit_json(stdout)
        return AnalysisResult(
            tool_name=tool,
            status=ToolStatus.AVAILABLE,
            findings=findings,
            raw_output=stdout[:5000],
        )

    async def _run_pip_audit(self) -> AnalysisResult:
        """Run pip-audit on the project's requirements."""
        tool = "pip-audit"
        status = self._check_tool(tool)
        if status != ToolStatus.AVAILABLE:
            logger.info(f"{tool} not installed — DS will use LLM-only")
            return AnalysisResult(tool_name=tool, status=status)

        # Look for requirements.txt in project root
        req_file = self.project_path / "requirements.txt"
        if not req_file.exists():
            return AnalysisResult(
                tool_name=tool, status=ToolStatus.AVAILABLE,
                raw_output="No requirements.txt found"
            )

        cmd = [tool, "-f", "json", "-r", str(req_file)]

        try:
            rc, stdout, stderr = await self._exec_tool(cmd)
        except asyncio.TimeoutError:
            return AnalysisResult(tool_name=tool, status=ToolStatus.TIMEOUT)
        except FileNotFoundError:
            self._tool_cache[tool] = ToolStatus.NOT_INSTALLED
            return AnalysisResult(tool_name=tool, status=ToolStatus.NOT_INSTALLED)

        findings = self._parse_pip_audit_json(stdout)
        return AnalysisResult(
            tool_name=tool,
            status=ToolStatus.AVAILABLE,
            findings=findings,
            raw_output=stdout[:5000],
        )
