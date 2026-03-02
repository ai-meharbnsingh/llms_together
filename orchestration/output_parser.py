"""
Structured Output Parser — Autonomous Factory
═══════════════════════════════════════════════
Parses structured JSON from worker code output.
Writes files, routes decisions, handles malformed output.
═══════════════════════════════════════════════
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from orchestration.database import queue_write

logger = logging.getLogger("factory.output_parser")

# Files any task may legitimately touch regardless of its module scope.
CROSS_CUTTING_FILES = {
    "requirements.txt", "package.json", "package-lock.json",
    "pyproject.toml", "setup.py", "setup.cfg", "Dockerfile",
    "docker-compose.yml", ".env.example", "README.md", ".gitignore",
    "tsconfig.json", "vite.config.ts", "tailwind.config.js",
}


class OutputParseError(Exception):
    """Raised when worker output cannot be parsed."""
    pass


class OutputParser:
    """
    Parses structured JSON output from workers and applies results.

    Expected format:
    {
        "files": [{"path": "relative/path", "content": "...", "action": "create|update|delete"}],
        "decisions": [{"type": "minor|major", "description": "..."}],
        "notes": ["..."],
        "tests_needed": ["..."]
    }
    """

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        self._files_written: List[str] = []
        self._decisions_logged: List[dict] = []
        self._escalations: List[dict] = []

    @staticmethod
    def _escape_string_content(inner: str) -> str:
        """Escape a raw string so it becomes a valid JSON string value."""
        inner = inner.replace('\\', '\\\\')
        inner = inner.replace('"', '\\"')
        inner = inner.replace('\n', '\\n')
        inner = inner.replace('\r', '\\r')
        inner = inner.replace('\t', '\\t')
        return f'"{inner}"'

    @classmethod
    def _sanitize_triple_quotes(cls, text: str) -> str:
        """
        Replace Python-style triple-quoted strings (\"\"\"...\"\"\" or '''...''')
        inside JSON with properly escaped JSON strings.
        Workers like DeepSeek/Qwen sometimes emit these instead of valid JSON strings.
        """
        def replace_triple(m):
            return cls._escape_string_content(m.group(1))

        # Handle triple double-quotes first (most common)
        text = re.sub(r'"""([\s\S]*?)"""', replace_triple, text)
        # Handle triple single-quotes
        text = re.sub(r"'''([\s\S]*?)'''", replace_triple, text)
        return text

    @staticmethod
    def _get_allowed_prefix(task_module: str) -> Optional[str]:
        """Derive the allowed directory prefix from a task module path.

        Examples:
          backend/database.py  →  backend/
          backend/routers/     →  backend/routers/
          frontend/            →  frontend/
        """
        if not task_module:
            return None
        p = Path(task_module)
        if p.suffix:          # It's a file path → scope is its parent dir
            prefix = str(p.parent)
        else:                 # It's a directory path → scope is that dir
            prefix = str(p).rstrip("/")
        return prefix + "/"

    @classmethod
    def _sanitize_backtick_strings(cls, text: str) -> str:
        """
        Replace JavaScript template literals (backtick strings) inside JSON with
        properly escaped JSON strings.
        Workers like Qwen/DeepSeek sometimes wrap TypeScript/JS content in backticks
        instead of valid JSON double-quoted strings.
        Avoids matching triple-backtick code-fence markers.
        """
        def replace_backtick(m):
            return cls._escape_string_content(m.group(1))

        # Match single backtick strings; negative look-ahead/behind prevents
        # matching ``` code-fence markers.
        return re.sub(r'(?<!`)`(?!`)([\s\S]*?)(?<!`)`(?!`)', replace_backtick, text)

    def parse(self, raw_output: str) -> dict:
        """
        Parse worker output string into structured dict.
        Handles: clean JSON, JSON in markdown code blocks, partial JSON,
        and Python triple-quoted strings inside JSON (common in local LLMs).
        Returns parsed dict or raises OutputParseError.
        """
        if not raw_output or not raw_output.strip():
            raise OutputParseError("Empty output from worker")

        raw = raw_output.strip()

        # Build candidate list: raw, triple-quote-sanitized, backtick-sanitized
        candidates = [raw]
        tq_sanitized = self._sanitize_triple_quotes(raw)
        if tq_sanitized != raw:
            candidates.insert(0, tq_sanitized)
        bt_sanitized = self._sanitize_backtick_strings(tq_sanitized)
        if bt_sanitized not in candidates:
            candidates.insert(0, bt_sanitized)

        for candidate in candidates:
            # Try direct JSON parse
            try:
                result = json.loads(candidate)
                if isinstance(result, dict):
                    return self._validate_structure(result)
            except json.JSONDecodeError:
                pass

            # Try extracting JSON from markdown code blocks
            json_match = re.search(r'```(?:json)?\s*\n({[\s\S]*?})\s*\n```', candidate)
            if json_match:
                block = json_match.group(1)
                # Try the block raw, then with each sanitizer applied
                for attempt in [
                    block,
                    self._sanitize_triple_quotes(block),
                    self._sanitize_backtick_strings(block),
                    self._sanitize_backtick_strings(self._sanitize_triple_quotes(block)),
                ]:
                    try:
                        result = json.loads(attempt)
                        if isinstance(result, dict):
                            return self._validate_structure(result)
                    except json.JSONDecodeError:
                        pass

            # Try finding JSON object anywhere in the text (brace-counting)
            brace_start = candidate.find('{')
            if brace_start >= 0:
                depth = 0
                for i in range(brace_start, len(candidate)):
                    if candidate[i] == '{':
                        depth += 1
                    elif candidate[i] == '}':
                        depth -= 1
                        if depth == 0:
                            block = candidate[brace_start:i + 1]
                            for attempt in [
                                block,
                                self._sanitize_backtick_strings(block),
                                self._sanitize_backtick_strings(
                                    self._sanitize_triple_quotes(block)
                                ),
                            ]:
                                try:
                                    result = json.loads(attempt)
                                    if isinstance(result, dict):
                                        return self._validate_structure(result)
                                except json.JSONDecodeError:
                                    pass
                            break

        raise OutputParseError(
            f"Could not parse structured JSON from worker output "
            f"(length={len(raw_output)}, starts with: {raw_output[:100]}...)"
        )

    def _validate_structure(self, data: dict) -> dict:
        """Validate and normalize the parsed structure."""
        # Ensure required keys exist with correct types
        result = {
            "files": [],
            "decisions": [],
            "notes": [],
            "tests_needed": [],
        }

        # Files
        files = data.get("files", [])
        if isinstance(files, list):
            for f in files:
                if isinstance(f, dict) and "path" in f and "content" in f:
                    result["files"].append({
                        "path": f["path"],
                        "content": f["content"],
                        "action": f.get("action", "create"),
                    })

        # Decisions
        decisions = data.get("decisions", [])
        if isinstance(decisions, list):
            for d in decisions:
                if isinstance(d, dict) and "description" in d:
                    result["decisions"].append({
                        "type": d.get("type", "minor"),
                        "description": d["description"],
                    })

        # Notes
        notes = data.get("notes", [])
        if isinstance(notes, list):
            result["notes"] = [str(n) for n in notes]

        # Tests needed
        tests = data.get("tests_needed", [])
        if isinstance(tests, list):
            result["tests_needed"] = [str(t) for t in tests]

        return result

    def apply(self, parsed_output: dict, task_id: str,
              worker_name: str = "unknown",
              task_module: Optional[str] = None) -> dict:
        """
        Apply parsed output: write files, log decisions, queue escalations.

        Returns summary:
        {
            "files_written": [...],
            "decisions_logged": [...],
            "escalations": [...],
            "notes": [...],
            "scope_violations": [...],
        }
        """
        self._files_written = []
        self._decisions_logged = []
        self._escalations = []
        self._scope_violations: List[dict] = []

        allowed_prefix = self._get_allowed_prefix(task_module) if task_module else None

        # 1. Write files
        for file_spec in parsed_output.get("files", []):
            violation = self._apply_file(
                file_spec, task_id=task_id, allowed_prefix=allowed_prefix
            )
            if violation:
                self._scope_violations.append(violation)

        # 2. Route decisions
        for decision in parsed_output.get("decisions", []):
            self._route_decision(decision, task_id, worker_name)

        return {
            "files_written": self._files_written,
            "decisions_logged": self._decisions_logged,
            "escalations": self._escalations,
            "notes": parsed_output.get("notes", []),
            "tests_needed": parsed_output.get("tests_needed", []),
            "scope_violations": self._scope_violations,
        }

    def _apply_file(self, file_spec: dict,
                    task_id: str = "unknown",
                    allowed_prefix: Optional[str] = None) -> Optional[dict]:
        """Write/update/delete a file in the project directory.

        Returns a violation dict if the file is out of module scope (file is
        NOT written in that case). Returns None on success or security block.
        """
        rel_path = file_spec["path"]
        action = file_spec.get("action", "create")
        content = file_spec.get("content", "")

        # ── Scope enforcement ──────────────────────────────────────────────
        if allowed_prefix is not None:
            filename = Path(rel_path).name
            if filename not in CROSS_CUTTING_FILES and not rel_path.startswith(allowed_prefix):
                violation = {
                    "rule_id": "R010",
                    "violation_tag": "TRAP",
                    "detail": (
                        f"out_of_scope_write: {rel_path!r} "
                        f"(allowed prefix: {allowed_prefix!r})"
                    ),
                    "path": rel_path,
                    "allowed_prefix": allowed_prefix,
                }
                logger.warning(
                    f"TRAP out_of_scope_write: task {task_id} wrote {rel_path!r} "
                    f"but scope is {allowed_prefix!r} — file skipped"
                )
                try:
                    queue_write(
                        operation="insert",
                        table="dac_tags",
                        params={
                            "task_id": task_id,
                            "tag_type": "TRAP",
                            "context": (
                                f"out_of_scope_write: {rel_path} "
                                f"(allowed prefix: {allowed_prefix})"
                            ),
                            "source_step": "output_parser_scope_check",
                            "source_worker": "output_parser",
                            "status": "open",
                        },
                        requester="output_parser",
                    )
                except RuntimeError as e:
                    logger.error(f"Failed to queue scope TRAP tag: {e}")
                return violation

        # ── Security: prevent path traversal ──────────────────────────────
        full_path = (self.project_path / rel_path).resolve()
        if not str(full_path).startswith(str(self.project_path.resolve())):
            logger.error(f"Path traversal blocked: {rel_path}")
            return None

        if action == "delete":
            if full_path.exists():
                full_path.unlink()
                logger.info(f"Deleted: {rel_path}")
                self._files_written.append({"path": rel_path, "action": "deleted"})
            return None

        # Create parent directories
        full_path.parent.mkdir(parents=True, exist_ok=True)

        # Write file
        full_path.write_text(content)
        size = len(content)
        logger.info(f"{'Created' if action == 'create' else 'Updated'}: {rel_path} ({size} bytes)")
        self._files_written.append({
            "path": rel_path,
            "action": action,
            "size": size,
        })
        return None

    def _route_decision(self, decision: dict, task_id: str, worker_name: str):
        """Route decision: minor->decision_logs, major->escalation queue."""
        dec_type = decision.get("type", "minor")
        description = decision.get("description", "")

        if dec_type == "major":
            # Major decisions get escalated
            self._escalations.append({
                "task_id": task_id,
                "type": "major_decision",
                "description": description,
                "worker": worker_name,
            })
            # Queue escalation via message bus
            try:
                queue_write(
                    operation="insert",
                    table="escalations",
                    params={
                        "task_id": task_id,
                        "escalation_type": "major_decision",
                        "escalated_by": worker_name,
                        "escalation_reason": description,
                        "context_data": json.dumps(decision),
                        "status": "pending",
                    },
                    requester="output_parser",
                )
            except RuntimeError as e:
                logger.critical(f"Failed to queue escalation: {e}")
            logger.warning(f"MAJOR DECISION escalated: {description[:100]}")
        else:
            # Minor decisions get logged
            self._decisions_logged.append(decision)
            try:
                queue_write(
                    operation="insert",
                    table="decision_logs",
                    params={
                        "task_id": task_id,
                        "decision_type": dec_type,
                        "decision_maker": worker_name,
                        "decision": description,
                        "reasoning": "Auto-logged from worker output",
                    },
                    requester="output_parser",
                )
            except RuntimeError as e:
                logger.error(f"Failed to log decision: {e}")

    def parse_and_apply(self, raw_output: str, task_id: str,
                         worker_name: str = "unknown",
                         task_module: Optional[str] = None) -> Tuple[dict, List[dict]]:
        """
        Convenience: parse + apply in one call.
        Returns (apply_summary, violations).
        Violations include both parse failures and out-of-scope writes.
        On parse failure, creates DaC TRAP tag and returns empty result.
        """
        violations = []
        try:
            parsed = self.parse(raw_output)
        except OutputParseError as e:
            # FER-CLI-001 FIX: Distinguish JSON decode failures (HAL tag) from
            # structural parse failures (TRAP tag) for precise DaC learning.
            is_json_error = "json" in str(e).lower() or "parse" in str(e).lower()
            tag_type = "HAL" if is_json_error else "TRAP"
            logger.error(f"Task {task_id}: output parse failed [{tag_type}] — {e}")
            violations.append({
                "rule_id": "R005",
                "violation_tag": tag_type,
                "detail": str(e),
            })
            try:
                queue_write(
                    operation="insert",
                    table="dac_tags",
                    params={
                        "task_id": task_id,
                        "tag_type": tag_type,
                        "context": f"Malformed output from {worker_name}: {str(e)[:500]}",
                        "source_step": "output_parse",
                        "source_worker": worker_name,
                        "status": "open",
                    },
                    requester="output_parser",
                )
            except RuntimeError as qe:
                logger.critical(f"Failed to queue {tag_type} tag: {qe}")
            return {"files_written": [], "decisions_logged": [],
                    "escalations": [], "notes": [], "tests_needed": [],
                    "scope_violations": []}, violations

        summary = self.apply(parsed, task_id, worker_name, task_module=task_module)
        # Merge any scope violations into the violations list so callers
        # see a unified signal and can trigger a targeted retry.
        violations.extend(summary.get("scope_violations", []))
        return summary, violations
