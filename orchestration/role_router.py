"""
Role Router - Decouples roles from workers.
=====================================================
Roles are WHAT needs to be done (tdd_testing, gatekeeper, etc.)
Workers are WHO does it (claude, kimi, deepseek, etc.)
Role->Worker mapping is configurable and hot-swappable from dashboard.
=====================================================
"""

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

from workers.adapters import WorkerAdapter

logger = logging.getLogger("factory.role_router")

# All supported roles
VALID_ROLES = {
    "code_generation_simple",
    "code_generation_complex",
    "tdd_testing",
    "gatekeeper_review",
    "architecture_audit",
    "task_planning_gsd",
    "blueprint_generation",
    "summarization",
    "frontend_design",
    "project_classification",
}


class RoleAssignment:
    """Single role -> worker mapping with fallback."""
    __slots__ = ("role", "primary", "fallback", "updated_at")

    def __init__(self, role: str, primary: str, fallback: str = None):
        self.role = role
        self.primary = primary
        self.fallback = fallback
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "primary": self.primary,
            "fallback": self.fallback,
            "updated_at": self.updated_at,
        }


class RoleRouter:
    """
    Routes roles to workers dynamically.
    Mappings loaded from config, hot-swappable via dashboard.

    Usage:
        router = RoleRouter(config, workers)
        worker = router.get_worker("tdd_testing")
        # worker is the adapter for whoever is assigned to TDD
    """

    def __init__(self, config: dict, workers: Dict[str, WorkerAdapter]):
        self.workers = workers
        self._assignments: Dict[str, RoleAssignment] = {}
        self._config_path = None
        self._load_from_config(config)

    def _load_from_config(self, config: dict):
        """Load role mappings from config. Warns if workers are unavailable."""
        roles_config = config.get("roles", {})
        for role, mapping in roles_config.items():
            if role not in VALID_ROLES:
                logger.warning(f"Unknown role in config: {role}")
                continue
            primary = mapping.get("primary", "")
            fallback = mapping.get("fallback")
            self._assignments[role] = RoleAssignment(role, primary, fallback)
            # Warn about unavailable workers
            if primary and primary not in self.workers:
                logger.warning(f"  Role '{role}': primary '{primary}' not available")
            if fallback and fallback not in self.workers:
                logger.warning(f"  Role '{role}': fallback '{fallback}' not available")
            logger.info(f"  Role '{role}': {primary}" +
                        (f" (fallback: {fallback})" if fallback else ""))

    # --- Core: Get worker for a role ---

    def get_worker(self, role: str) -> Optional[WorkerAdapter]:
        """
        Get the worker adapter assigned to a role.
        Tries primary, then fallback if primary unavailable.
        """
        assignment = self._assignments.get(role)
        if not assignment:
            logger.error(f"No worker assigned to role: {role}")
            return None

        # Try primary
        primary = self.workers.get(assignment.primary)
        if primary:
            return primary

        # Try fallback
        if assignment.fallback:
            fallback = self.workers.get(assignment.fallback)
            if fallback:
                logger.warning(
                    f"Role '{role}': primary '{assignment.primary}' unavailable, "
                    f"using fallback '{assignment.fallback}'"
                )
                return fallback

        logger.error(
            f"Role '{role}': neither primary '{assignment.primary}' "
            f"nor fallback '{assignment.fallback}' available"
        )
        return None

    def get_worker_name(self, role: str) -> Optional[str]:
        """Get the name of the worker assigned to a role."""
        assignment = self._assignments.get(role)
        if not assignment:
            return None
        if assignment.primary in self.workers:
            return assignment.primary
        if assignment.fallback and assignment.fallback in self.workers:
            return assignment.fallback
        return None

    # --- Hot-swap from dashboard ---

    def swap_role(self, role: str, new_primary: str,
                  new_fallback: str = None) -> dict:
        """
        Hot-swap a role to a different worker.
        Called from dashboard UI. No restart needed.
        """
        if role not in VALID_ROLES:
            return {"error": f"Invalid role: {role}", "valid_roles": list(VALID_ROLES)}

        if new_primary not in self.workers:
            return {"error": f"Unknown worker: {new_primary}",
                    "available": list(self.workers.keys())}

        if new_fallback and new_fallback not in self.workers:
            return {"error": f"Unknown fallback worker: {new_fallback}",
                    "available": list(self.workers.keys())}

        old = self._assignments.get(role)
        old_primary = old.primary if old else "none"

        self._assignments[role] = RoleAssignment(role, new_primary, new_fallback)

        logger.info(
            f"Role SWAP: '{role}' - {old_primary} -> {new_primary}"
            + (f" (fallback: {new_fallback})" if new_fallback else "")
        )

        return {
            "success": True,
            "role": role,
            "old_primary": old_primary,
            "new_primary": new_primary,
            "new_fallback": new_fallback,
        }

    # --- Bulk operations ---

    def get_all_assignments(self) -> list:
        """Get all current role->worker assignments (for dashboard)."""
        result = []
        for role in VALID_ROLES:
            assignment = self._assignments.get(role)
            if assignment:
                active_worker = self.get_worker_name(role)
                result.append({
                    "role": role,
                    "primary": assignment.primary,
                    "fallback": assignment.fallback,
                    "active_worker": active_worker,
                    "updated_at": assignment.updated_at,
                })
            else:
                result.append({
                    "role": role,
                    "primary": None,
                    "fallback": None,
                    "active_worker": None,
                    "updated_at": None,
                })
        return sorted(result, key=lambda x: x["role"])

    def get_available_workers(self) -> list:
        """List available workers (for dashboard dropdown)."""
        return list(self.workers.keys())

    def export_config(self) -> dict:
        """Export current assignments as config dict (for saving to file)."""
        roles = {}
        for role, assignment in self._assignments.items():
            entry = {"primary": assignment.primary}
            if assignment.fallback:
                entry["fallback"] = assignment.fallback
            roles[role] = entry
        return roles

    def save_to_config_file(self, config_path: str):
        """Persist current role assignments to config file."""
        try:
            with open(config_path) as f:
                config = json.load(f)
            config["roles"] = self.export_config()
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
            logger.info(f"Role config saved to {config_path}")
        except Exception as e:
            logger.error(f"Failed to save role config: {e}")
