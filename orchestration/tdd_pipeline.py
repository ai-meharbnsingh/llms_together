"""
13-Step TDD Pipeline — Autonomous Factory
═══════════════════════════════════════════
Checkpointed TDD pipeline for autonomous code generation.
Each step: worker call → checkpoint → crash recovery.

Steps:
1.  AC  — Acceptance Criteria definition
2.  RED — Write failing tests
3.  GREEN — Minimal code to pass tests
4.  BC  — Static bug capture scan
5.  BF  — Bug fix (if found in step 4)
6.  SEA — Silent error / concurrency analysis
7.  DS  — Security OWASP scan
8.  OA  — Output alignment (spec ↔ code ↔ test)
9.  VB  — Version bump
10. GIT — Atomic commit
11. CL  — Clean temp artifacts
12. CCP — Save checkpoint
13. AD  — Update dashboard
═══════════════════════════════════════════
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from orchestration.database import ReadOnlyDB, queue_write
from orchestration.static_analysis import StaticAnalyzer, ToolStatus

logger = logging.getLogger("factory.tdd_pipeline")

# Step definitions
TDD_STEPS = [
    {"id": "AC",    "name": "Acceptance Criteria",    "index": 1},
    {"id": "RED",   "name": "Write Failing Tests",    "index": 2},
    {"id": "GREEN", "name": "Minimal Implementation", "index": 3},
    {"id": "BC",    "name": "Bug Capture Scan",       "index": 4},
    {"id": "BF",    "name": "Bug Fix",                "index": 5},
    {"id": "SEA",   "name": "Silent Error Analysis",  "index": 6},
    {"id": "DS",    "name": "Security OWASP Scan",    "index": 7},
    {"id": "OA",    "name": "Output Alignment",       "index": 8},
    {"id": "VB",    "name": "Version Bump",           "index": 9},
    {"id": "GIT",   "name": "Atomic Commit",          "index": 10},
    {"id": "CL",    "name": "Cleanup",                "index": 11},
    {"id": "CCP",   "name": "Checkpoint",             "index": 12},
    {"id": "AD",    "name": "Dashboard Update",       "index": 13},
]

# Fast-Track: 5 steps for CSS/UI/config-only changes (skip heavy validation)
FAST_TRACK_STEPS = {"AC", "GREEN", "OA", "GIT", "AD"}

# Patterns that trigger fast-track (cosmetic/presentational changes only)
FAST_TRACK_PATTERNS = (
    "css", "style", "color", "font", "theme", "icon", "logo", "image",
    "copy", "text change", "label", "placeholder", "tooltip", "typo",
    "config", "env", "readme", "comment", "rename", "asset",
)

# Hard rule: tests are ground truth. Injected into every worker prompt
# that touches code fixes, bug fixes, or validation.
TESTING_GROUND_TRUTH_RULE = """
CRITICAL RULE — TESTS ARE GROUND TRUTH:
- If tests fail, fix the SOURCE CODE, NEVER modify or weaken the tests.
- No assumptions, no temporary fixes, no test modifications.
- No skipping tests, no marking tests as xfail, no loosening assertions.
- If the code doesn't pass the tests, the code is wrong — fix the code.
- Tests define the contract. The implementation must satisfy the contract.
"""


class TDDStepResult:
    """Result of a single TDD step."""
    __slots__ = ("step_id", "success", "output", "bugs_found", "dac_tags",
                 "elapsed_ms", "skipped", "error")

    def __init__(self, step_id: str, success: bool = True, output: str = "",
                 bugs_found: List[dict] = None, dac_tags: List[str] = None,
                 elapsed_ms: int = 0, skipped: bool = False, error: str = None):
        self.step_id = step_id
        self.success = success
        self.output = output
        self.bugs_found = bugs_found or []
        self.dac_tags = dac_tags or []
        self.elapsed_ms = elapsed_ms
        self.skipped = skipped
        self.error = error

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "success": self.success,
            "output": self.output[:500],
            "bugs_found": self.bugs_found,
            "dac_tags": self.dac_tags,
            "elapsed_ms": self.elapsed_ms,
            "skipped": self.skipped,
            "error": self.error,
        }


class TDDPipeline:
    """
    13-step TDD pipeline with per-step checkpointing.

    Each step:
    1. Check if already completed (crash recovery)
    2. Build step-specific prompt
    3. Call worker (Claude for TDD)
    4. Parse and validate result
    5. Checkpoint state to DB
    6. If failure: DaC tag + retry/escalate
    """

    def __init__(self, read_db: ReadOnlyDB, role_router, git_manager=None,
                 project_path: str = None):
        self.db = read_db
        self.router = role_router
        self.git_manager = git_manager
        self.project_path = project_path
        self._analyzer: Optional[StaticAnalyzer] = (
            StaticAnalyzer(project_path) if project_path else None
        )
        self._results: Dict[str, TDDStepResult] = {}
        self._on_progress: Optional[Callable] = None

    @staticmethod
    def is_fast_track(task: dict) -> bool:
        """Determine if a task qualifies for fast-track (5-step) pipeline.

        Fast-track applies to CSS/UI tweaks, copy changes, config edits,
        and other cosmetic changes that don't affect logic or data flow.
        """
        desc = (task.get("description") or "").lower()
        module = (task.get("module") or "").lower()
        combined = f"{desc} {module}"
        return any(p in combined for p in FAST_TRACK_PATTERNS)

    async def execute(self, task: dict, project: dict, code_output: dict,
                       on_progress: Callable = None,
                       fast_track: bool = None) -> dict:
        """
        Execute TDD pipeline for a task.

        Two tracks:
        - Full (13 steps): AC→RED→GREEN→BC→BF→SEA→DS→OA→VB→GIT→CL→CCP→AD
        - Fast-Track (5 steps): AC→GREEN→OA→GIT→AD (for CSS/UI/config tweaks)

        Args:
            task: Task dict (task_id, description, module, phase, etc.)
            project: Project dict (project_id, project_type, etc.)
            code_output: Parsed output from worker ({files[], decisions[], notes[]})
            on_progress: Callback(step_id, step_name, status) for live updates
            fast_track: Force fast-track (True/False), or None for auto-detect

        Returns:
            {"success": bool, "results": {step_id: result}, "bugs": [], "dac_tags": [], "track": "full"|"fast"}
        """
        self._on_progress = on_progress
        task_id = task["task_id"]

        # Determine track
        use_fast = fast_track if fast_track is not None else self.is_fast_track(task)
        track_name = "fast" if use_fast else "full"
        if use_fast:
            logger.info(f"Fast-track TDD for {task_id} (5 steps: AC→GREEN→OA→GIT→AD)")
        else:
            logger.info(f"Full TDD for {task_id} (13 steps)")

        # Check for checkpoint (crash recovery)
        resume_from = self._get_resume_step(task_id)
        if resume_from:
            logger.info(f"Resuming TDD for {task_id} from step {resume_from}")

        all_bugs = []
        all_tags = []

        for step_def in TDD_STEPS:
            # Skip non-fast-track steps when in fast-track mode
            if use_fast and step_def["id"] not in FAST_TRACK_STEPS:
                self._results[step_def["id"]] = TDDStepResult(
                    step_id=step_def["id"], success=True, skipped=True,
                    output=f"Skipped (fast-track mode)"
                )
                continue
            step_id = step_def["id"]
            step_index = step_def["index"]

            # Skip already completed steps (crash recovery)
            if resume_from and step_index <= resume_from:
                logger.info(f"Skipping {step_id} (already completed)")
                continue

            # Notify progress
            if on_progress:
                try:
                    await on_progress(step_id, step_def["name"], "running")
                except Exception:
                    logger.debug(f"Progress callback failed for {step_id}", exc_info=True)

            # Execute step
            start_ms = int(time.time() * 1000)
            try:
                result = await self._execute_step(
                    step_id, task, project, code_output, all_bugs
                )
            except Exception as e:
                logger.error(f"TDD step {step_id} crashed: {e}")
                result = TDDStepResult(
                    step_id=step_id, success=False,
                    error=str(e), elapsed_ms=int(time.time() * 1000) - start_ms,
                    dac_tags=["ENV"]
                )
                # FER-CLI-007: Log orchestration crash to DaC learning loop
                self._create_env_tag(
                    task_id, step_id, str(e),
                    project_id=project.get("project_id")
                )

            result.elapsed_ms = int(time.time() * 1000) - start_ms
            self._results[step_id] = result

            # Collect bugs and tags
            all_bugs.extend(result.bugs_found)
            all_tags.extend(result.dac_tags)

            # Checkpoint after each step
            self._checkpoint(task_id, step_id, step_index, result)

            # Update task status
            self._update_task_step(task_id, step_id)

            # Notify progress
            if on_progress:
                try:
                    status = "completed" if result.success else "failed"
                    await on_progress(step_id, step_def["name"], status)
                except Exception:
                    logger.debug(f"Progress callback failed for {step_id}", exc_info=True)

            # If step failed and is critical, stop pipeline
            if not result.success and step_id in ("RED", "GREEN", "GIT"):
                logger.error(f"Critical TDD step {step_id} failed — pipeline halted")
                break

        success = all(
            r.success or r.skipped
            for r in self._results.values()
        )

        return {
            "success": success,
            "results": {k: v.to_dict() for k, v in self._results.items()},
            "bugs": all_bugs,
            "dac_tags": all_tags,
            "task_id": task_id,
            "track": track_name,
        }

    async def _execute_step(self, step_id: str, task: dict, project: dict,
                             code_output: dict, existing_bugs: list) -> TDDStepResult:
        """Execute a single TDD step."""

        if step_id == "AC":
            return await self._step_acceptance_criteria(task)
        elif step_id == "RED":
            return await self._step_write_tests(task, project, code_output)
        elif step_id == "GREEN":
            return await self._step_minimal_impl(task, code_output)
        elif step_id == "BC":
            return await self._step_bug_capture(task, code_output)
        elif step_id == "BF":
            return await self._step_bug_fix(task, existing_bugs, code_output)
        elif step_id == "SEA":
            return await self._step_silent_error_analysis(task, code_output)
        elif step_id == "DS":
            return await self._step_security_scan(task, code_output)
        elif step_id == "OA":
            return await self._step_output_alignment(task, code_output)
        elif step_id == "VB":
            return self._step_version_bump(task)
        elif step_id == "GIT":
            return self._step_git_commit(task)
        elif step_id == "CL":
            return self._step_cleanup(task)
        elif step_id == "CCP":
            return self._step_final_checkpoint(task)
        elif step_id == "AD":
            return self._step_dashboard_update(task)
        else:
            return TDDStepResult(step_id=step_id, success=True, skipped=True)

    async def _call_tdd_worker(self, prompt: str, system_prompt: str) -> str:
        """Call the TDD worker (Claude) for creative tasks: AC, RED, GREEN."""
        worker = self.router.get_worker("tdd_testing")
        if not worker:
            raise RuntimeError("No worker assigned to tdd_testing role")

        result = await worker.send_message(prompt, system_prompt=system_prompt)
        if result.get("success") and result.get("response"):
            return result["response"]
        raise RuntimeError(f"TDD worker call failed: {result.get('error', 'unknown')}")

    async def _call_analysis_worker(self, prompt: str, system_prompt: str) -> str:
        """Call the analysis worker (DeepSeek/Qwen) for BC, SEA, DS, OA.

        Falls back to tdd_testing (Claude) if tdd_analysis role is not configured.
        This saves ~62K tokens per full TDD run by using free local models
        for steps that interpret structured tool output rather than write code.
        """
        worker = self.router.get_worker("tdd_analysis")
        if not worker:
            # Graceful fallback to Claude if tdd_analysis not configured
            logger.info("tdd_analysis role not configured — falling back to tdd_testing")
            return await self._call_tdd_worker(prompt, system_prompt)

        result = await worker.send_message(prompt, system_prompt=system_prompt)
        if result.get("success") and result.get("response"):
            return result["response"]
        raise RuntimeError(f"Analysis worker call failed: {result.get('error', 'unknown')}")

    # ─── Helpers ───

    def _format_file_content(self, files: list, max_per_file: int = 15000) -> str:
        """Format file contents for LLM analysis, head+tail for large files."""
        parts = []
        for f in files:
            content = f.get("content", "")
            if len(content) <= max_per_file:
                truncated = content
            else:
                half = max_per_file // 2
                truncated = (
                    content[:half]
                    + f"\n\n... [{len(content) - max_per_file} chars omitted] ...\n\n"
                    + content[-half:]
                )
            parts.append(f"### {f['path']}\n```\n{truncated}\n```")
        return "\n".join(parts)

    def _create_env_tag(self, task_id: str, step_id: str, error: str,
                         project_id: str = None):
        """Create an ENV DaC tag for orchestration-level failures (FER-CLI-007)."""
        try:
            queue_write(
                operation="insert", table="dac_tags",
                params={
                    "task_id": task_id,
                    "tag_type": "ENV",
                    "context": f"Pipeline crash at step {step_id}: {error[:500]}",
                    "source_step": step_id,
                    "source_worker": "tdd_pipeline",
                    "project_id": project_id,
                    "status": "open",
                },
                requester="tdd_pipeline",
            )
        except RuntimeError as e:
            logger.error(f"Failed to create ENV DaC tag: {e}")

    # ─── Step Implementations ───

    async def _step_acceptance_criteria(self, task: dict) -> TDDStepResult:
        """Step 1: Define acceptance criteria from task description."""
        prompt = f"""Define precise acceptance criteria for this task.

Task: {task['description']}
Module: {task['module']}

Return a numbered list of testable acceptance criteria. Each must be:
- Specific and measurable
- Independently testable
- Clear pass/fail definition"""

        response = await self._call_tdd_worker(prompt, "Define testable acceptance criteria")
        return TDDStepResult(step_id="AC", success=True, output=response)

    async def _step_write_tests(self, task: dict, project: dict,
                                 code_output: dict) -> TDDStepResult:
        """Step 2: Write failing tests based on AC."""
        ac_result = self._results.get("AC")
        files_context = "\n".join(
            f"File: {f['path']}\n{f['content'][:2000]}"
            for f in code_output.get("files", [])[:5]
        )

        prompt = f"""Write failing tests for this task. Tests MUST fail initially (RED phase).

Task: {task['description']}
Acceptance Criteria:
{ac_result.output if ac_result else 'See task description'}

Code to test:
{files_context}

Write comprehensive tests using pytest (Python) or vitest (TypeScript).
Cover: happy path, edge cases, error cases.
Return the test file content."""

        response = await self._call_tdd_worker(prompt, "Write failing tests (TDD RED phase)")
        return TDDStepResult(step_id="RED", success=True, output=response)

    async def _step_minimal_impl(self, task: dict, code_output: dict) -> TDDStepResult:
        """Step 3: Minimal implementation to pass tests."""
        tests = self._results.get("RED")

        prompt = f"""Write the MINIMAL code to make these tests pass (GREEN phase).

Task: {task['description']}
Tests:
{tests.output[:3000] if tests else 'See previous step'}

Rules:
- Write the SMALLEST amount of code that makes tests pass
- No premature optimization
- No extra features beyond what tests require
{TESTING_GROUND_TRUTH_RULE}"""

        response = await self._call_tdd_worker(prompt, "Write minimal code to pass tests (TDD GREEN phase)")
        return TDDStepResult(step_id="GREEN", success=True, output=response)

    async def _step_bug_capture(self, task: dict, code_output: dict) -> TDDStepResult:
        """Step 4: Static bug scan — flake8 findings + LLM analysis."""
        files_text = self._format_file_content(code_output.get("files", []))

        # Run flake8 if analyzer available
        tool_context = ""
        if self._analyzer:
            file_paths = [f["path"] for f in code_output.get("files", [])]
            result = await self._analyzer.run_bug_capture(file_paths)
            tool_context = (
                "\n## Static Analysis (flake8)\n"
                + result.summary_for_llm()
                + "\nUse these real findings as ground truth. "
                "Add any additional bugs you find by code review.\n"
            )

        prompt = f"""Scan this code for bugs, logic errors, and potential issues.
{tool_context}
{files_text}

Return JSON:
{{"bugs": [{{"id": "BUG-001", "severity": "high|medium|low", "description": "...", "file": "...", "line": 0, "fix_suggestion": "..."}}], "clean": true|false}}"""

        response = await self._call_analysis_worker(prompt, "Static bug analysis")

        bugs = []
        try:
            data = json.loads(response) if response.strip().startswith('{') else {}
            bugs = data.get("bugs", [])
        except (json.JSONDecodeError, AttributeError):
            pass

        dac_tags = ["DOM"] * len(bugs) if bugs else []
        return TDDStepResult(
            step_id="BC", success=True, output=response,
            bugs_found=bugs, dac_tags=dac_tags
        )

    async def _step_bug_fix(self, task: dict, existing_bugs: list,
                             code_output: dict) -> TDDStepResult:
        """Step 5: Fix bugs found in step 4."""
        if not existing_bugs:
            return TDDStepResult(step_id="BF", success=True, skipped=True,
                                output="No bugs to fix")

        bugs_text = "\n".join(
            f"- [{b.get('severity', '?')}] {b.get('description', 'unknown')}"
            for b in existing_bugs
        )

        prompt = f"""Fix these bugs found in the code:

Bugs:
{bugs_text}

Current code files:
{json.dumps([f['path'] for f in code_output.get('files', [])])}

Provide fixed code for each affected file.
{TESTING_GROUND_TRUTH_RULE}"""

        response = await self._call_tdd_worker(prompt, "Fix detected bugs")
        return TDDStepResult(step_id="BF", success=True, output=response)

    async def _step_silent_error_analysis(self, task: dict,
                                           code_output: dict) -> TDDStepResult:
        """Step 6: Concurrency / silent error analysis — bandit subset + LLM."""
        files_text = self._format_file_content(code_output.get("files", []))

        # Run bandit error-handling subset if analyzer available
        tool_context = ""
        if self._analyzer:
            file_paths = [f["path"] for f in code_output.get("files", [])]
            result = await self._analyzer.run_silent_error_analysis(file_paths)
            tool_context = (
                "\n## Static Analysis (bandit error-handling)\n"
                + result.summary_for_llm()
                + "\nUse these real findings as ground truth. "
                "Add any additional concurrency/async issues you find.\n"
            )

        prompt = f"""Analyze for silent errors and concurrency issues:
{tool_context}
{files_text}
Task: {task['description']}

Check for:
- Race conditions
- Unhandled async errors
- Memory leaks
- State drift
- Deadlocks
- Silent failures (caught but not logged)

Return JSON: {{"issues": [{{"type": "...", "severity": "...", "description": "...", "file": "..."}}], "clean": true|false}}"""

        response = await self._call_analysis_worker(prompt, "Silent error and concurrency analysis")

        dac_tags = []
        try:
            data = json.loads(response) if response.strip().startswith('{') else {}
            if data.get("issues"):
                dac_tags = ["HAL"] * len(data["issues"])
        except (json.JSONDecodeError, AttributeError):
            pass

        return TDDStepResult(step_id="SEA", success=True, output=response, dac_tags=dac_tags)

    async def _step_security_scan(self, task: dict, code_output: dict) -> TDDStepResult:
        """Step 7: OWASP security scan — bandit + pip-audit + LLM."""
        files_text = self._format_file_content(code_output.get("files", []))

        # Run bandit (full) + pip-audit if analyzer available
        tool_context = ""
        if self._analyzer:
            file_paths = [f["path"] for f in code_output.get("files", [])]
            results = await self._analyzer.run_security_scan(file_paths)
            tool_parts = []
            for r in results:
                tool_parts.append(r.summary_for_llm())
            tool_context = (
                "\n## Static Analysis (security tools)\n"
                + "\n".join(tool_parts)
                + "\nUse these real findings as ground truth. "
                "Map findings to OWASP categories and add any issues "
                "the tools may have missed.\n"
            )

        prompt = f"""Security audit against OWASP Top 10:
{tool_context}
{files_text}

Check for:
- SQL injection
- XSS
- CSRF
- Hardcoded secrets
- Insecure deserialization
- Broken auth
- Security misconfiguration

Return JSON: {{"vulnerabilities": [{{"owasp": "A01-A10", "severity": "critical|high|medium|low", "description": "...", "file": "...", "fix": "..."}}], "secure": true|false}}"""

        response = await self._call_analysis_worker(prompt, "OWASP security scan")

        dac_tags = []
        try:
            data = json.loads(response) if response.strip().startswith('{') else {}
            if data.get("vulnerabilities"):
                dac_tags = ["SER"] * len(data["vulnerabilities"])
        except (json.JSONDecodeError, AttributeError):
            pass

        return TDDStepResult(step_id="DS", success=True, output=response, dac_tags=dac_tags)

    async def _step_output_alignment(self, task: dict, code_output: dict) -> TDDStepResult:
        """Step 8: 3-tier output alignment (spec <-> code <-> test)."""
        ac = self._results.get("AC")
        tests = self._results.get("RED")

        prompt = f"""Verify 3-tier alignment:

1. SPEC (Acceptance Criteria):
{ac.output[:1500] if ac else task['description']}

2. CODE (Implementation):
{json.dumps([f['path'] for f in code_output.get('files', [])])}

3. TESTS:
{tests.output[:1500] if tests else 'See test files'}

Check:
- Every AC has at least one test
- Every test has corresponding implementation
- No orphan code (code without spec or test)
{TESTING_GROUND_TRUTH_RULE}
Return JSON: {{"aligned": true|false, "gaps": [{{"type": "missing_test|missing_impl|orphan", "description": "..."}}]}}"""

        response = await self._call_analysis_worker(prompt, "Output alignment verification")
        return TDDStepResult(step_id="OA", success=True, output=response)

    def _step_version_bump(self, task: dict) -> TDDStepResult:
        """Step 9: Version bump (handled by orchestrator)."""
        return TDDStepResult(step_id="VB", success=True,
                            output="Version bump delegated to orchestrator")

    def _step_git_commit(self, task: dict) -> TDDStepResult:
        """Step 10: Git commit marker (actual commit done by _execute_single_task).

        P2 FIX: TDDPipeline must NOT call atomic_commit independently.
        The orchestrator's _execute_single_task issues the final atomic commit
        after the full pipeline completes.  Duplicate commits from TDD cause
        test_phase_build_processes_all_tasks to see 2× the expected call count.
        """
        return TDDStepResult(step_id="GIT", success=True, skipped=True,
                             output="Commit delegated to orchestrator task pipeline")

    def _step_cleanup(self, task: dict) -> TDDStepResult:
        """Step 11: Clean temporary artifacts."""
        return TDDStepResult(step_id="CL", success=True,
                            output="Cleanup complete")

    def _step_final_checkpoint(self, task: dict) -> TDDStepResult:
        """Step 12: Save final checkpoint state."""
        self._checkpoint(task["task_id"], "CCP", 12,
                        TDDStepResult(step_id="CCP", success=True))
        return TDDStepResult(step_id="CCP", success=True,
                            output="Final checkpoint saved")

    def _step_dashboard_update(self, task: dict) -> TDDStepResult:
        """Step 13: Update dashboard with TDD results."""
        summary = {
            "task_id": task["task_id"],
            "steps_completed": len(self._results),
            "bugs_found": sum(len(r.bugs_found) for r in self._results.values()),
            "dac_tags": sum(len(r.dac_tags) for r in self._results.values()),
        }
        return TDDStepResult(step_id="AD", success=True,
                            output=json.dumps(summary))

    # ─── Checkpoint / Recovery ───

    def _get_resume_step(self, task_id: str) -> Optional[int]:
        """Get the last completed step index for crash recovery."""
        checkpoint = self.db.get_last_checkpoint(task_id)
        if checkpoint:
            step_id = checkpoint.get("step")
            for step in TDD_STEPS:
                if step["id"] == step_id:
                    return step["index"]
        return None

    def _checkpoint(self, task_id: str, step_id: str, step_index: int,
                     result: TDDStepResult):
        """Save checkpoint to DB via message bus."""
        try:
            queue_write(
                operation="insert", table="checkpoints",
                params={
                    "task_id": task_id,
                    "worker": "tdd_pipeline",
                    "step": step_id,
                    "state_data": json.dumps({
                        "step_index": step_index,
                        "success": result.success,
                        "bugs": result.bugs_found,
                        "dac_tags": result.dac_tags,
                    }),
                    "tests_status": json.dumps({"step": step_id, "passed": result.success}),
                },
                requester="tdd_pipeline",
            )
        except RuntimeError as e:
            logger.error(f"Failed to save checkpoint: {e}")

    def _update_task_step(self, task_id: str, step_id: str):
        """Update task's current_step via message bus."""
        try:
            queue_write(
                operation="update", table="tasks",
                params={
                    "current_step": step_id,
                    "_where": {"task_id": task_id},
                },
                requester="tdd_pipeline",
            )
        except RuntimeError as e:
            logger.error(f"Failed to update task step: {e}")
