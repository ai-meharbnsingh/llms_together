"""
DaC Rules Engine — Autonomous Factory
═══════════════════════════════════════
Loads project-specific rules from rules/project_rules.json.
Injects rules into worker prompts. Auto-enforces automated rules.
Flags violations with DaC tags via message bus.
═══════════════════════════════════════
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from orchestration.database import ReadOnlyDB, queue_write

logger = logging.getLogger("factory.rules_engine")

# Default rules applied to ALL project types
DEFAULT_RULES = [
    {
        "id": "R001", "type": "API",
        "rule": "No worker may modify api_contract.json directly. Request changes via Orchestrator.",
        "enforcement": "automated", "violation_tag": "SER"
    },
    {
        "id": "R002", "type": "GIT",
        "rule": "All commits must reference task_id in message.",
        "enforcement": "automated", "violation_tag": "TRAP"
    },
    {
        "id": "R003", "type": "DEPENDENCY",
        "rule": "If dependency unclear, do NOT assume. Escalate to Kimi.",
        "enforcement": "kimi", "violation_tag": "TRAP"
    },
    {
        "id": "R004", "type": "CONTRACT",
        "rule": "All API responses must match types.json schemas.",
        "enforcement": "validator+kimi", "violation_tag": "SER"
    },
    {
        "id": "R005", "type": "OUTPUT",
        "rule": "All code output must be structured JSON: {files[], decisions[], notes[]}.",
        "enforcement": "automated", "violation_tag": "TRAP"
    },
    {
        "id": "R006", "type": "DECISION",
        "rule": "Minor decisions: log to decision_logs. Major decisions (schema/API/security): escalate.",
        "enforcement": "kimi", "violation_tag": "DOM"
    },
    {
        "id": "R007", "type": "SECURITY",
        "rule": "No hardcoded secrets. OWASP Top 10 compliance required.",
        "enforcement": "kimi", "violation_tag": "SER"
    },
    {
        "id": "R008", "type": "SYNC",
        "rule": "Git pull latest before starting any task.",
        "enforcement": "automated", "violation_tag": "ENV"
    },
    {
        "id": "R009", "type": "ACCESS",
        "rule": "Workers have full filesystem access to project folder. No human permission needed for operational tasks.",
        "enforcement": "automated", "violation_tag": "ENV"
    },
]


class RulesEngine:
    """
    Loads, validates, and enforces DaC rules for a project.

    Rules are loaded from rules/project_rules.json in the project directory.
    If no rules file exists, default rules are used and a rules file is generated.
    """

    def __init__(self, read_db: ReadOnlyDB = None):
        self.db = read_db
        self._rules: Dict[str, dict] = {}  # rule_id -> rule
        self._project_path: Optional[str] = None

    def load_rules(self, project_path: str) -> dict:
        """Load rules from project directory. Returns full rules document."""
        self._project_path = project_path
        rules_path = Path(project_path) / "rules" / "project_rules.json"

        if rules_path.exists():
            try:
                data = json.loads(rules_path.read_text())
                for rule in data.get("rules", []):
                    self._rules[rule["id"]] = rule
                logger.info(f"Loaded {len(self._rules)} rules from {rules_path}")
                return data
            except (json.JSONDecodeError, IOError, KeyError) as e:
                logger.error(f"Failed to load rules: {e}")

        # No rules file — use defaults
        self._rules = {r["id"]: r for r in DEFAULT_RULES}
        logger.info(f"Using {len(DEFAULT_RULES)} default rules (no project rules file)")
        return {"version": 1, "project_type": "web", "rules": DEFAULT_RULES}

    def generate_rules_file(self, project_path: str, project_type: str = "web") -> str:
        """Generate a rules/project_rules.json file with defaults + type-specific rules."""
        rules_dir = Path(project_path) / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)

        rules = list(DEFAULT_RULES)

        # Add project-type-specific rules
        type_rules = self._get_type_specific_rules(project_type)
        rules.extend(type_rules)

        doc = {
            "version": 1,
            "project_type": project_type,
            "rules": rules
        }

        rules_path = rules_dir / "project_rules.json"
        rules_path.write_text(json.dumps(doc, indent=2))
        logger.info(f"Generated rules file: {rules_path} ({len(rules)} rules)")

        self._rules = {r["id"]: r for r in rules}
        self._project_path = project_path
        return str(rules_path)

    def _get_type_specific_rules(self, project_type: str) -> List[dict]:
        """Get additional rules specific to project type."""
        type_rules = {
            "iot": [
                {
                    "id": "R100", "type": "SAFETY",
                    "rule": "Default to Simulation safety tier. Live mode requires per-task human confirmation.",
                    "enforcement": "automated", "violation_tag": "SER"
                },
                {
                    "id": "R101", "type": "MQTT",
                    "rule": "TLS encryption mandatory for production MQTT. Per-device auth required.",
                    "enforcement": "kimi", "violation_tag": "SER"
                },
                {
                    "id": "R102", "type": "FIRMWARE",
                    "rule": "All firmware must include watchdog timer and OTA update support.",
                    "enforcement": "kimi", "violation_tag": "DOM"
                },
            ],
            "plm": [
                {
                    "id": "R200", "type": "PRECISION",
                    "rule": "Use Decimal for financial calculations. Numpy for engineering. Never raw float for money.",
                    "enforcement": "kimi", "violation_tag": "SER"
                },
                {
                    "id": "R201", "type": "BOM",
                    "rule": "All BOM entries must have part_number, quantity, unit, and supplier.",
                    "enforcement": "validator+kimi", "violation_tag": "TRAP"
                },
            ],
            "mobile": [
                {
                    "id": "R300", "type": "OFFLINE",
                    "rule": "All critical features must work offline. Queue mutations for sync.",
                    "enforcement": "kimi", "violation_tag": "DOM"
                },
                {
                    "id": "R301", "type": "SECURITY",
                    "rule": "Use platform secure storage (Keychain/Keystore). Never store tokens in AsyncStorage.",
                    "enforcement": "kimi", "violation_tag": "SER"
                },
            ],
        }
        return type_rules.get(project_type, [])

    def get_rules_for_prompt(self, task_module: str = None) -> str:
        """Format rules as text for injection into worker prompts."""
        if not self._rules:
            return ""

        lines = ["## DaC Rules (MUST follow)"]
        for rule_id, rule in sorted(self._rules.items()):
            # Filter by module relevance if specified
            if task_module and rule.get("modules") and task_module not in rule["modules"]:
                continue
            enforcement = rule.get("enforcement", "manual")
            lines.append(
                f"- [{rule_id}] **{rule['type']}** ({enforcement}): {rule['rule']}"
            )
        return "\n".join(lines)

    def check_automated_rules(self, task_id: str, worker_output: dict,
                               commit_message: str = None) -> List[dict]:
        """
        Check automated enforcement rules against worker output.
        Returns list of violations: [{rule_id, rule, violation_tag, detail}]
        """
        violations = []

        for rule_id, rule in self._rules.items():
            if rule.get("enforcement") != "automated":
                continue

            violation = self._check_single_rule(rule, worker_output, commit_message)
            if violation:
                violations.append({
                    "rule_id": rule_id,
                    "rule": rule["rule"],
                    "violation_tag": rule.get("violation_tag", "TRAP"),
                    "detail": violation,
                })

        if violations:
            logger.warning(f"Task {task_id}: {len(violations)} rule violation(s) detected")
            # Queue DaC tags via message bus
            self._queue_violation_tags(task_id, violations)

        return violations

    def _check_single_rule(self, rule: dict, worker_output: dict,
                            commit_message: str = None) -> Optional[str]:
        """Check a single automated rule. Returns violation detail or None."""
        rule_id = rule["id"]
        rule_type = rule["type"]

        if rule_id == "R001":
            # No direct contract modification
            files = worker_output.get("files", [])
            for f in files:
                if "api_contract.json" in f.get("path", ""):
                    return f"Worker attempted to modify api_contract.json: {f['path']}"

        elif rule_id == "R002" and commit_message:
            # Commit must reference task_id
            if not re.search(r'task_\w+', commit_message):
                return f"Commit message missing task_id reference: {commit_message[:80]}"

        elif rule_id == "R005":
            # Output must be structured JSON
            if not isinstance(worker_output, dict):
                return "Worker output is not a dict"
            required = ["files"]
            missing = [k for k in required if k not in worker_output]
            if missing:
                return f"Worker output missing required keys: {missing}"

        elif rule_id == "R008":
            # Git pull check — handled externally by orchestrator
            pass

        return None

    def _queue_violation_tags(self, task_id: str, violations: List[dict]):
        """Queue DaC tag creation via message bus."""
        for v in violations:
            try:
                queue_write(
                    operation="insert", table="dac_tags",
                    params={
                        "task_id": task_id,
                        "tag_type": v["violation_tag"],
                        "context": f"Rule {v['rule_id']} violated: {v['detail']}",
                        "source_step": "rules_check",
                        "source_worker": "rules_engine",
                        "status": "open",
                    },
                    requester="rules_engine",
                )
            except RuntimeError as e:
                logger.error(f"Failed to queue DaC tag: {e}")

    def add_rule(self, rule: dict) -> bool:
        """Add a new rule dynamically. Persists to rules file if project_path set."""
        if "id" not in rule or "type" not in rule or "rule" not in rule:
            logger.error("Invalid rule: missing required fields (id, type, rule)")
            return False

        self._rules[rule["id"]] = rule
        if self._project_path:
            self._persist_rules()
        return True

    def _persist_rules(self):
        """Write current rules back to disk."""
        if not self._project_path:
            return
        rules_path = Path(self._project_path) / "rules" / "project_rules.json"
        rules_path.parent.mkdir(parents=True, exist_ok=True)

        doc = {
            "version": 1,
            "project_type": "web",
            "rules": list(self._rules.values())
        }
        rules_path.write_text(json.dumps(doc, indent=2))
        logger.info(f"Persisted {len(self._rules)} rules to {rules_path}")
