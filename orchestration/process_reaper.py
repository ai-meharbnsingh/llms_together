"""
Process Reaper - Ghost Process Prevention & Cleanup
=====================================================
Ensures NO ghost/zombie/orphan processes survive.
Integrated into Watchdog's monitoring loop.

STRATEGY:
1. PID Registry - every spawned process registered with parent
2. Process Groups - all children in same pgid for group kill
3. Heartbeat Validation - no heartbeat = kill
4. Parent-Child Binding - child dies when parent dies
5. Shutdown Cascade - ordered teardown, force-kill stragglers
6. Zombie Reaper - periodic waitpid() for zombie collection
7. Startup Sweep - kill leftover processes from previous crash
=====================================================
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger("factory.reaper")

# PID file location for crash recovery
PID_REGISTRY_FILE = "factory_state/process_registry.json"


@dataclass
class TrackedProcess:
    """A process tracked by the reaper."""
    pid: int
    name: str                       # e.g. 'deepseek', 'phi3-claude', 'dashboard'
    parent_name: Optional[str]      # e.g. 'claude' for phi3-claude, None for top-level
    pgid: int                       # process group ID for group kill
    started_at: float               # time.time()
    last_heartbeat: float           # time.time()
    process_type: str               # 'worker', 'phi3', 'dashboard', 'subprocess'
    subprocess_ref: Optional[asyncio.subprocess.Process] = None
    max_silent_seconds: int = 120   # kill if no heartbeat for this long
    is_critical: bool = False       # critical = escalate before kill

    @property
    def age_seconds(self) -> float:
        return time.time() - self.started_at

    @property
    def silent_seconds(self) -> float:
        return time.time() - self.last_heartbeat

    @property
    def is_alive(self) -> bool:
        try:
            os.kill(self.pid, 0)  # signal 0 = check existence
            return True
        except (ProcessLookupError, PermissionError):
            return False


class ProcessReaper:
    """
    Tracks, validates, and reaps all factory processes.
    Integrated into Watchdog's monitoring cycle.
    """

    def __init__(self, state_dir: str):
        self.state_dir = Path(state_dir)
        self.registry: Dict[int, TrackedProcess] = {}    # pid -> TrackedProcess
        self.name_to_pid: Dict[str, int] = {}            # name -> pid
        self.parent_children: Dict[str, Set[str]] = {}   # parent_name -> set of child names
        self._factory_pgid: Optional[int] = None
        self._pid_file = self.state_dir / "process_registry.json"

    # ========================================
    # STARTUP: Kill ghosts from previous crashes
    # ========================================

    def startup_sweep(self):
        """
        Called FIRST at boot. Kills any leftover processes
        from a previous factory crash.
        """
        logger.info("Startup sweep: checking for ghost processes...")

        # 1. Read old PID registry if exists
        killed = 0
        if self._pid_file.exists():
            try:
                old_pids = json.loads(self._pid_file.read_text())
                for entry in old_pids:
                    pid = entry.get("pid")
                    name = entry.get("name", "unknown")
                    if pid and pid != os.getpid():
                        if self._is_pid_alive(pid):
                            logger.warning(f"  Killing ghost: {name} (PID {pid})")
                            self._force_kill(pid)
                            killed += 1
            except Exception as e:
                logger.error(f"  PID file read error: {e}")
            finally:
                self._pid_file.unlink(missing_ok=True)

        # 2. Kill any lingering ollama-spawned processes with our marker
        killed += self._kill_orphaned_ollama_requests()

        if killed > 0:
            logger.info(f"  Killed {killed} ghost process(es)")
        else:
            logger.info("  No ghosts found")

        # 3. Set our process group
        self._factory_pgid = os.getpgid(os.getpid())

    def _kill_orphaned_ollama_requests(self) -> int:
        """Kill any orphaned curl/ollama API requests from previous runs."""
        killed = 0
        try:
            result = subprocess.run(
                ["pgrep", "-f", "autonomous_factory"],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                my_pid = os.getpid()
                for line in result.stdout.strip().split("\n"):
                    pid = int(line.strip())
                    if pid != my_pid and pid != os.getppid():
                        try:
                            os.kill(pid, signal.SIGTERM)
                            killed += 1
                        except (ProcessLookupError, PermissionError):
                            pass
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            pass
        return killed

    # ========================================
    # REGISTRATION: Track every spawned process
    # ========================================

    def register(self, pid: int, name: str, process_type: str,
                 parent_name: str = None, max_silent: int = 120,
                 is_critical: bool = False,
                 subprocess_ref=None) -> TrackedProcess:
        """Register a new process for tracking."""
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError):
            pgid = pid

        proc = TrackedProcess(
            pid=pid,
            name=name,
            parent_name=parent_name,
            pgid=pgid,
            started_at=time.time(),
            last_heartbeat=time.time(),
            process_type=process_type,
            subprocess_ref=subprocess_ref,
            max_silent_seconds=max_silent,
            is_critical=is_critical,
        )

        self.registry[pid] = proc
        self.name_to_pid[name] = pid

        # Track parent-child
        if parent_name:
            if parent_name not in self.parent_children:
                self.parent_children[parent_name] = set()
            self.parent_children[parent_name].add(name)

        # Persist PID registry to disk (for crash recovery)
        self._persist_registry()

        logger.debug(f"Registered: {name} (PID {pid}, parent={parent_name})")
        return proc

    def unregister(self, name: str):
        """Remove process from tracking."""
        pid = self.name_to_pid.pop(name, None)
        if pid:
            self.registry.pop(pid, None)

        # Remove from parent-child mappings
        for parent, children in self.parent_children.items():
            children.discard(name)

        self._persist_registry()

    def heartbeat(self, name: str):
        """Update heartbeat timestamp for a tracked process."""
        pid = self.name_to_pid.get(name)
        if pid and pid in self.registry:
            self.registry[pid].last_heartbeat = time.time()

    # ========================================
    # MONITORING: Called by Watchdog every 30s
    # ========================================

    async def check_all(self) -> dict:
        """
        Full process health check. Returns report.
        Called by Watchdog in every monitoring cycle.
        """
        report = {
            "alive": [],
            "ghosts_killed": [],
            "zombies_reaped": [],
            "orphans_killed": [],
            "missing": [],
        }

        pids_to_remove = []

        for pid, proc in list(self.registry.items()):
            # 1. Is it still alive?
            if not proc.is_alive:
                logger.warning(f"  {proc.name} (PID {pid}): DEAD - removing from registry")
                report["missing"].append(proc.name)
                pids_to_remove.append(pid)

                # Kill its children too
                await self._kill_children(proc.name, report)
                continue

            # 2. Is it a ghost? (alive but no heartbeat)
            if proc.silent_seconds > proc.max_silent_seconds:
                logger.warning(
                    f"  {proc.name} (PID {pid}): GHOST - silent for "
                    f"{proc.silent_seconds:.0f}s > {proc.max_silent_seconds}s"
                )
                if proc.is_critical:
                    logger.error(f"  {proc.name}: CRITICAL - escalating before kill")
                    report["ghosts_killed"].append(f"{proc.name} (CRITICAL, escalated)")
                else:
                    await asyncio.to_thread(self._force_kill, pid)
                    report["ghosts_killed"].append(proc.name)
                    pids_to_remove.append(pid)
                    await self._kill_children(proc.name, report)
                continue

            # 3. Check parent alive (for child processes)
            if proc.parent_name:
                parent_pid = self.name_to_pid.get(proc.parent_name)
                if parent_pid is None or not self._is_pid_alive(parent_pid):
                    logger.warning(
                        f"  {proc.name} (PID {pid}): ORPHAN - "
                        f"parent '{proc.parent_name}' is dead"
                    )
                    await asyncio.to_thread(self._force_kill, pid)
                    report["orphans_killed"].append(proc.name)
                    pids_to_remove.append(pid)
                    continue

            report["alive"].append(proc.name)

        # Cleanup dead entries
        for pid in pids_to_remove:
            proc = self.registry.pop(pid, None)
            if proc:
                self.name_to_pid.pop(proc.name, None)

        # 4. Reap zombies (collect exit status of dead children)
        reaped = self._reap_zombies()
        report["zombies_reaped"] = reaped

        # 5. Persist updated registry
        self._persist_registry()

        return report

    async def _kill_children(self, parent_name: str, report: dict):
        """Kill all children of a dead parent."""
        children = self.parent_children.pop(parent_name, set())
        for child_name in children:
            child_pid = self.name_to_pid.get(child_name)
            if child_pid and self._is_pid_alive(child_pid):
                logger.warning(f"  Killing orphan child: {child_name} (PID {child_pid})")
                await asyncio.to_thread(self._force_kill, child_pid)
                report["orphans_killed"].append(child_name)

    # ========================================
    # SHUTDOWN: Ordered teardown, no survivors
    # ========================================

    async def shutdown_all(self, timeout: float = 15.0):
        """
        Ordered shutdown of all tracked processes.
        Order: Phi3 -> Workers -> Dashboard -> Orchestrator
        Final: force-kill anything still alive.
        """
        logger.info("Process Reaper: Ordered shutdown...")

        # Group by type for ordered teardown
        groups = {
            "phi3": [],
            "worker": [],
            "dashboard": [],
            "subprocess": [],
            "other": [],
        }
        for pid, proc in self.registry.items():
            group = proc.process_type if proc.process_type in groups else "other"
            groups[group].append(proc)

        # Shutdown order: phi3 -> subprocess -> worker -> dashboard -> other
        for group_name in ["phi3", "subprocess", "worker", "dashboard", "other"]:
            procs = groups[group_name]
            if not procs:
                continue

            logger.info(f"  Stopping {group_name}: {[p.name for p in procs]}")

            # SIGTERM first (graceful)
            for proc in procs:
                if proc.is_alive:
                    try:
                        os.kill(proc.pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        pass

            # Wait for graceful shutdown
            deadline = time.time() + timeout / len(groups)
            for proc in procs:
                while proc.is_alive and time.time() < deadline:
                    await asyncio.sleep(0.2)

            # SIGKILL survivors
            for proc in procs:
                if proc.is_alive:
                    logger.warning(f"  Force-killing: {proc.name} (PID {proc.pid})")
                    await asyncio.to_thread(self._force_kill, proc.pid)

        # Final zombie reap
        self._reap_zombies()

        # Clear registry
        self.registry.clear()
        self.name_to_pid.clear()
        self.parent_children.clear()

        # Delete PID file
        self._pid_file.unlink(missing_ok=True)

        logger.info("  All processes terminated")

    # ========================================
    # CLI SUBPROCESS TRACKING
    # ========================================

    def track_subprocess(self, proc: asyncio.subprocess.Process,
                         name: str, parent_name: str = None,
                         max_silent: int = 300) -> TrackedProcess:
        """
        Track an asyncio subprocess (from CLI adapter).
        Returns TrackedProcess for heartbeat updates.
        """
        return self.register(
            pid=proc.pid,
            name=name,
            process_type="subprocess",
            parent_name=parent_name,
            max_silent=max_silent,
            subprocess_ref=proc,
        )

    async def kill_subprocess(self, name: str, timeout: float = 10):
        """Kill a tracked subprocess gracefully then forcefully."""
        pid = self.name_to_pid.get(name)
        if not pid:
            return

        proc = self.registry.get(pid)
        if not proc:
            return

        # Try graceful
        if proc.subprocess_ref:
            try:
                proc.subprocess_ref.terminate()
                try:
                    await asyncio.wait_for(proc.subprocess_ref.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    proc.subprocess_ref.kill()
                    await proc.subprocess_ref.wait()
            except Exception:
                logger.debug(f"Process {pid} cleanup failed", exc_info=True)

        # Force if still alive
        if self._is_pid_alive(pid):
            await asyncio.to_thread(self._force_kill, pid)

        self.unregister(name)

    # ========================================
    # INTERNALS
    # ========================================

    def _is_pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def _force_kill(self, pid: int):
        """SIGKILL with PGID fallback for stubborn processes.

        Must be called via asyncio.to_thread() from async contexts so it runs
        in a thread pool and does NOT block the event loop.
        Uses threading.Event.wait() for cooperative waits between signal checks.
        """
        _wait = threading.Event().wait
        try:
            # First try SIGTERM
            os.kill(pid, signal.SIGTERM)
            _wait(0.5)

            # Check if dead
            if self._is_pid_alive(pid):
                # SIGKILL
                os.kill(pid, signal.SIGKILL)
                _wait(0.2)

            # If STILL alive, try process group kill
            if self._is_pid_alive(pid):
                try:
                    pgid = os.getpgid(pid)
                    if pgid != self._factory_pgid:
                        os.killpg(pgid, signal.SIGKILL)
                except Exception:
                    logger.debug(f"Process group kill for {pid} failed", exc_info=True)

        except (ProcessLookupError, PermissionError):
            pass  # Already dead

    def _reap_zombies(self) -> list:
        """Collect exit status of zombie child processes."""
        reaped = []
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    break
                reaped.append(pid)
                logger.debug(f"  Reaped zombie PID {pid} (exit status {status})")
            except ChildProcessError:
                break  # No children
        return reaped

    def _persist_registry(self):
        """Save PID registry to disk for crash recovery."""
        entries = []
        for pid, proc in self.registry.items():
            entries.append({
                "pid": pid,
                "name": proc.name,
                "parent": proc.parent_name,
                "type": proc.process_type,
                "started": proc.started_at,
            })
        try:
            self._pid_file.parent.mkdir(parents=True, exist_ok=True)
            self._pid_file.write_text(json.dumps(entries, indent=2))
        except Exception as e:
            logger.error(f"PID file persist error: {e}")

    # ========================================
    # STATUS (for Dashboard)
    # ========================================

    def get_status(self) -> dict:
        """Return current process status for dashboard display."""
        procs = []
        for pid, proc in self.registry.items():
            procs.append({
                "pid": pid,
                "name": proc.name,
                "parent": proc.parent_name,
                "type": proc.process_type,
                "alive": proc.is_alive,
                "age_s": round(proc.age_seconds),
                "silent_s": round(proc.silent_seconds),
                "max_silent_s": proc.max_silent_seconds,
                "critical": proc.is_critical,
            })
        return {
            "total_tracked": len(self.registry),
            "processes": procs,
            "parent_child_map": {
                k: list(v) for k, v in self.parent_children.items()
            },
        }
