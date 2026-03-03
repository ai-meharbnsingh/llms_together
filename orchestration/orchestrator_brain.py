"""
Orchestrator Brain — LLM-Powered Intelligence Layer
════════════════════════════════════════════════════
Wraps deepseek/qwen workers with orchestrator-specific reasoning prompts.
Called at every decision point where the orchestrator currently retries
mechanically or fails silently.

Every method falls back to a deterministic heuristic if the LLM call fails,
so the brain never blocks the pipeline.
"""

import json
import logging
from typing import Dict, List, Optional

from orchestration.database import ReadOnlyDB
from orchestration.role_router import RoleRouter

logger = logging.getLogger("factory.brain")

# Role key used in factory_config.json → roles / local_roles
BRAIN_ROLE = "orchestrator_reasoning"


class OrchestratorBrain:
    """Intelligence layer between the orchestrator and its decision points."""

    def __init__(self, role_router: RoleRouter, db: ReadOnlyDB):
        self.router = role_router
        self.db = db

    # ──────────────────────────────────────────────
    # Core: send a reasoning request to the LLM
    # ──────────────────────────────────────────────

    async def _think(self, prompt: str, system: str) -> Optional[dict]:
        """Send reasoning request to deepseek (primary) or qwen (fallback).

        Returns parsed JSON dict on success, None on failure.
        """
        worker = self.router.get_worker(BRAIN_ROLE)
        if not worker:
            logger.warning("OrchestratorBrain: no worker for role %s", BRAIN_ROLE)
            return None

        try:
            result = await worker.send_message(prompt, system_prompt=system)
            if not result.get("success"):
                logger.warning("Brain LLM call failed: %s", result.get("error", "unknown"))
                return None
            return self._parse_json(result.get("response", ""))
        except Exception as exc:
            logger.warning("Brain _think exception (non-fatal): %s", exc)
            return None

    @staticmethod
    def _parse_json(text: str) -> Optional[dict]:
        """Extract JSON object from LLM response text."""
        # Try direct parse first
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass
        # Try to find JSON within markdown fences
        import re
        m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
        if m:
            try:
                return json.loads(m.group(1))
            except (json.JSONDecodeError, TypeError):
                pass
        # Try brace extraction
        start = text.find("{")
        if start >= 0:
            depth = 0
            for i, ch in enumerate(text[start:], start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except (json.JSONDecodeError, TypeError):
                            break
        return None

    # ──────────────────────────────────────────────
    # 1. Gate Rejection Analysis (HIGHEST IMPACT)
    # ──────────────────────────────────────────────

    async def analyze_rejection(
        self,
        task: dict,
        gate_result: dict,
        rejection_count: int,
        past_attempts: List[dict] = None,
    ) -> dict:
        """Analyze why a quality gate rejected a task and suggest a strategy.

        Returns:
            {
                "diagnosis": str,
                "strategy": "targeted_retry" | "switch_worker" | "escalate_to_human",
                "retry_guidance": str,         # extra prompt text for retry
                "human_summary": str,          # readable summary for escalation
            }
        """
        issues = gate_result.get("issues", [])
        past_text = ""
        if past_attempts:
            for i, att in enumerate(past_attempts, 1):
                past_text += f"\nAttempt {i} issues: {att.get('issues', [])}"

        prompt = (
            f"You are the Orchestrator Brain analyzing a quality-gate rejection.\n\n"
            f"TASK: {task.get('description', '')}\n"
            f"MODULE: {task.get('module', '')}\n"
            f"REJECTION #{rejection_count}\n"
            f"ISSUES: {json.dumps(issues)}\n"
            f"{past_text}\n\n"
            f"Respond with a JSON object containing:\n"
            f'- "diagnosis": one-sentence root cause\n'
            f'- "strategy": one of "targeted_retry", "switch_worker", "escalate_to_human"\n'
            f'  (use "targeted_retry" if fixable with better prompt guidance,\n'
            f'   "switch_worker" if the worker repeatedly makes the same mistake,\n'
            f'   "escalate_to_human" if this requires human judgement)\n'
            f'- "retry_guidance": specific instruction to prepend to the retry prompt\n'
            f'- "human_summary": 2-3 sentence summary for the human dashboard\n\n'
            f"Return ONLY the JSON object, no other text."
        )

        result = await self._think(
            prompt,
            system="You are the reasoning core of an autonomous software factory. "
                   "Analyze gate rejections and recommend the best recovery strategy. "
                   "Always respond with valid JSON.",
        )

        if result and isinstance(result.get("strategy"), str):
            # Validate strategy field
            valid = {"targeted_retry", "switch_worker", "escalate_to_human"}
            if result["strategy"] not in valid:
                result["strategy"] = "targeted_retry"
            return result

        # Deterministic fallback
        return self._fallback_rejection(task, gate_result, rejection_count)

    @staticmethod
    def _fallback_rejection(task: dict, gate_result: dict, rejection_count: int) -> dict:
        issues_text = "; ".join(
            str(i) for i in gate_result.get("issues", [])[:3]
        )
        if rejection_count >= 2:
            return {
                "diagnosis": f"Task rejected {rejection_count}x — issues: {issues_text}",
                "strategy": "escalate_to_human",
                "retry_guidance": "",
                "human_summary": (
                    f"Task '{task.get('description', '')[:80]}' has been rejected "
                    f"{rejection_count} times. Issues: {issues_text}. "
                    f"Requires human decision."
                ),
            }
        return {
            "diagnosis": f"Gate issues: {issues_text}",
            "strategy": "targeted_retry",
            "retry_guidance": (
                f"CRITICAL: Your previous attempt was REJECTED. "
                f"Fix these specific issues: {issues_text}"
            ),
            "human_summary": "",
        }

    # ──────────────────────────────────────────────
    # 2. Task Failure Escalation (HIGH IMPACT)
    # ──────────────────────────────────────────────

    async def compose_escalation(
        self,
        task: dict,
        gate_results: List[dict],
        dac_tags: List[dict] = None,
    ) -> dict:
        """Compose a rich escalation after repeated task failures.

        Returns:
            {
                "summary": str,
                "root_cause": str,
                "options": [{"label": str, "action": str, ...}, ...]
            }
        """
        all_issues = []
        for gr in gate_results:
            all_issues.extend(gr.get("issues", []))

        tag_text = ""
        if dac_tags:
            tag_text = "\nDaC TAGS: " + json.dumps(
                [{"type": t.get("tag_type"), "context": t.get("context", "")[:100]}
                 for t in dac_tags[:5]]
            )

        prompt = (
            f"You are the Orchestrator Brain composing an escalation for a human operator.\n\n"
            f"TASK: {task.get('description', '')}\n"
            f"MODULE: {task.get('module', '')}\n"
            f"ALL GATE ISSUES across attempts: {json.dumps(all_issues[:10])}\n"
            f"{tag_text}\n\n"
            f"Respond with a JSON object containing:\n"
            f'- "summary": 2-3 sentence analysis of what went wrong\n'
            f'- "root_cause": one-sentence root cause\n'
            f'- "options": array of 3 objects, each with:\n'
            f'    - "label": short action name\n'
            f'    - "action": one of "retry_with_different_worker", "simplify_task", "skip"\n'
            f'    - "detail": explanation of this option\n\n'
            f"Return ONLY the JSON object."
        )

        result = await self._think(
            prompt,
            system="You are the reasoning core of an autonomous software factory. "
                   "Compose clear, actionable escalations for human operators. "
                   "Always respond with valid JSON.",
        )

        if result and isinstance(result.get("options"), list):
            return result

        # Deterministic fallback
        desc = task.get("description", "")[:80]
        return {
            "summary": (
                f"Task '{desc}' failed after all retry attempts. "
                f"Issues: {'; '.join(str(i) for i in all_issues[:3])}."
            ),
            "root_cause": "Repeated quality gate rejections — worker unable to self-correct.",
            "options": [
                {"label": "Retry with different worker", "action": "retry_with_different_worker",
                 "detail": "Try deepseek instead of qwen (or vice versa)"},
                {"label": "Simplify task scope", "action": "simplify_task",
                 "detail": "Break into smaller subtasks and retry"},
                {"label": "Skip and continue", "action": "skip",
                 "detail": "Mark task as skipped and move on to next tasks"},
            ],
        }

    # ──────────────────────────────────────────────
    # 3. Dependency Deadlock Resolution (MEDIUM)
    # ──────────────────────────────────────────────

    async def resolve_deadlock(
        self,
        pending_tasks: Dict[str, dict],
        dep_graph: Dict[str, List[str]],
        completed: set,
    ) -> dict:
        """Decide which task to run when a dependency deadlock is detected.

        Returns:
            {
                "analysis": str,
                "resolution": "run_task" | "reorder" | "escalate",
                "task_to_run": str,
                "reason": str,
            }
        """
        tasks_info = [
            {"id": tid, "desc": t.get("description", "")[:80],
             "deps": dep_graph.get(tid, [])}
            for tid, t in pending_tasks.items()
        ]

        prompt = (
            f"You are the Orchestrator Brain resolving a dependency deadlock.\n\n"
            f"PENDING TASKS:\n{json.dumps(tasks_info, indent=2)}\n\n"
            f"COMPLETED TASK IDS: {list(completed)}\n\n"
            f"None of these tasks can start because they all depend on uncompleted "
            f"tasks. Analyze the dependency graph and decide which task to run first "
            f"to break the deadlock.\n\n"
            f"Respond with a JSON object containing:\n"
            f'- "analysis": one-sentence explanation of the deadlock\n'
            f'- "resolution": "run_task"\n'
            f'- "task_to_run": the task_id to run first\n'
            f'- "reason": why this task should go first\n\n'
            f"Return ONLY the JSON object."
        )

        result = await self._think(
            prompt,
            system="You are the reasoning core of an autonomous software factory. "
                   "Resolve dependency deadlocks by identifying the best task to run first. "
                   "Always respond with valid JSON.",
        )

        task_ids = list(pending_tasks.keys())
        if result and result.get("task_to_run") in pending_tasks:
            return result

        # Deterministic fallback: pick the task with the fewest dependencies
        best_tid = min(task_ids, key=lambda t: len(dep_graph.get(t, [])))
        return {
            "analysis": f"Circular dependency among {len(task_ids)} tasks.",
            "resolution": "run_task",
            "task_to_run": best_tid,
            "reason": f"Task has fewest unsatisfied dependencies ({len(dep_graph.get(best_tid, []))})",
        }

    # ──────────────────────────────────────────────
    # 4. Smart Worker Routing on Retry (MEDIUM)
    # ──────────────────────────────────────────────

    async def suggest_worker(
        self,
        task: dict,
        previous_worker: str,
        failure_history: List[dict] = None,
    ) -> dict:
        """Suggest which worker to use for a retry.

        Returns:
            {
                "worker": "deepseek" | "qwen" | ...,
                "reason": str,
            }
        """
        hist_text = ""
        if failure_history:
            hist_text = "\nFAILURE HISTORY:\n" + json.dumps(
                [{"worker": h.get("worker"), "error": str(h.get("error", ""))[:100]}
                 for h in failure_history[:5]]
            )

        prompt = (
            f"You are the Orchestrator Brain deciding which LLM worker to assign.\n\n"
            f"TASK: {task.get('description', '')[:200]}\n"
            f"MODULE: {task.get('module', '')}\n"
            f"PREVIOUS WORKER: {previous_worker} (FAILED)\n"
            f"{hist_text}\n\n"
            f"Available local workers: deepseek (16B, strong at complex code), "
            f"qwen (7B, fast, good at simple code).\n\n"
            f"Respond with a JSON object:\n"
            f'- "worker": worker name to try next\n'
            f'- "reason": one-sentence explanation\n\n'
            f"Return ONLY the JSON object."
        )

        result = await self._think(
            prompt,
            system="You are the reasoning core of an autonomous software factory. "
                   "Recommend the best worker for a retry based on failure history. "
                   "Always respond with valid JSON.",
        )

        if result and isinstance(result.get("worker"), str):
            return result

        # Deterministic fallback: swap to the other worker
        alt = "deepseek" if previous_worker == "qwen" else "qwen"
        return {
            "worker": alt,
            "reason": f"Previous worker '{previous_worker}' failed — switching to '{alt}'",
        }

    # ──────────────────────────────────────────────
    # 5. Escalation Resolution Feedback Loop (LOW)
    # ──────────────────────────────────────────────

    async def interpret_resolution(
        self,
        escalation: dict,
        human_decision: str,
        task_context: Optional[dict] = None,
    ) -> dict:
        """Interpret a human's escalation resolution and decide next action.

        Returns:
            {
                "action": "retry_task" | "skip_task" | "modify_prompt" | "log_learning",
                "learning": str,
                "prompt_modifier": str,
            }
        """
        ctx_text = ""
        if task_context:
            ctx_text = (
                f"\nTASK CONTEXT:\n"
                f"  Description: {task_context.get('description', '')[:200]}\n"
                f"  Module: {task_context.get('module', '')}\n"
            )

        prompt = (
            f"You are the Orchestrator Brain interpreting a human operator's decision.\n\n"
            f"ESCALATION TYPE: {escalation.get('escalation_type', 'unknown')}\n"
            f"ESCALATION REASON: {escalation.get('escalation_reason', '')}\n"
            f"HUMAN DECISION: {human_decision}\n"
            f"{ctx_text}\n\n"
            f"Based on the human's decision, determine what action to take.\n\n"
            f"Respond with a JSON object containing:\n"
            f'- "action": one of "retry_task", "skip_task", "modify_prompt", "log_learning"\n'
            f'- "learning": what to record in the learning log (or empty string)\n'
            f'- "prompt_modifier": text to prepend to future prompts for similar tasks (or empty string)\n\n'
            f"Return ONLY the JSON object."
        )

        result = await self._think(
            prompt,
            system="You are the reasoning core of an autonomous software factory. "
                   "Interpret human decisions and translate them into actionable system instructions. "
                   "Always respond with valid JSON.",
        )

        if result and isinstance(result.get("action"), str):
            valid_actions = {"retry_task", "skip_task", "modify_prompt", "log_learning"}
            if result["action"] not in valid_actions:
                result["action"] = "log_learning"
            return result

        # Deterministic fallback
        decision_lower = human_decision.lower()
        if any(w in decision_lower for w in ("retry", "redo", "try again")):
            action = "retry_task"
        elif any(w in decision_lower for w in ("skip", "ignore", "move on")):
            action = "skip_task"
        else:
            action = "log_learning"

        return {
            "action": action,
            "learning": f"Human resolved escalation: {human_decision[:200]}",
            "prompt_modifier": "",
        }
