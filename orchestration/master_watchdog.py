"""
Master Watchdog - Process ID 1
================================================
SOLE DATABASE WRITER. All other components submit
write requests via the message bus (asyncio.Queue).
Watchdog drains the queue every 5s in batch.
================================================
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestration.database import (
    WatchdogDB, ReadOnlyDB, get_write_queue, get_result_bus,
)
from orchestration.process_reaper import ProcessReaper
from orchestration.watchdog_state import WatchdogStatePersistence
from orchestration.role_router import RoleRouter
from workers.adapters import create_worker_adapter, WorkerAdapter

logger = logging.getLogger("factory.watchdog")


class MasterWatchdog:
    """
    Master Watchdog - PID 1.
    - Boots first, spawns all components
    - SOLE writer to SQLite (via WatchdogDB)
    - Drains message bus for write requests from other components
    - Monitors health, context, tasks
    """

    def __init__(self, config_path: str = None):
        self.config = self._load_config(config_path)
        self.working_dir = Path(self.config["factory"]["working_dir"]).expanduser()
        self.state_dir = self.working_dir / "autonomous_factory" / "factory_state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # WRITE-capable DB - only Watchdog holds this
        self.db = WatchdogDB(str(self.state_dir / "factory.db"))

        # Process Reaper - ghost/zombie prevention
        self.reaper = ProcessReaper(str(self.state_dir))

        # State Persistence - crash recovery
        self.state_persistence = WatchdogStatePersistence(str(self.state_dir))

        # FIX: backup had `self._config_path = path` but parameter is `config_path`
        self._config_path = config_path or str(Path(__file__).parent.parent / "config" / "factory_config.json")
        self._recovered_state = None   # set if resuming from crash
        self._monitor_cycle_count = 0

        # Role Router - initialized after workers boot
        self.role_router: Optional[RoleRouter] = None

        # Message bus for incoming write requests
        self.write_queue = get_write_queue()
        self.result_bus = get_result_bus()
        self.batch_interval = self.config["watchdog"].get("db_write_batch_interval_seconds", 5)

        # Worker adapters
        self.workers: Dict[str, WorkerAdapter] = {}
        self.worker_states: Dict[str, dict] = {}

        # Monitoring config
        self.monitoring = False
        self.monitor_interval = self.config["watchdog"]["monitoring_interval_seconds"]
        self.task_timeout = self.config["watchdog"]["task_timeout_minutes"]
        self.context_threshold = self.config["watchdog"]["context_respawn_threshold"]

        # Session
        self.session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.boot_time = None
        self._drain_task = None
        self._monitor_task = None

        logger.info(f"Watchdog created | session={self.session_id}")

    def _load_config(self, config_path=None) -> dict:
        if config_path is None:
            config_path = str(Path(__file__).parent.parent / "config" / "factory_config.json")
        with open(config_path) as f:
            return json.load(f)

    def get_readonly_db(self) -> ReadOnlyDB:
        """Return a read-only handle for other components. They CANNOT write."""
        return ReadOnlyDB(str(self.state_dir / "factory.db"))

    # ================================================
    # BOOT SEQUENCE (Rules 1-9)
    # ================================================

    async def boot(self) -> bool:
        self.boot_time = datetime.now()
        logger.info("=" * 60)
        logger.info("AUTONOMOUS FACTORY - BOOT SEQUENCE")
        logger.info(f"Session: {self.session_id}")
        logger.info("=" * 60)

        # 1. DB ready (Rule 1-2: PID 1 sole writer, 14 tables, WAL mode)
        logger.info("[1/9] Database initialized (Watchdog = sole writer)")

        # 2. Check for previous state - CRASH RECOVERY (Rule 3)
        logger.info("[2/9] Checking for crash recovery state...")
        self._recovered_state = self.state_persistence.load_state()
        if self._recovered_state:
            logger.info("  RECOVERY MODE - resuming from previous state")
            self._monitor_cycle_count = self._recovered_state.get("monitoring_cycle", 0)
        else:
            logger.info("  Fresh start (no previous state)")

        # 3. Startup sweep - kill ghosts from previous crash (Rule 4)
        logger.info("[3/9] Killing ghost processes from previous runs...")
        self.reaper.startup_sweep()

        # 4. Prerequisites (Rule 5)
        logger.info("[4/9] Checking prerequisites...")
        ok, issues = await self._check_prerequisites()
        if issues:
            for issue in issues:
                logger.warning(f"  WARN: {issue}")
            logger.info("  Some workers unavailable — booting with available workers")
        else:
            logger.info("  All prerequisites OK")

        # 5. Git
        logger.info("[5/9] Checking git...")
        await self._check_git()

        # 6. Workers (Rule 6: create adapters, health check each)
        logger.info("[6/9] Initializing workers...")
        await self._init_workers()

        # 7. Dashboard states (restore or fresh)
        logger.info("[7/9] Restoring dashboard states...")
        self._restore_or_init_dashboard_states()

        # 8. Role Router (Rule 8: maps 10 roles -> workers)
        logger.info("[8/9] Initializing role router...")
        self.role_router = RoleRouter(self.config, self.workers)
        assignments = self.role_router.get_all_assignments()
        for a in assignments:
            if a["active_worker"]:
                logger.info(f"  {a['role']}: {a['primary']}"
                            + (f" (fallback: {a['fallback']})" if a['fallback'] else ""))

        # 9. Start loops (Rule 9: drain 5s, monitor 30s, state persist 30s)
        logger.info("[9/9] Starting loops (drain + monitor + state persist)...")
        self.monitoring = True
        self._drain_task = asyncio.create_task(self._db_drain_loop())
        self._monitor_task = asyncio.create_task(self._monitoring_loop())

        # Log recovery summary
        if self._recovered_state:
            project = self._recovered_state.get("current_project_id")
            cycle = self._recovered_state.get("monitoring_cycle", 0)
            logger.info("=" * 60)
            logger.info(f"RECOVERY COMPLETE - resumed from cycle {cycle}")
            if project:
                logger.info(f"Active project: {project}")
            logger.info("Checking task states and reconnecting...")
            await self._post_recovery_reconciliation()
            logger.info("=" * 60)

        port = self.config["dashboard"]["port"]
        logger.info("=" * 60)
        logger.info(f"BOOT COMPLETE - Dashboard: http://127.0.0.1:{port}")
        logger.info("=" * 60)
        return True

    async def _check_prerequisites(self):
        """Rule 5: Ollama running? Git available? CLI tools in PATH?"""
        issues = []
        wc = self.config["workers"]

        # Ollama check
        locals_ = [n for n, c in wc.items() if c.get("type") == "local_ollama"]
        if locals_:
            if not await self._ollama_running():
                issues.append(
                    "Ollama not running. Start: ollama serve\n"
                    "  Pull models:\n"
                    "    ollama pull deepseek-coder-v2:16b\n"
                    "    ollama pull qwen2.5-coder:7b\n"
                    "    ollama pull phi3:mini"
                )

        # CLI checks
        for name, cfg in wc.items():
            if cfg.get("type") in ("cli_login", "dual_auth"):
                adapter = create_worker_adapter(name, cfg)
                if not await adapter.is_authenticated():
                    cmd = cfg.get("cli_command", name)
                    issues.append(f"{name}: '{cmd}' not authenticated. Run: {cmd} login")
                await adapter.close()

        return True, issues

    async def _ollama_running(self) -> bool:
        try:
            import aiohttp
            ollama_base = (
                self.config.get("workers", {})
                .get("phi3", {})
                .get("api_base", "http://localhost:11434")
                .rstrip("/")
            )
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{ollama_base}/api/tags",
                                 timeout=aiohttp.ClientTimeout(total=5)) as r:
                    return r.status == 200
        except Exception:
            logger.debug("Ollama health check failed", exc_info=True)
            return False

    async def _check_git(self):
        repo = self.config["factory"].get("git_repo")
        if not repo:
            logger.warning("No git repo configured - Watchdog will prompt before coding starts")

    async def _init_workers(self):
        """Rule 6: Create adapters, health check each, register with reaper."""
        for name, cfg in self.config["workers"].items():
            if name == "phi3":
                continue
            try:
                adapter = create_worker_adapter(name, cfg)
                # Inject reaper for CLI workers
                if hasattr(adapter, 'set_reaper'):
                    adapter.set_reaper(self.reaper)
                health = await adapter.check_health()
                self.workers[name] = adapter
                self.worker_states[name] = {
                    "status": health,
                    "started": datetime.now().isoformat(),
                    "context_tokens": 0,
                }
                # NOTE: Worker adapters are in-process (same PID as watchdog).
                # Do NOT register them in the reaper — it would kill our own
                # process when no heartbeat is received. Worker health is
                # already monitored by the watchdog monitoring loop.
                logger.info(f"  {name}: {health}")
            except Exception as e:
                logger.error(f"  {name}: FAILED - {e}")
                self.worker_states[name] = {"status": "offline", "error": str(e)}

    def _restore_or_init_dashboard_states(self):
        """Restore dashboard from DB (recovery) or init fresh."""
        existing = self.db.get_all_dashboard_states()
        existing_names = {d["instance_name"] for d in existing}

        # In-process components are always online — set them to idle
        for name in ["orchestrator", "phi3-orchestrator"]:
            self.db.update_dashboard_state(
                instance_name=name,
                status="idle",
                context_usage_percent=0.0,
                context_token_count=0,
                max_context_tokens=0,
                tasks_completed_today=0,
            )

        # Worker adapters: check actual health
        for name in self.workers:
            if name in existing_names and self._recovered_state:
                logger.info(f"  {name}: restored from DB")
            else:
                cfg = self.config["workers"].get(name, {})
                max_tok = cfg.get("max_context_tokens", 0)
                health = self.worker_states.get(name, {}).get("status", "offline")
                self.db.update_dashboard_state(
                    instance_name=name,
                    status="idle" if health == "healthy" else "crashed",
                    context_usage_percent=0.0,
                    context_token_count=0,
                    max_context_tokens=max_tok,
                    tasks_completed_today=0,
                )

    # ================================================
    # PROJECT FOLDER STRUCTURE CREATOR
    # ================================================

    def create_project_structure(self, project_path: str, project_type: str = "web") -> str:
        """
        Create project folder structure based on project type.
        Called after blueprint approval by the orchestrator.

        Args:
            project_path: Absolute path where the project root should be created.
            project_type: One of 'web', 'iot', 'plm', 'mobile'. Defaults to 'web'.

        Returns:
            The resolved project root path as a string.
        """
        base = Path(project_path)
        base.mkdir(parents=True, exist_ok=True)

        # Common directories for all project types
        common_dirs = [
            "contracts",
            "rules",
            "tests",
            "config",
            "docs",
        ]

        # Type-specific directories
        type_dirs = {
            "web": [
                "backend",
                "frontend/components",
                "frontend/logic",
                "database/migrations",
                "database/seeds",
            ],
            "iot": [
                "firmware",
                "backend",
                "frontend",
                "database/migrations",
                "database/seeds",
                "tests/hardware_sim",
            ],
            "plm": [
                "engineering",
                "backend",
                "frontend",
                "database/migrations",
                "database/seeds",
            ],
            "mobile": [
                "app/components",
                "app/screens",
                "app/logic",
                "backend",
                "database/migrations",
                "database/seeds",
            ],
        }

        dirs = common_dirs + type_dirs.get(project_type, type_dirs["web"])

        for d in dirs:
            (base / d).mkdir(parents=True, exist_ok=True)
            # Add .gitkeep to empty dirs so git tracks them
            gitkeep = base / d / ".gitkeep"
            if not any((base / d).iterdir()):
                gitkeep.touch()

        logger.info(f"Created project structure at {base} (type={project_type}, {len(dirs)} dirs)")
        return str(base)

    async def _post_recovery_reconciliation(self):
        """
        After crash recovery, reconcile DB state with reality:
        1. Find tasks that were 'in_progress' when Watchdog died
        2. Check if their workers are still alive
        3. Re-assign stuck tasks or mark them for retry
        4. Unblock tasks that were blocked on escalations
        """
        logger.info("  Reconciling task states after crash...")

        # Find in-progress tasks
        in_progress = self.db.get_stuck_tasks(timeout_minutes=0)  # all in_progress
        for t in in_progress:
            tid = t["task_id"]
            worker = t["assigned_to"]
            worker_health = self.worker_states.get(worker, {}).get("status", "offline")

            if worker_health in ("healthy",):
                # Worker alive - check last checkpoint
                cp = self.db.get_last_checkpoint(tid)
                if cp:
                    logger.info(f"    {tid}: worker {worker} alive, last step={cp.get('step')} - continuing")
                else:
                    logger.info(f"    {tid}: worker {worker} alive, no checkpoint - will re-verify")
            else:
                # Worker dead - reassign
                logger.warning(f"    {tid}: worker {worker} DEAD - reassigning")
                await self._reassign_task(tid, f"recovery: {worker} dead after watchdog crash")

        # Check blocked tasks (maybe escalation was resolved while we were down)
        blocked = []
        with self.db._read_conn() as conn:
            rows = conn.execute(
                "SELECT task_id FROM tasks WHERE status='blocked'"
            ).fetchall()
            blocked = [dict(r) for r in rows]

        for t in blocked:
            tid = t["task_id"]
            # Check if related escalation was resolved
            with self.db._read_conn() as conn:
                esc = conn.execute(
                    "SELECT status FROM escalations WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
                    (tid,)
                ).fetchone()
            if esc and dict(esc).get("status") == "resolved":
                logger.info(f"    {tid}: escalation resolved - unblocking")
                self.db.update_task(tid, status="in_progress")

        logger.info("  Reconciliation complete")

    # ================================================
    # DB DRAIN LOOP - processes write requests from all components
    # ================================================

    async def _db_drain_loop(self):
        """
        Every batch_interval seconds, drain the write queue.
        This is the ONLY path for non-Watchdog writes to reach the DB.
        """
        logger.info(f"DB drain loop started (interval: {self.batch_interval}s)")
        while self.monitoring:
            try:
                count = await self.db.drain_write_queue(
                    self.write_queue, self.result_bus, batch_size=100
                )
                if count > 0:
                    logger.debug(f"Drained {count} write(s)")
            except Exception as e:
                logger.error(f"Drain error: {e}")
            await asyncio.sleep(self.batch_interval)

    # ================================================
    # MONITORING LOOP - health, tasks, escalations
    # ================================================

    async def _monitoring_loop(self):
        """Rule 9: 30s health checks + reaper + stuck tasks + state persist."""
        logger.info(f"Monitor loop started (interval: {self.monitor_interval}s)")
        while self.monitoring:
            try:
                self._monitor_cycle_count += 1
                await self._monitor_cycle(self._monitor_cycle_count)

                # Persist Watchdog state every cycle (for crash recovery)
                active_project = self.db.get_active_project()
                self.state_persistence.save_state(
                    session_id=self.session_id,
                    boot_time=self.boot_time.isoformat() if self.boot_time else "",
                    worker_states=self.worker_states,
                    current_project_id=active_project["project_id"] if active_project else None,
                    monitoring_cycle=self._monitor_cycle_count,
                    config_path=self._config_path,
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor cycle error: {e}")
            await asyncio.sleep(self.monitor_interval)

    async def _monitor_cycle(self, cycle: int):
        # 0. Process Reaper - kill ghosts, orphans, zombies
        reaper_report = await self.reaper.check_all()
        if reaper_report["ghosts_killed"]:
            logger.warning(f"  Reaper killed ghosts: {reaper_report['ghosts_killed']}")
            for ghost in reaper_report["ghosts_killed"]:
                self.db.log_decision(
                    decision_type="ghost_killed",
                    decision_maker="reaper",
                    decision=f"Killed ghost: {ghost}",
                    reasoning="No heartbeat within max_silent_seconds",
                )
        if reaper_report["orphans_killed"]:
            logger.warning(f"  Reaper killed orphans: {reaper_report['orphans_killed']}")
        if reaper_report["zombies_reaped"]:
            logger.info(f"  Reaper reaped {len(reaper_report['zombies_reaped'])} zombie(s)")

        # 1. Health-check workers
        for name, adapter in self.workers.items():
            health = await adapter.check_health()
            old = self.worker_states.get(name, {}).get("status")
            if health != old:
                logger.info(f"  {name}: {old} -> {health}")
                self.db.log_decision(
                    decision_type="health_change",
                    decision_maker="watchdog",
                    decision=f"{name}: {old} -> {health}",
                    reasoning="periodic health check",
                )
            self.worker_states[name]["status"] = health
            dash_status = self._health_to_dash(health)
            dash_update = {"status": dash_status}
            # Clear stale current_task_id when worker returns to idle
            if dash_status == "idle":
                dash_update["current_task_id"] = None
            self.db.update_dashboard_state(
                instance_name=name,
                **dash_update,
            )
            self.db.update_worker_health(
                worker_id=name,
                worker_type=self.config["workers"].get(name, {}).get("type", "unknown"),
                status=health,
            )

            if health in ("crashed", "offline"):
                logger.error(f"  {name}: CRASHED - respawning...")
                await self._respawn(name)

        # 2. Stuck tasks — retry once, then escalate with DaC HAL tag
        stuck = self.db.get_stuck_tasks(self.task_timeout)
        for t in stuck:
            ws = self.worker_states.get(t["assigned_to"], {}).get("status", "offline")
            if ws in ("crashed", "offline"):
                await self._reassign_task(t["task_id"], "worker_crashed")
            # Remaining stuck tasks (worker alive but task stalled) handled below
        await self._handle_stuck_tasks()

        # 3. Integrity (every ~8h at 30s intervals = cycle 960)
        if cycle % 960 == 0:
            if not self.db.integrity_check():
                logger.critical("DB INTEGRITY CHECK FAILED")

    async def _handle_stuck_tasks(self):
        """Detect stuck tasks, retry once on same worker, then escalate."""
        timeout = self.config.get("watchdog", {}).get("task_timeout_minutes", 10)
        stuck = self.db.get_stuck_tasks(timeout_minutes=timeout)

        for task_info in stuck:
            task_id = task_info["task_id"]
            assigned_to = task_info.get("assigned_to", "unknown")

            # Get full task data
            task = self.db.get_task(task_id)
            if not task:
                continue

            retry_count = task.get("retry_count", 0) or 0
            max_retries = task.get("max_retries", 2) or 2

            if retry_count < 1:
                # First timeout: kill + retry on same worker
                logger.warning(
                    f"Task {task_id} stuck (>{timeout}min). "
                    f"Retry {retry_count + 1}/{max_retries} on {assigned_to}"
                )
                self.db.update_task(
                    task_id,
                    status="pending",
                    retry_count=retry_count + 1,
                    current_step=f"retry_after_timeout_{retry_count + 1}",
                )
                self.db.log_decision(
                    task_id=task_id,
                    decision_type="task_retry",
                    decision_maker="watchdog",
                    decision=f"Retry {retry_count + 1}/{max_retries} on {assigned_to}",
                    reasoning=f"Task stuck for >{timeout}min, first timeout — retrying",
                )
                # FER-CLI-002 FIX: Actively re-queue the task so a worker picks it up.
                # Without this, the task sits as 'pending' in DB but no worker knows about it.
                complexity = task.get("complexity", "high")
                requeued_to = await self.assign_task(task_id, complexity=complexity)
                if requeued_to:
                    logger.info(f"  Task {task_id} re-queued to {requeued_to}")
                else:
                    logger.warning(f"  Task {task_id} could not be re-queued (no healthy worker)")
            else:
                # Already retried: escalate
                logger.error(
                    f"Task {task_id} stuck after {retry_count} retries. Escalating."
                )
                self.db.update_task(task_id, status="blocked")
                self.db.create_escalation(
                    task_id=task_id,
                    escalation_type="task_timeout",
                    escalated_by="watchdog",
                    reason=(
                        f"Task stuck for >{timeout}min after {retry_count} retries. "
                        f"Last assigned to: {assigned_to}"
                    ),
                    context_data={
                        "retry_count": retry_count,
                        "timeout_minutes": timeout,
                        "last_worker": assigned_to,
                    },
                )
                # Create DaC HAL tag for audit trail
                self.db.create_dac_tag(
                    task_id=task_id,
                    tag_type="HAL",
                    context=f"Task timed out after {retry_count} retries ({timeout}min each)",
                    source_step="watchdog_monitor",
                    source_worker=assigned_to,
                )

    def _health_to_dash(self, h: str) -> str:
        return {"healthy": "idle", "degraded": "active",
                "crashed": "crashed", "offline": "crashed"}.get(h, "crashed")

    # ================================================
    # RESPAWN & REASSIGN
    # ================================================

    async def _respawn(self, name: str):
        """Rule 24-25: Respawn crashed workers."""
        self.db.update_dashboard_state(instance_name=name, status="respawning")
        cfg = self.config["workers"].get(name, {})
        try:
            adapter = create_worker_adapter(name, cfg)
            h = await adapter.check_health()
            if h == "healthy":
                self.workers[name] = adapter
                self.worker_states[name]["status"] = "healthy"
                self.db.update_dashboard_state(
                    instance_name=name, status="idle",
                    context_usage_percent=0.0, context_token_count=0,
                )
                logger.info(f"  {name}: Respawned")
            else:
                self.db.update_dashboard_state(instance_name=name, status="crashed")
                logger.error(f"  {name}: Respawn failed ({h})")
        except Exception as e:
            logger.error(f"  {name}: Respawn error: {e}")

    async def _reassign_task(self, task_id: str, reason: str):
        """Reassign a task from a dead/stuck worker to a fallback via RoleRouter."""
        task = self.db.get_task(task_id)
        if not task:
            return
        old = task["assigned_to"]

        # FER-CLI-003 FIX: Use RoleRouter to find an alternate worker instead of
        # hardcoded deepseek/qwen swap. This respects user's role config and works
        # even if workers are swapped or renamed.
        new = None
        if self.role_router:
            # Find which role the old worker was fulfilling
            for role_name, assignment in self.role_router._assignments.items():
                if assignment.primary == old:
                    # Try the fallback for this role
                    if assignment.fallback and assignment.fallback != old:
                        candidate = assignment.fallback
                        if self.worker_states.get(candidate, {}).get("status") == "healthy":
                            new = candidate
                            break
                elif assignment.fallback == old:
                    # Old worker was the fallback; try the primary
                    candidate = assignment.primary
                    if self.worker_states.get(candidate, {}).get("status") == "healthy":
                        new = candidate
                        break

            # If no role-based match, try any healthy worker (excluding the failed one)
            if not new:
                for name, state in self.worker_states.items():
                    if name != old and state.get("status") == "healthy":
                        new = name
                        break

        if not new:
            self.db.create_escalation(
                task_id=task_id, escalation_type="task_timeout",
                escalated_by="watchdog",
                reason=f"No alternate worker available (old={old}). Reason: {reason}",
            )
            self.db.update_task(task_id, status="blocked")
            return

        retry = (task.get("retry_count") or 0) + 1
        self.db.update_task(task_id, assigned_to=new, retry_count=retry, status="in_progress")
        self.db.update_dashboard_state(instance_name=new, status="working", current_task_id=task_id)
        self.db.log_decision(
            task_id=task_id, decision_type="task_reassignment",
            decision_maker="watchdog",
            decision=f"{old} -> {new}",
            reasoning=reason,
        )
        logger.info(f"  Task {task_id}: {old} -> {new} ({reason})")

    # ================================================
    # TASK ASSIGNMENT (Watchdog writes directly)
    # ================================================

    async def assign_task(self, task_id: str, complexity: str = None) -> Optional[str]:
        """Assign task using role router - no hardcoded worker names."""
        role = "code_generation_simple" if complexity == "low" else "code_generation_complex"
        target = self.role_router.get_worker_name(role) if self.role_router else None

        if not target:
            # Fallback: try the other complexity
            alt_role = "code_generation_complex" if role == "code_generation_simple" else "code_generation_simple"
            target = self.role_router.get_worker_name(alt_role) if self.role_router else None

        if not target:
            logger.error(f"No worker for role '{role}' - check role config")
            return None

        ts = self.worker_states.get(target, {}).get("status")
        if ts != "healthy":
            # Try fallback from role config
            assignment = self.role_router._assignments.get(role) if self.role_router else None
            fb = assignment.fallback if assignment else None
            if fb and self.worker_states.get(fb, {}).get("status") == "healthy":
                target = fb
            else:
                logger.error(f"No healthy worker for role '{role}'")
                return None

        self.db.update_task(
            task_id,
            assigned_to=target,
            assigned_at=datetime.now().isoformat(),
            status="in_progress",
        )
        self.db.update_dashboard_state(
            instance_name=target, status="working", current_task_id=task_id,
        )
        self.db.log_decision(
            task_id=task_id, decision_type="task_routing",
            decision_maker="watchdog",
            decision=f"-> {target} (role: {role})", reasoning=f"complexity={complexity}",
        )
        logger.info(f"  Task {task_id} -> {target} (role: {role})")
        return target

    # ================================================
    # PUBLIC API (for Dashboard / Orchestrator reads)
    # ================================================

    def get_system_status(self) -> dict:
        workers = {}
        for n, s in self.worker_states.items():
            workers[n] = {"status": s.get("status", "offline"), "started": s.get("started")}
        return {
            "session_id": self.session_id,
            "boot_time": self.boot_time.isoformat() if self.boot_time else None,
            "monitoring": self.monitoring,
            "workers": workers,
            "dashboard_states": self.db.get_all_dashboard_states(),
            "task_stats": self.db.get_task_stats(),
            "pending_escalations": self.db.get_pending_escalations(5),
            "recent_activity": self.db.get_recent_activity(10),
        }

    def recall_chats(self, chat_ids: list) -> list:
        """Retrieve specific full chat records by ID.
        Used by Dashboard and future Claude-orchestrator for on-demand recall."""
        if not chat_ids:
            return []
        read_db = self.get_readonly_db()
        return read_db.get_chats_by_ids(chat_ids)

    # ================================================
    # SHUTDOWN
    # ================================================

    async def shutdown(self):
        """Ordered teardown: Phi3 -> Workers -> Dashboard -> Orchestrator."""
        logger.info("Watchdog shutting down...")
        self.monitoring = False
        if self._drain_task:
            self._drain_task.cancel()
        if self._monitor_task:
            self._monitor_task.cancel()

        # Reaper: ordered shutdown of ALL tracked processes
        await self.reaper.shutdown_all(timeout=15)

        # Final drain
        await self.db.drain_write_queue(self.write_queue, self.result_bus, batch_size=500)
        for adapter in self.workers.values():
            try:
                await adapter.close()
            except Exception:
                logger.debug(f"Worker adapter close failed", exc_info=True)

        # Clean shutdown: clear state files (no recovery needed)
        self.state_persistence.clear()

        logger.info("Watchdog shutdown complete - clean exit, no ghost processes")
