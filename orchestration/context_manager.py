"""
Context Manager — Autonomous Factory
═══════════════════════════════════════
Builds rich context for worker prompts during autonomous execution.
Injects: conversation history, protocol rules, contracts, learning log entries.
Used by both chat mode (orchestrator) and autonomous execution (phase loop).
═══════════════════════════════════════
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("factory.context_manager")


class ContextManager:
    """
    Builds context payloads for worker prompts.

    In chat mode: delegates to orchestrator's existing history logic.
    In autonomous mode: builds structured prompts with protocol + contracts + rules + learning.
    """

    def __init__(self, working_dir: str, read_db=None):
        self.working_dir = Path(working_dir).expanduser()
        self.db = read_db
        self._protocol_cache: Dict[str, str] = {}
        self._rules_cache: Optional[dict] = None

    def load_protocol(self, project_type: str) -> str:
        """Load protocol file for project type. Cached after first load.

        Search order:
          1. self.working_dir/protocols/{type}.md  (project-local override)
          2. factory_dir/protocols/{type}.md        (built-in)
          3. working_dir/protocols/web.md           (fallback)
          4. factory_dir/protocols/web.md           (fallback)
        """
        if project_type in self._protocol_cache:
            return self._protocol_cache[project_type]

        factory_dir = Path(__file__).resolve().parent.parent
        candidates = [
            self.working_dir / "protocols" / f"{project_type}.md",
            factory_dir / "protocols" / f"{project_type}.md",
        ]
        proto_path = next((p for p in candidates if p.exists()), None)

        if proto_path is None:
            logger.warning(
                f"Protocol file not found for type '{project_type}', falling back to web.md"
            )
            fallbacks = [
                self.working_dir / "protocols" / "web.md",
                factory_dir / "protocols" / "web.md",
            ]
            proto_path = next((p for p in fallbacks if p.exists()), None)

        if proto_path is not None:
            content = proto_path.read_text()
            self._protocol_cache[project_type] = content
            logger.info(f"Loaded protocol: {proto_path.name} ({len(content)} chars)")
            return content

        logger.warning("No protocol files found")
        return ""

    def load_rules(self, project_path: str) -> dict:
        """Load project rules from rules/project_rules.json."""
        rules_path = Path(project_path) / "rules" / "project_rules.json"
        if rules_path.exists():
            try:
                rules = json.loads(rules_path.read_text())
                self._rules_cache = rules
                return rules
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load rules: {e}")
        return {"version": 1, "rules": []}

    def load_contracts(self, project_path: str) -> dict:
        """Load all contract files from contracts/ directory."""
        contracts_dir = Path(project_path) / "contracts"
        result = {}

        if not contracts_dir.exists():
            return result

        for contract_file in contracts_dir.iterdir():
            if contract_file.suffix in ('.json', '.sql'):
                try:
                    result[contract_file.name] = contract_file.read_text()
                except IOError as e:
                    logger.error(f"Failed to load contract {contract_file}: {e}")

        return result

    # Quality thresholds — mirror LearningLog constants (R11).
    _MIN_OCCURRENCES = 2
    _EXPIRY_DAYS = 90

    @staticmethod
    def _is_qualified_learning(entry: dict) -> bool:
        """Return True if entry meets injection quality bar (R11)."""
        occurrence = entry.get("occurrence_count", 1)
        validated = entry.get("validated", False)
        if not (occurrence >= ContextManager._MIN_OCCURRENCES or validated):
            return False
        created_at = entry.get("created_at")
        if created_at:
            try:
                if isinstance(created_at, str):
                    dt = datetime.fromisoformat(created_at.replace(" ", "T").rstrip("Z"))
                else:
                    dt = created_at
                if datetime.utcnow() - dt > timedelta(days=ContextManager._EXPIRY_DAYS):
                    return False
            except (ValueError, TypeError):
                pass
        return True

    def get_relevant_learnings(self, project_type: str, keywords: List[str],
                                limit: int = 5) -> List[dict]:
        """Fetch relevant learning log entries for similar past bugs.
        Filters to occurrence_count >= 2 OR validated=True, within last 90 days (R11).
        """
        if not self.db:
            return []

        learnings = []
        seen_ids = set()
        for kw in keywords[:3]:  # Search top 3 keywords
            entries = self.db.get_learning_log(
                project_type=project_type, keywords=kw, limit=limit * 3
            )
            for entry in entries:
                eid = entry.get("log_id")
                if eid not in seen_ids and self._is_qualified_learning(entry):
                    seen_ids.add(eid)
                    learnings.append(entry)

        return learnings[:limit]

    def build_task_prompt(self, task: dict, project: dict,
                          project_path: str, relevant_files: List[str] = None) -> str:
        """
        Build a complete task prompt for a worker during autonomous execution.

        Includes:
        - Task description + acceptance criteria
        - Relevant contract sections
        - DaC rules
        - Protocol context
        - Relevant file contents
        - Learning log entries for similar past issues
        """
        project_type = project.get("project_type", "web")
        sections = []

        # 1. Task description
        sections.append(f"# TASK: {task['task_id']}")
        sections.append(f"**Module:** {task['module']}")
        sections.append(f"**Phase:** {task['phase']}")
        sections.append(f"**Complexity:** {task.get('complexity', 'unknown')}")
        sections.append(f"\n## Description\n{task['description']}")

        # 2. Protocol context
        protocol = self.load_protocol(project_type)
        if protocol:
            sections.append(f"\n## Protocol ({project_type})\n{protocol}")

        # 3. DaC Rules
        rules = self.load_rules(project_path)
        if rules.get("rules"):
            rules_text = "\n".join(
                f"- [{r['id']}] ({r['type']}) {r['rule']}"
                for r in rules["rules"]
            )
            sections.append(f"\n## Rules\n{rules_text}")

        # 4. Contracts (only relevant sections)
        contracts = self.load_contracts(project_path)
        if contracts:
            contract_text = ""
            for name, content in contracts.items():
                contract_text += f"\n### {name}\n```\n{content}\n```\n"
            sections.append(f"\n## Contracts (LOCKED — do not modify){contract_text}")

        # 5. Relevant file contents
        if relevant_files:
            file_text = ""
            for fpath in relevant_files[:10]:  # Max 10 files
                fp = Path(fpath)
                if fp.exists() and fp.stat().st_size < 50000:  # Max 50KB per file
                    try:
                        file_text += f"\n### {fp.name}\n```\n{fp.read_text()}\n```\n"
                    except IOError:
                        pass
            if file_text:
                sections.append(f"\n## Current Code{file_text}")

        # 6. Learning log (past fixes for similar issues)
        task_keywords = task.get("description", "").split()[:5]
        learnings = self.get_relevant_learnings(project_type, task_keywords)
        if learnings:
            learning_text = ""
            for entry in learnings:
                learning_text += (
                    f"\n- **Bug:** {entry['bug_description']}\n"
                    f"  **Root cause:** {entry['root_cause']}\n"
                    f"  **Fix:** {entry['fix_applied']}\n"
                )
            sections.append(f"\n## Past Learnings (avoid repeating){learning_text}")

        # 7. Output format instruction
        sections.append("""
## Required Output Format
You MUST return valid JSON with this exact structure:
```json
{
  "files": [
    {"path": "relative/path/to/file.py", "content": "full file content", "action": "create|update"}
  ],
  "decisions": [
    {"type": "minor|major", "description": "what was decided and why"}
  ],
  "notes": ["any implementation notes or concerns"],
  "tests_needed": ["description of tests that should be written for this code"]
}
```
""")

        # 8. File scope + permissions
        task_module = task.get("module", "")
        if task_module:
            p = Path(task_module)
            module_dir = str(p.parent) if p.suffix else str(p).rstrip("/")
            sections.append(f"""
## File Scope (CRITICAL)
You are responsible for module: `{task_module}`
You MUST ONLY create or modify files within: `{module_dir}/`

Cross-cutting files you MAY also write if legitimately required:
  requirements.txt, package.json, package-lock.json, pyproject.toml, setup.py,
  Dockerfile, docker-compose.yml, .env.example, README.md, .gitignore,
  tsconfig.json, vite.config.ts, tailwind.config.js

Writing files OUTSIDE your module scope is a TRAP violation — those files will
be rejected and discarded. Stay within `{module_dir}/`.

Only escalate for DECISIONS or BUG FIXES that need human judgment.
""")
        else:
            sections.append("""
## Permissions
You have filesystem access to the project folder for files related to your task.
Only escalate for DECISIONS or BUG FIXES that need human judgment.
""")

        return "\n".join(sections)

    def build_gate_prompt(self, task: dict, code_output: dict,
                          contracts: dict, validator_report: dict) -> str:
        """
        Build a prompt for Kimi quality gate review.

        Kimi receives: code + tests + AC + contract + validator report.
        Returns: {verdict, confidence, issues[], dac_tags[]}
        """
        sections = [
            f"# Quality Gate Review: {task['task_id']}",
            f"\n## Task\n{task['description']}",
        ]

        # Files produced
        if code_output.get("files"):
            files_text = ""
            for f in code_output["files"]:
                files_text += f"\n### {f['path']}\n```\n{f.get('content', '')}\n```\n"
            sections.append(f"\n## Code Output{files_text}")

        # Contract compliance
        if contracts:
            sections.append("\n## Contracts")
            for name, content in contracts.items():
                sections.append(f"### {name}\n```\n{content}\n```")

        # Auto-validator report
        if validator_report:
            sections.append(f"\n## Auto-Validator Report\n```json\n{json.dumps(validator_report, indent=2)}\n```")

        # Decisions made
        if code_output.get("decisions"):
            dec_text = "\n".join(
                f"- [{d['type']}] {d['description']}"
                for d in code_output["decisions"]
            )
            sections.append(f"\n## Decisions Made\n{dec_text}")

        sections.append("""
## Your Task
Review this code output for quality, correctness, and contract compliance.
Return JSON:
```json
{
  "verdict": "APPROVED|REJECTED",
  "confidence": 0.0-1.0,
  "issues": ["list of issues found"],
  "dac_tags": ["TRAP|SER|DOM|HRO|HAL|ENV for each issue"],
  "suggestions": ["optional improvement suggestions"]
}
```
Only APPROVE if confidence > 0.9 and no critical issues found.
""")

        return "\n".join(sections)

    def invalidate_cache(self):
        """Clear cached protocols and rules (e.g., after hot-reload)."""
        self._protocol_cache.clear()
        self._rules_cache = None
        logger.info("Context manager cache invalidated")
