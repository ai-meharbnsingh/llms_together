"""
Watchdog State Persistence - Crash Recovery Support
=====================================================
Saves Watchdog's own state every monitoring cycle so that
if Watchdog dies suddenly and is restarted via recover.sh,
it can resume from exactly where it left off.
=====================================================
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("factory.state")

STATE_FILE = "watchdog_state.json"
HEARTBEAT_FILE = "watchdog_heartbeat"


class WatchdogStatePersistence:
    """
    Persists Watchdog state to disk every cycle.
    On restart, Watchdog reads this to resume.
    """

    def __init__(self, state_dir: str):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / STATE_FILE
        self.heartbeat_file = self.state_dir / HEARTBEAT_FILE
        self._last_save = 0

    # ========================================
    # SAVE: Called every monitoring cycle
    # ========================================

    def save_state(self, session_id: str, boot_time: str,
                   worker_states: Dict[str, dict],
                   current_project_id: Optional[str],
                   monitoring_cycle: int,
                   config_path: str):
        """
        Persist full Watchdog state to disk.
        Called every monitoring cycle by Watchdog.
        """
        state = {
            "saved_at": datetime.now().isoformat(),
            "saved_timestamp": time.time(),
            "session_id": session_id,
            "boot_time": boot_time,
            "monitoring_cycle": monitoring_cycle,
            "config_path": config_path,
            "current_project_id": current_project_id,
            "worker_states": {},
            "recovery_instructions": {
                "resume_from": "last_known_state",
                "check_workers": True,
                "drain_pending_writes": True,
                "reconnect_alive_workers": True,
            },
        }

        # Save worker states (status, last task, etc.)
        for name, ws in worker_states.items():
            state["worker_states"][name] = {
                "status": ws.get("status", "unknown"),
                "started": ws.get("started"),
                "context_tokens": ws.get("context_tokens", 0),
                "last_known_health": ws.get("status", "unknown"),
            }

        try:
            # Atomic write: write to temp then rename
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2, default=str))
            tmp.rename(self.state_file)
            self._last_save = time.time()
        except Exception as e:
            logger.error(f"State save failed: {e}")

        # Update heartbeat file (just timestamp)
        try:
            self.heartbeat_file.write_text(str(time.time()))
        except Exception:
            pass

    # ========================================
    # LOAD: Called on restart after crash
    # ========================================

    def load_state(self) -> Optional[dict]:
        """
        Load last saved state. Returns None if no state or corrupted.
        """
        if not self.state_file.exists():
            logger.info("No previous state file found - fresh start")
            return None

        try:
            state = json.loads(self.state_file.read_text())
            age = time.time() - state.get("saved_timestamp", 0)

            logger.info(f"Found previous state (age: {age:.0f}s)")
            logger.info(f"  Session: {state.get('session_id')}")
            logger.info(f"  Saved at: {state.get('saved_at')}")
            logger.info(f"  Cycle: {state.get('monitoring_cycle')}")
            logger.info(f"  Project: {state.get('current_project_id', 'none')}")
            logger.info(f"  Workers: {list(state.get('worker_states', {}).keys())}")

            return state

        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"State file corrupted: {e}")
            # Rename corrupted file for forensics
            corrupted = self.state_file.with_suffix(".corrupted")
            self.state_file.rename(corrupted)
            return None

    # ========================================
    # HEARTBEAT: External check if Watchdog alive
    # ========================================

    def is_watchdog_alive(self, max_age_seconds: int = 90) -> bool:
        """
        Check if Watchdog is alive by reading heartbeat file.
        Used by recover.sh to decide whether to restart.
        """
        if not self.heartbeat_file.exists():
            return False
        try:
            ts = float(self.heartbeat_file.read_text().strip())
            age = time.time() - ts
            return age < max_age_seconds
        except Exception:
            return False

    def get_last_heartbeat_age(self) -> float:
        """Seconds since last heartbeat. Returns inf if no heartbeat."""
        if not self.heartbeat_file.exists():
            return float("inf")
        try:
            ts = float(self.heartbeat_file.read_text().strip())
            return time.time() - ts
        except Exception:
            return float("inf")

    # ========================================
    # CLEANUP: Remove state files
    # ========================================

    def clear(self):
        """Remove state files (clean shutdown)."""
        self.state_file.unlink(missing_ok=True)
        self.heartbeat_file.unlink(missing_ok=True)
