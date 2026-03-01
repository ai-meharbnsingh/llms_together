#!/usr/bin/env python3
"""
Autonomous Factory v1.1 - Main Entry Point
Boots Watchdog (PID 1) -> Dashboard -> Orchestrator -> Phi3
Role assignments are hot-swappable from Dashboard UI.
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

FACTORY_ROOT = Path(__file__).parent
sys.path.insert(0, str(FACTORY_ROOT))

from orchestration.master_watchdog import MasterWatchdog
from orchestration.master_orchestrator import MasterOrchestrator
from orchestration.phi3_manager import Phi3Manager
from dashboard.dashboard_server import DashboardServer


def setup_logging(level="INFO"):
    log_dir = FACTORY_ROOT / "factory_state" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(log_dir / "factory.log"), mode="a"),
        ],
    )


async def main(config_path: str = None):
    cp = config_path or str(FACTORY_ROOT / "config" / "factory_config.json")
    with open(cp) as f:
        cfg = json.load(f)

    # Store config path for role saving
    cfg["_config_path"] = cp

    setup_logging(cfg.get("factory", {}).get("log_level", "INFO"))
    logger = logging.getLogger("factory.main")

    logger.info("=" * 60)
    logger.info("AUTONOMOUS FACTORY v1.1.0")
    logger.info("DB Writer: WATCHDOG ONLY | Roles: HOT-SWAPPABLE")
    logger.info("=" * 60)

    # --- 1. Boot Watchdog (PID 1, sole writer) ---
    watchdog = MasterWatchdog(config_path=cp)
    if not await watchdog.boot():
        logger.error("Boot failed. Fix prerequisites and retry.")
        return

    # --- 2. Spawn Phi3 Manager (Rule 11: orchestrator scribe first) ---
    read_db = watchdog.get_readonly_db()
    phi3_manager = Phi3Manager(cfg, read_db)
    await phi3_manager.start_all(["orchestrator"])
    logger.info("Phi3 scribe started: phi3-orchestrator")

    # NOTE: phi3, orchestrator, dashboard are in-process (same PID as watchdog).
    # Do NOT register them in the reaper — it would kill our own process
    # when no heartbeat is received. Reaper is for external subprocesses only.

    # --- 3. Spawn Master Orchestrator (COO, persistent in-memory) ---
    working_dir = cfg.get("factory", {}).get("working_dir", "~/working")
    orchestrator = MasterOrchestrator(
        read_db=watchdog.get_readonly_db(),
        role_router=watchdog.role_router,
        config=cfg,
        working_dir=working_dir,
    )
    orchestrator.phi3 = phi3_manager.get("orchestrator")

    logger.info("Master Orchestrator spawned (persistent, in-memory)")

    # --- 4. Spawn Dashboard ---
    dashboard = DashboardServer(
        read_db=watchdog.get_readonly_db(),
        config=cfg,
        role_router=watchdog.role_router,
    )
    dashboard.set_orchestrator(orchestrator)
    dashboard.set_watchdog(watchdog)
    await dashboard.start()
    logger.info("Dashboard online with chat panel")

    # --- Ready ---
    logger.info("")
    logger.info("Factory ready.")
    logger.info(f"Dashboard: http://127.0.0.1:{cfg['dashboard']['port']}")
    logger.info("Roles are hot-swappable from the dashboard UI.")
    logger.info("Orchestrator: ONLINE | Phi3-orchestrator: ONLINE")
    logger.info("")

    # Shutdown handler
    loop = asyncio.get_event_loop()
    stop = asyncio.Event()

    def _sig():
        logger.info("Shutdown signal received")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _sig)

    await stop.wait()

    # --- Ordered Shutdown: Phi3 -> Dashboard -> Orchestrator -> Watchdog ---
    logger.info("Shutting down...")

    logger.info("  Stopping Phi3 scribes...")
    await phi3_manager.stop_all()

    logger.info("  Stopping Dashboard...")
    await dashboard.stop()

    logger.info("  Stopping Orchestrator...")
    # Orchestrator is in-memory, no async cleanup needed

    logger.info("  Stopping Watchdog...")
    await watchdog.shutdown()
    logger.info("Goodbye.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Autonomous Factory v1.1")
    parser.add_argument("--config", type=str, default=None)
    args = parser.parse_args()
    asyncio.run(main(args.config))
