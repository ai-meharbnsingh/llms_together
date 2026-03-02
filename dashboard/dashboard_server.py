"""
Dashboard Server v1.5 — Web UI with Role Switching + Multi-Mode Chat Panel + Project Execution.
READ-ONLY DB. Writes + role swaps via bus/router -> Watchdog.

Chat modes:
  - Orchestrator: routes to MasterOrchestrator.handle_message() (intent routing)
  - Direct: user picks any configured worker model, sends raw message
  - Project: user picks worker, message gets project context prepended

M4 (v1.5): Project execution dashboard — launch, progress timeline, TDD step bar,
blueprint approval modal, UAT panel, live WebSocket events.
"""

import asyncio
import json
import logging
from datetime import datetime

from aiohttp import web
import aiohttp as aio

from orchestration.database import ReadOnlyDB
from orchestration.role_router import RoleRouter

logger = logging.getLogger("factory.dashboard")


# ─── Export Helpers (module-level for testability) ───


def _merge_and_dedup(cold: list, warm: list) -> list:
    """Merge cold (DB) and warm (in-memory) messages, dedup by timestamp, sort chronologically."""
    seen = {}
    for msg in cold:
        ts = msg.get("timestamp", "")
        seen[ts] = msg
    for msg in warm:
        ts = msg.get("timestamp", "")
        if ts not in seen:
            seen[ts] = msg
    return sorted(seen.values(), key=lambda m: m.get("timestamp", ""))


def _format_chat_markdown(messages: list, session_id: str, session_name: str) -> str:
    """Format messages as Markdown with chronological + grouped-by-worker sections."""
    lines = [f"# Chat Export: {session_name}", f"**Session:** `{session_id}`",
             f"**Messages:** {len(messages)}", ""]

    # Chronological view
    lines.append("## Chronological View")
    lines.append("")
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        ts = msg.get("timestamp", "")
        meta = msg.get("metadata") or {}
        worker = meta.get("worker", "")
        label = f"**{role}**" if not worker else f"**{role}** ({worker})"
        lines.append(f"### {ts}")
        lines.append(f"{label}")
        lines.append("")
        lines.append(content)
        lines.append("")

    # Grouped by worker
    lines.append("## Grouped by Worker")
    lines.append("")
    groups = {}
    for msg in messages:
        meta = msg.get("metadata") or {}
        worker = meta.get("worker") or msg.get("role", "unknown")
        groups.setdefault(worker, []).append(msg)
    for worker, msgs in sorted(groups.items()):
        lines.append(f"### {worker} ({len(msgs)} messages)")
        lines.append("")
        for msg in msgs:
            ts = msg.get("timestamp", "")
            role = msg.get("role", "")
            content = msg.get("content", "")
            lines.append(f"- **[{ts}] {role}:** {content}")
        lines.append("")

    return "\n".join(lines)


class DashboardServer:
    def __init__(self, read_db: ReadOnlyDB, config: dict,
                 role_router: RoleRouter = None):
        self.db = read_db
        self.db.set_requester("dashboard")
        self.cfg = config.get("dashboard", {})
        self.host = self.cfg.get("host", "127.0.0.1")
        self.port = self.cfg.get("port", 8420)
        self.refresh_ms = self.cfg.get("refresh_interval_ms", 2000)
        self.role_router = role_router
        self.config = config
        self.ws_clients = set()
        self.orchestrator = None
        self.watchdog = None
        self._runner = None
        self._current_chat_task = None  # asyncio.Task for in-progress chat (cancellable)
        self._running_projects = {}  # project_id -> asyncio.Task for execute_project()

    def set_role_router(self, router: RoleRouter):
        self.role_router = router

    def set_orchestrator(self, orch):
        """Inject MasterOrchestrator for chat routing."""
        self.orchestrator = orch

    def set_watchdog(self, wdg):
        """Inject MasterWatchdog for DB flush before export."""
        self.watchdog = wdg

    async def start(self):
        app = web.Application()
        r = app.router
        r.add_get("/", self._index)
        r.add_get("/ws", self._websocket)
        r.add_get("/api/status", self._api_status)
        r.add_get("/api/tasks", self._api_tasks)
        r.add_get("/api/escalations", self._api_escalations)
        r.add_post("/api/escalation/{id}/resolve", self._api_resolve)
        r.add_get("/api/activity", self._api_activity)
        # Role endpoints
        r.add_get("/api/roles", self._api_get_roles)
        r.add_post("/api/roles/swap", self._api_swap_role)
        r.add_get("/api/workers/available", self._api_available_workers)
        # Chat endpoints
        r.add_post("/api/chat", self._api_chat)
        r.add_get("/api/chat/history", self._api_chat_history)
        r.add_post("/api/chat/direct", self._api_chat_direct)
        r.add_post("/api/chat/project", self._api_chat_project)
        r.add_post("/api/chat/discussion", self._api_chat_discussion)
        r.add_post("/api/chat/stop", self._api_chat_stop)
        r.add_get("/api/workers/status", self._api_workers_status)
        # Project endpoints
        r.add_get("/api/projects", self._api_projects)
        r.add_post("/api/projects/select", self._api_project_select)
        r.add_post("/api/projects/create", self._api_project_create)
        # M4: Project execution endpoints
        r.add_post("/api/projects/launch", self._api_project_launch)
        r.add_get("/api/projects/{id}/progress", self._api_project_progress)
        r.add_get("/api/projects/{id}/blueprint", self._api_project_blueprint)
        r.add_post("/api/projects/{id}/approve-blueprint", self._api_approve_blueprint)
        r.add_post("/api/projects/{id}/approve-uat", self._api_approve_uat)
        r.add_delete("/api/projects/{id}", self._api_project_delete)
        # Issue 7: Training data validation endpoint (G3)
        r.add_post("/api/training-data/{id}/validate", self._api_validate_training_data)
        # Config endpoints
        r.add_post("/api/config/mode", self._api_config_mode)
        r.add_get("/api/config/mode", self._api_config_mode_get)
        # Chat session endpoints
        r.add_get("/api/chat/sessions", self._api_chat_sessions)
        r.add_post("/api/chat/sessions/new", self._api_chat_session_new)
        r.add_post("/api/chat/sessions/switch", self._api_chat_session_switch)
        r.add_post("/api/chat/sessions/rename", self._api_chat_session_rename)
        r.add_post("/api/chat/sessions/close", self._api_chat_session_close)
        # Search endpoints (cold memory)
        r.add_get("/api/chat/search", self._api_chat_search)
        r.add_get("/api/chat/archive", self._api_chat_archive)
        # Export endpoint
        r.add_get("/api/chat/download", self._api_chat_download)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        try:
            await site.start()
        except OSError as e:
            raise RuntimeError(
                f"Dashboard port {self.port} is already in use. "
                "Stop the existing process or change 'dashboard.port' in factory_config.json."
            ) from e
        logger.info(f"Dashboard: http://{self.host}:{self.port}")
        self._broadcast_task = asyncio.create_task(self._broadcast())

    async def stop(self):
        """Graceful shutdown -- close WebSockets and runner."""
        for ws in list(self.ws_clients):
            try:
                await ws.close()
            except Exception:
                logger.debug("WebSocket close failed during shutdown", exc_info=True)
        self.ws_clients.clear()
        if hasattr(self, "_broadcast_task") and self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass
        
        if self._runner:
            await self._runner.cleanup()
            logger.info("Dashboard stopped")

    async def _broadcast(self):
        while True:
            try:
                if self.ws_clients:
                    data = json.dumps(self._full_status(), default=str)
                    dead = set()
                    for ws in self.ws_clients:
                        try:
                            await ws.send_str(data)
                        except Exception:
                            logger.debug("WebSocket send failed, marking dead", exc_info=True)
                            dead.add(ws)
                    self.ws_clients -= dead
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Broadcast error (recovering): {e}")
            await asyncio.sleep(self.refresh_ms / 1000)

    def _full_status(self):
        s = {
            "timestamp": datetime.now().isoformat(),
            "workers": self.db.get_all_dashboard_states(),
            "task_stats": self.db.get_task_stats(),
            "escalations": self.db.get_pending_escalations(5),
            "activity": self.db.get_recent_activity(8),
        }
        if self.role_router:
            s["roles"] = self.role_router.get_all_assignments()
            s["available_workers"] = self.role_router.get_available_workers()
        return s

    # --- WebSocket ---

    async def _websocket(self, req):
        ws = web.WebSocketResponse()
        await ws.prepare(req)
        self.ws_clients.add(ws)
        try:
            async for msg in ws:
                if msg.type == aio.WSMsgType.TEXT:
                    try:
                        cmd = json.loads(msg.data)
                        action = cmd.get("action")
                        if action == "resolve_escalation":
                            self.db.request_write("update", "escalations", {
                                "_where": {"escalation_id": int(cmd["escalation_id"])},
                                "human_decision": cmd.get("decision", ""),
                                "status": "resolved",
                                "resolved_at": datetime.now().isoformat(),
                            })
                        elif action == "swap_role" and self.role_router:
                            result = self.role_router.swap_role(
                                cmd["role"], cmd["primary"],
                                cmd.get("fallback"))
                            await ws.send_str(json.dumps({"event": "role_swapped", **result}))
                            cfg_path = self.config.get("_config_path")
                            if cfg_path:
                                self.role_router.save_to_config_file(cfg_path)
                        elif action == "new_session" and self.orchestrator:
                            result = self.orchestrator.new_chat_session(cmd.get("name"))
                            await self._broadcast_to_all(json.dumps({"event": "session_changed", **result}))
                        elif action == "switch_session" and self.orchestrator:
                            result = self.orchestrator.switch_chat_session(cmd.get("session_id", ""))
                            await self._broadcast_to_all(json.dumps({"event": "session_changed", **result}))
                        elif action == "close_session" and self.orchestrator:
                            result = self.orchestrator.close_chat_session(cmd.get("session_id", ""))
                            await self._broadcast_to_all(json.dumps({"event": "session_changed", **result}))
                        elif action == "select_project" and self.orchestrator:
                            result = self.orchestrator.select_project(cmd.get("project_id"))
                            await ws.send_str(json.dumps({"event": "project_selected", **result}))
                        elif action == "chat_message":
                            await self._handle_ws_chat(cmd, ws)
                        elif action == "direct_chat":
                            await self._handle_ws_direct(cmd, ws)
                        elif action == "project_chat":
                            await self._handle_ws_project(cmd, ws)
                        elif action == "discussion_chat":
                            await self._handle_ws_discussion(cmd, ws)
                        elif action == "discussion_cancel":
                            if self.orchestrator:
                                self.orchestrator.cancel_discussion_round()
                        elif action == "chat_stop":
                            if self.orchestrator:
                                self.orchestrator.cancel_discussion_round()
                            if self._current_chat_task and not self._current_chat_task.done():
                                self._current_chat_task.cancel()
                                logger.info("Chat stopped by user (WS)")
                            await self._broadcast_to_all(json.dumps({
                                "event": "chat_stopped",
                                "timestamp": datetime.now().isoformat(),
                            }))
                        elif action == "discussion_update_participants":
                            if self.orchestrator:
                                self.orchestrator.update_discussion_participants(
                                    cmd.get("participants", []))
                        # M4: Project execution via WS
                        elif action == "launch_project" and self.orchestrator:
                            pid = cmd.get("project_id", "")
                            project = self.db.get_project(pid)
                            if not project:
                                await ws.send_str(json.dumps({"event": "project_error", "error": "Project not found"}))
                            elif pid in self._running_projects and not self._running_projects[pid].done():
                                await ws.send_str(json.dumps({"event": "project_error", "error": "Already running"}))
                            else:
                                t = asyncio.create_task(
                                    self.orchestrator.execute_project(pid, on_progress=self._on_project_progress))
                                self._running_projects[pid] = t
                                t.add_done_callback(lambda _t, _pid=pid: asyncio.create_task(self._on_execute_complete(_t, _pid)))
                                await ws.send_str(json.dumps({"event": "project_launched", "project_id": pid}))
                        elif action == "approve_blueprint" and self.orchestrator:
                            pid = cmd.get("project_id", "")
                            result = await self.orchestrator.approve_blueprint(pid)
                            if result.get("success"):
                                await self._broadcast_to_all(json.dumps({
                                    "event": "blueprint_approved", "project_id": pid,
                                    "timestamp": datetime.now().isoformat()}))
                                # Re-launch execute_project to continue with phases 1-3
                                t = asyncio.create_task(
                                    self.orchestrator.execute_project(pid, on_progress=self._on_project_progress))
                                self._running_projects[pid] = t
                                t.add_done_callback(lambda _t, _pid=pid: asyncio.create_task(self._on_execute_complete(_t, _pid)))
                            else:
                                await ws.send_str(json.dumps({"event": "project_error", "error": result.get("error", "Failed")}))
                        elif action == "approve_uat" and self.orchestrator:
                            pid = cmd.get("project_id", "")
                            result = await self.orchestrator.approve_uat(pid)
                            if result.get("success"):
                                await self._broadcast_to_all(json.dumps({
                                    "event": "uat_approved", "project_id": pid,
                                    "timestamp": datetime.now().isoformat()}))
                            else:
                                await ws.send_str(json.dumps({"event": "project_error", "error": result.get("error", "Failed")}))
                    except Exception as e:
                        logger.error(f"WS command error: {e}")
        finally:
            self.ws_clients.discard(ws)
        return ws

    async def _handle_ws_chat(self, cmd: dict, sender_ws):
        """Handle orchestrator chat message over WebSocket."""
        message = cmd.get("message", "").strip()
        if not message:
            await sender_ws.send_str(json.dumps({
                "event": "chat_error", "error": "Empty message"}))
            return
        if not self.orchestrator:
            await sender_ws.send_str(json.dumps({
                "event": "chat_error", "error": "Orchestrator not connected"}))
            return

        user_event = json.dumps({
            "event": "chat_message", "mode": "orchestrator",
            "role": "user", "content": message,
            "timestamp": datetime.now().isoformat(),
        })
        await self._broadcast_to_all(user_event)

        try:
            self._current_chat_task = asyncio.current_task()
            response = await self.orchestrator.handle_message(message)
            resp_text = response.get("response", response.get("error", str(response)))
        except asyncio.CancelledError:
            logger.info("Orchestrator chat cancelled by user")
            resp_text = "[Stopped by user]"
        except Exception as e:
            logger.error(f"Chat error: {e}")
            resp_text = f"Error: {e}"
        finally:
            self._current_chat_task = None

        orch_event = json.dumps({
            "event": "chat_message", "mode": "orchestrator",
            "role": "assistant", "content": resp_text,
            "worker": "orchestrator",
            "timestamp": datetime.now().isoformat(),
        })
        await self._broadcast_to_all(orch_event)

    async def _handle_ws_direct(self, cmd: dict, sender_ws):
        """Handle direct chat with a specific worker over WebSocket."""
        message = cmd.get("message", "").strip()
        worker_name = cmd.get("worker", "").strip()
        if not message or not worker_name:
            await sender_ws.send_str(json.dumps({
                "event": "chat_error", "error": "Missing message or worker"}))
            return
        if not self.orchestrator:
            await sender_ws.send_str(json.dumps({
                "event": "chat_error", "error": "Orchestrator not connected"}))
            return

        user_event = json.dumps({
            "event": "chat_message", "mode": "direct",
            "role": "user", "content": message, "worker": worker_name,
            "timestamp": datetime.now().isoformat(),
        })
        await self._broadcast_to_all(user_event)

        response = {}
        try:
            self._current_chat_task = asyncio.current_task()
            response = await self.orchestrator.direct_chat(worker_name, message)
            resp_text = response.get("response", response.get("error", str(response)))
        except asyncio.CancelledError:
            logger.info(f"Direct chat to {worker_name} cancelled by user")
            resp_text = "[Stopped by user]"
        except Exception as e:
            logger.error(f"Direct chat error: {e}")
            resp_text = f"Error: {e}"
        finally:
            self._current_chat_task = None

        resp_event = json.dumps({
            "event": "chat_message", "mode": "direct",
            "role": "assistant", "content": resp_text,
            "worker": worker_name,
            "elapsed_ms": response.get("elapsed_ms") if isinstance(response, dict) else None,
            "timestamp": datetime.now().isoformat(),
        })
        await self._broadcast_to_all(resp_event)

    async def _handle_ws_project(self, cmd: dict, sender_ws):
        """Handle project-context chat with a specific worker over WebSocket."""
        message = cmd.get("message", "").strip()
        worker_name = cmd.get("worker", "").strip()
        if not message or not worker_name:
            await sender_ws.send_str(json.dumps({
                "event": "chat_error", "error": "Missing message or worker"}))
            return
        if not self.orchestrator:
            await sender_ws.send_str(json.dumps({
                "event": "chat_error", "error": "Orchestrator not connected"}))
            return

        user_event = json.dumps({
            "event": "chat_message", "mode": "project",
            "role": "user", "content": message, "worker": worker_name,
            "timestamp": datetime.now().isoformat(),
        })
        await self._broadcast_to_all(user_event)

        response = {}
        try:
            self._current_chat_task = asyncio.current_task()
            response = await self.orchestrator.project_chat(worker_name, message)
            resp_text = response.get("response", response.get("error", str(response)))
        except asyncio.CancelledError:
            logger.info(f"Project chat to {worker_name} cancelled by user")
            resp_text = "[Stopped by user]"
        except Exception as e:
            logger.error(f"Project chat error: {e}")
            resp_text = f"Error: {e}"
        finally:
            self._current_chat_task = None

        resp_event = json.dumps({
            "event": "chat_message", "mode": "project",
            "role": "assistant", "content": resp_text,
            "worker": worker_name,
            "project_id": response.get("project_id") if isinstance(response, dict) else None,
            "elapsed_ms": response.get("elapsed_ms") if isinstance(response, dict) else None,
            "timestamp": datetime.now().isoformat(),
        })
        await self._broadcast_to_all(resp_event)

    async def _handle_ws_discussion(self, cmd: dict, sender_ws):
        """Handle discussion mode: multi-model sequential chat over WebSocket."""
        message = cmd.get("message", "").strip()
        participants = cmd.get("participants", [])
        auto_loop = cmd.get("auto_loop", False)
        if not message or not participants:
            await sender_ws.send_str(json.dumps({
                "event": "chat_error",
                "error": "Missing message or participants"}))
            return
        if not self.orchestrator:
            await sender_ws.send_str(json.dumps({
                "event": "chat_error",
                "error": "Orchestrator not connected"}))
            return

        # Broadcast user message immediately
        user_event = json.dumps({
            "event": "chat_message", "mode": "discussion",
            "role": "user", "content": message,
            "timestamp": datetime.now().isoformat(),
        })
        await self._broadcast_to_all(user_event)

        # Signal discussion start
        await self._broadcast_to_all(json.dumps({
            "event": "discussion_start",
            "participants": participants,
            "auto_loop": auto_loop,
        }))

        async def on_response(worker_name, text, elapsed_ms):
            """Callback: broadcast each model's response as it arrives."""
            # Round markers are system-level messages
            if worker_name == "__round__":
                await self._broadcast_to_all(json.dumps({
                    "event": "chat_message", "mode": "discussion",
                    "role": "system", "content": text,
                    "timestamp": datetime.now().isoformat(),
                }))
                return
            resp_event = json.dumps({
                "event": "chat_message", "mode": "discussion",
                "role": "assistant", "content": text,
                "worker": worker_name,
                "elapsed_ms": elapsed_ms,
                "timestamp": datetime.now().isoformat(),
            })
            await self._broadcast_to_all(resp_event)

        try:
            result = await self.orchestrator.discussion_chat(
                participants, message, on_response=on_response,
                auto_loop=auto_loop)
        except Exception as e:
            logger.error(f"Discussion chat error: {e}")
            await self._broadcast_to_all(json.dumps({
                "event": "chat_error", "error": str(e)}))

        # Signal discussion end
        await self._broadcast_to_all(json.dumps({
            "event": "discussion_end",
            "cancelled": result.get("cancelled", False) if isinstance(result, dict) else False,
        }))

    async def _broadcast_to_all(self, data: str):
        """Send data to all connected WebSocket clients."""
        dead = set()
        for ws in self.ws_clients:
            try:
                await ws.send_str(data)
            except Exception:
                logger.debug("WebSocket broadcast send failed", exc_info=True)
                dead.add(ws)
        self.ws_clients -= dead

    # --- REST APIs ---

    async def _index(self, req):
        html = DASHBOARD_HTML.replace("{{REFRESH_MS}}", str(self.refresh_ms))
        return web.Response(text=html, content_type="text/html")

    async def _api_status(self, req):
        return web.json_response(self._full_status())

    async def _api_tasks(self, req):
        s = req.query.get("status")
        return web.json_response(
            self.db.get_tasks_by_status(s) if s else self.db.get_task_stats())

    async def _api_escalations(self, req):
        return web.json_response(self.db.get_pending_escalations(20))

    async def _api_resolve(self, req):
        eid = req.match_info["id"]
        body = await req.json()
        self.db.request_write("update", "escalations", {
            "_where": {"escalation_id": int(eid)},
            "human_decision": body.get("decision", ""),
            "status": "resolved",
            "resolved_at": datetime.now().isoformat(),
        })
        return web.json_response({"resolved": True})

    async def _api_activity(self, req):
        return web.json_response(
            self.db.get_recent_activity(int(req.query.get("limit", 10))))

    async def _api_get_roles(self, req):
        if not self.role_router:
            return web.json_response({"error": "Role router not initialized"}, status=503)
        return web.json_response({
            "roles": self.role_router.get_all_assignments(),
            "available_workers": self.role_router.get_available_workers(),
        })

    async def _api_swap_role(self, req):
        if not self.role_router:
            return web.json_response({"error": "Role router not initialized"}, status=503)
        body = await req.json()
        result = self.role_router.swap_role(
            body["role"], body["primary"], body.get("fallback"))
        if result.get("success"):
            cfg_path = self.config.get("_config_path")
            if cfg_path:
                self.role_router.save_to_config_file(cfg_path)
        return web.json_response(result)

    async def _api_available_workers(self, req):
        if not self.role_router:
            return web.json_response([])
        return web.json_response(self.role_router.get_available_workers())

    # --- Chat REST APIs ---

    async def _api_chat(self, req):
        """POST /api/chat -- orchestrator mode chat."""
        body = await req.json()
        message = body.get("message", "").strip()
        if not message:
            return web.json_response({"error": "Empty message"}, status=400)
        if not self.orchestrator:
            return web.json_response({"error": "Orchestrator not connected"}, status=503)

        try:
            self._current_chat_task = asyncio.current_task()
            response = await self.orchestrator.handle_message(message)
            resp_text = response.get("response", response.get("error", str(response)))

            for event_data in [
                {"event": "chat_message", "mode": "orchestrator",
                 "role": "user", "content": message,
                 "timestamp": datetime.now().isoformat()},
                {"event": "chat_message", "mode": "orchestrator",
                 "role": "assistant", "content": resp_text,
                 "worker": "orchestrator",
                 "timestamp": datetime.now().isoformat()},
            ]:
                await self._broadcast_to_all(json.dumps(event_data))

            return web.json_response({
                "response": resp_text,
                "handler": response.get("_handler"),
                "history_length": len(self.orchestrator.chat_history),
            })
        except asyncio.CancelledError:
            return web.json_response({"response": "[Stopped by user]", "stopped": True})
        except Exception as e:
            logger.error(f"Chat API error: {e}")
            return web.json_response({"error": str(e)}, status=500)
        finally:
            self._current_chat_task = None

    async def _api_chat_direct(self, req):
        """POST /api/chat/direct -- send to a specific worker directly."""
        body = await req.json()
        message = body.get("message", "").strip()
        worker_name = body.get("worker", "").strip()
        if not message or not worker_name:
            return web.json_response({"error": "Missing message or worker"}, status=400)
        if not self.orchestrator:
            return web.json_response({"error": "Orchestrator not connected"}, status=503)

        try:
            self._current_chat_task = asyncio.current_task()
            response = await self.orchestrator.direct_chat(worker_name, message)
            resp_text = response.get("response", response.get("error", str(response)))

            for event_data in [
                {"event": "chat_message", "mode": "direct",
                 "role": "user", "content": message, "worker": worker_name,
                 "timestamp": datetime.now().isoformat()},
                {"event": "chat_message", "mode": "direct",
                 "role": "assistant", "content": resp_text, "worker": worker_name,
                 "elapsed_ms": response.get("elapsed_ms"),
                 "timestamp": datetime.now().isoformat()},
            ]:
                await self._broadcast_to_all(json.dumps(event_data))

            return web.json_response({
                "response": resp_text,
                "worker": worker_name,
                "elapsed_ms": response.get("elapsed_ms"),
            })
        except asyncio.CancelledError:
            return web.json_response({"response": "[Stopped by user]", "stopped": True})
        except Exception as e:
            logger.error(f"Direct chat API error: {e}")
            return web.json_response({"error": str(e)}, status=500)
        finally:
            self._current_chat_task = None

    async def _api_chat_project(self, req):
        """POST /api/chat/project -- send to worker with project context."""
        body = await req.json()
        message = body.get("message", "").strip()
        worker_name = body.get("worker", "").strip()
        if not message or not worker_name:
            return web.json_response({"error": "Missing message or worker"}, status=400)
        if not self.orchestrator:
            return web.json_response({"error": "Orchestrator not connected"}, status=503)

        try:
            self._current_chat_task = asyncio.current_task()
            response = await self.orchestrator.project_chat(worker_name, message)
            resp_text = response.get("response", response.get("error", str(response)))

            for event_data in [
                {"event": "chat_message", "mode": "project",
                 "role": "user", "content": message, "worker": worker_name,
                 "timestamp": datetime.now().isoformat()},
                {"event": "chat_message", "mode": "project",
                 "role": "assistant", "content": resp_text, "worker": worker_name,
                 "project_id": response.get("project_id"),
                 "elapsed_ms": response.get("elapsed_ms"),
                 "timestamp": datetime.now().isoformat()},
            ]:
                await self._broadcast_to_all(json.dumps(event_data))

            return web.json_response({
                "response": resp_text,
                "worker": worker_name,
                "project_id": response.get("project_id"),
                "elapsed_ms": response.get("elapsed_ms"),
            })
        except asyncio.CancelledError:
            return web.json_response({"response": "[Stopped by user]", "stopped": True})
        except Exception as e:
            logger.error(f"Project chat API error: {e}")
            return web.json_response({"error": str(e)}, status=500)
        finally:
            self._current_chat_task = None

    async def _api_chat_discussion(self, req):
        """POST /api/chat/discussion -- multi-model discussion (REST fallback)."""
        body = await req.json()
        message = body.get("message", "").strip()
        participants = body.get("participants", [])
        auto_loop = body.get("auto_loop", False)
        if not message or not participants:
            return web.json_response(
                {"error": "Missing message or participants"}, status=400)
        if not self.orchestrator:
            return web.json_response(
                {"error": "Orchestrator not connected"}, status=503)
        try:
            result = await self.orchestrator.discussion_chat(
                participants, message, auto_loop=auto_loop)
            return web.json_response(result)
        except Exception as e:
            logger.error(f"Discussion chat API error: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def _api_chat_stop(self, req):
        """POST /api/chat/stop -- cancel in-progress chat request."""
        stopped = False
        # Cancel discussion if in progress
        if self.orchestrator:
            self.orchestrator.cancel_discussion_round()
        # Cancel the current chat task
        if self._current_chat_task and not self._current_chat_task.done():
            self._current_chat_task.cancel()
            stopped = True
            logger.info("Chat stopped by user")
        await self._broadcast_to_all(json.dumps({
            "event": "chat_stopped",
            "timestamp": datetime.now().isoformat(),
        }))
        return web.json_response({"stopped": stopped})

    async def _api_chat_history(self, req):
        """GET /api/chat/history -- return orchestrator's chat_history list.
        Optional ?project_id= to filter by project."""
        if not self.orchestrator:
            return web.json_response({"error": "Orchestrator not connected"}, status=503)
        project_id = req.query.get("project_id")
        if project_id:
            return web.json_response(
                self.orchestrator.get_chat_history_filtered(project_id))
        return web.json_response(self.orchestrator.chat_history)

    async def _api_chat_download(self, req):
        """GET /api/chat/download?format=json|md — export full session chat."""
        if not self.orchestrator:
            return web.json_response({"error": "Orchestrator not connected"}, status=503)
        fmt = req.query.get("format", "json")
        if fmt not in ("json", "md"):
            return web.json_response({"error": f"Unsupported format: {fmt}. Use json or md."}, status=400)

        # Flush pending writes so cold archive is complete
        if self.watchdog:
            try:
                await self.watchdog.db.drain_write_queue(
                    self.watchdog.write_queue, self.watchdog.result_bus)
            except Exception as e:
                logger.warning(f"Export flush warning: {e}")

        session_id = self.orchestrator.session_id
        session_name = self.orchestrator._get_session_name(session_id)

        # Merge cold (DB archive) + warm (in-memory)
        cold = self.db.get_all_session_messages(session_id)
        warm = list(self.orchestrator.chat_history)
        merged = _merge_and_dedup(cold, warm)

        if fmt == "json":
            payload = json.dumps({
                "session_id": session_id,
                "session_name": session_name,
                "exported_at": datetime.now().isoformat(),
                "message_count": len(merged),
                "messages": merged,
            }, indent=2, default=str, ensure_ascii=False)
            return web.Response(
                text=payload,
                content_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="chat-{session_id}.json"'},
            )
        else:
            md = _format_chat_markdown(merged, session_id, session_name)
            return web.Response(
                text=md,
                content_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="chat-{session_id}.md"'},
            )

    async def _api_workers_status(self, req):
        """GET /api/workers/status -- list all workers with type/model info."""
        if not self.orchestrator:
            return web.json_response([])
        return web.json_response(
            self.orchestrator.get_available_workers_with_status())


    # --- Chat Session REST APIs ---

    async def _api_chat_sessions(self, req):
        """GET /api/chat/sessions -- list chat sessions."""
        if not self.orchestrator:
            return web.json_response({"error": "Orchestrator not connected"}, status=503)
        return web.json_response(self.orchestrator.list_chat_sessions())

    async def _api_chat_session_new(self, req):
        """POST /api/chat/sessions/new -- create a new chat session."""
        if not self.orchestrator:
            return web.json_response({"error": "Orchestrator not connected"}, status=503)
        body = await req.json()
        result = self.orchestrator.new_chat_session(body.get("name"))
        return web.json_response(result)

    async def _api_chat_session_switch(self, req):
        """POST /api/chat/sessions/switch -- switch to a session."""
        if not self.orchestrator:
            return web.json_response({"error": "Orchestrator not connected"}, status=503)
        body = await req.json()
        session_id = body.get("session_id", "").strip()
        if not session_id:
            return web.json_response({"error": "Missing session_id"}, status=400)
        result = self.orchestrator.switch_chat_session(session_id)
        if result.get("error"):
            return web.json_response(result, status=404)
        return web.json_response(result)

    async def _api_chat_session_rename(self, req):
        """POST /api/chat/sessions/rename -- rename a session."""
        if not self.orchestrator:
            return web.json_response({"error": "Orchestrator not connected"}, status=503)
        body = await req.json()
        session_id = body.get("session_id", "").strip()
        name = body.get("name", "").strip()
        if not session_id or not name:
            return web.json_response({"error": "Missing session_id or name"}, status=400)
        result = self.orchestrator.rename_chat_session(session_id, name)
        if result.get("error"):
            return web.json_response(result, status=404)
        return web.json_response(result)

    async def _api_chat_session_close(self, req):
        """POST /api/chat/sessions/close -- close/remove a session tab."""
        if not self.orchestrator:
            return web.json_response({"error": "Orchestrator not connected"}, status=503)
        body = await req.json()
        session_id = body.get("session_id", "").strip()
        if not session_id:
            return web.json_response({"error": "Missing session_id"}, status=400)
        result = self.orchestrator.close_chat_session(session_id)
        if result.get("error"):
            return web.json_response(result, status=400)
        return web.json_response(result)

    # --- Project REST APIs ---

    async def _api_projects(self, req):
        """GET /api/projects -- list projects + current selection."""
        include_completed = req.query.get("include_completed", "").lower() in ("1", "true")
        projects = self.db.list_projects(include_completed=include_completed)
        current = self.orchestrator.current_project if self.orchestrator else None
        return web.json_response({
            "projects": projects,
            "current_project": current,
        })

    async def _api_project_select(self, req):
        """POST /api/projects/select -- switch active project."""
        if not self.orchestrator:
            return web.json_response({"error": "Orchestrator not connected"}, status=503)
        body = await req.json()
        project_id = body.get("project_id")  # None = deselect
        result = self.orchestrator.select_project(project_id)
        if result.get("error"):
            return web.json_response(result, status=404)
        return web.json_response(result)

    async def _api_project_create(self, req):
        """POST /api/projects/create -- create a new project."""
        if not self.orchestrator:
            return web.json_response({"error": "Orchestrator not connected"}, status=503)
        body = await req.json()
        name = body.get("name", "").strip()
        description = body.get("description", "").strip()
        if not name:
            return web.json_response({"error": "Project name required"}, status=400)
        try:
            result = await self.orchestrator.create_project(name, description)
            return web.json_response(result)
        except Exception as e:
            logger.error(f"Project create error: {e}")
            return web.json_response({"error": str(e)}, status=500)

    # --- M4: Project Execution REST APIs ---

    async def _api_project_launch(self, req):
        """POST /api/projects/launch -- start execute_project() as background task."""
        if not self.orchestrator:
            return web.json_response({"error": "Orchestrator not connected"}, status=503)
        body = await req.json()
        project_id = body.get("project_id", "").strip()
        if not project_id:
            return web.json_response({"error": "Missing project_id"}, status=400)

        project = self.db.get_project(project_id)
        if not project:
            return web.json_response({"error": "Project not found"}, status=404)

        if project_id in self._running_projects:
            task = self._running_projects[project_id]
            if not task.done():
                return web.json_response({"error": "Project already running"}, status=409)

        # Immediately notify UI that project has started — don't wait for WS poll
        await self._broadcast_to_all(json.dumps({
            "event": "project_launched",
            "project_id": project_id,
            "timestamp": datetime.now().isoformat(),
        }))

        task = asyncio.create_task(
            self.orchestrator.execute_project(project_id, on_progress=self._on_project_progress)
        )
        self._running_projects[project_id] = task
        task.add_done_callback(lambda t, _pid=project_id: asyncio.create_task(self._on_execute_complete(t, _pid)))

        return web.json_response({
            "started": True, "project_id": project_id,
            "message": "Project execution started"
        })

    async def _api_project_progress(self, req):
        """GET /api/projects/{id}/progress -- fetch phase/step/tasks/bugs for project."""
        project_id = req.match_info["id"]
        project = self.db.get_project(project_id)
        if not project:
            return web.json_response({"error": "Project not found"}, status=404)

        current_phase = project.get("current_phase", 0)
        task_stats = self.db.get_task_stats(project_id)

        # Get tasks for current phase
        phase_tasks = []
        if hasattr(self.db, "get_tasks_by_phase"):
            phase_tasks = self.db.get_tasks_by_phase(project_id, current_phase)

        # Get escalations for this project
        escalations = [
            e for e in self.db.get_pending_escalations(20)
            if e.get("context_data") and project_id in str(e.get("context_data", ""))
        ]

        # Check if running
        is_running = (
            project_id in self._running_projects
            and not self._running_projects[project_id].done()
        )

        return web.json_response({
            "project_id": project_id,
            "name": project.get("name"),
            "status": project.get("status"),
            "current_phase": current_phase,
            "task_stats": task_stats,
            "phase_tasks": [
                {
                    "task_id": t.get("task_id"),
                    "description": t.get("description"),
                    "status": t.get("status"),
                    "current_step": t.get("current_step"),
                    "dac_tag": t.get("dac_tag"),
                }
                for t in phase_tasks
            ],
            "escalations": escalations,
            "is_running": is_running,
        })

    async def _api_project_blueprint(self, req):
        """GET /api/projects/{id}/blueprint -- fetch latest blueprint + contracts."""
        project_id = req.match_info["id"]
        project = self.db.get_project(project_id)
        if not project:
            return web.json_response({"error": "Project not found"}, status=404)

        blueprint = self.db.get_latest_blueprint(project_id)
        contracts = {}
        project_path = project.get("project_path", "")
        if project_path:
            import os
            contracts_dir = os.path.join(project_path, "contracts")
            for fname in ("api_contract.json", "db_schema.sql", "types.json"):
                fpath = os.path.join(contracts_dir, fname)
                if os.path.exists(fpath):
                    with open(fpath, "r") as f:
                        contracts[fname] = f.read()

        return web.json_response({
            "blueprint": {
                "version": blueprint.get("version") if blueprint else None,
                "content": blueprint.get("blueprint_content") if blueprint else None,
                "approved_by": blueprint.get("approved_by") if blueprint else None,
                "approved_at": blueprint.get("approved_at") if blueprint else None,
            } if blueprint else None,
            "contracts": contracts,
        })

    async def _api_approve_blueprint(self, req):
        """POST /api/projects/{id}/approve-blueprint -- human blueprint approval."""
        if not self.orchestrator:
            return web.json_response({"error": "Orchestrator not connected"}, status=503)
        project_id = req.match_info["id"]
        result = await self.orchestrator.approve_blueprint(project_id)
        if result.get("success"):
            await self._broadcast_to_all(json.dumps({
                "event": "blueprint_approved",
                "project_id": project_id,
                "timestamp": datetime.now().isoformat(),
            }))
            # Re-launch execute_project to continue with phases 1-3
            t = asyncio.create_task(
                self.orchestrator.execute_project(project_id, on_progress=self._on_project_progress))
            self._running_projects[project_id] = t
            t.add_done_callback(lambda _t, _pid=project_id: asyncio.create_task(self._on_execute_complete(_t, _pid)))
        return web.json_response(result)

    async def _api_approve_uat(self, req):
        """POST /api/projects/{id}/approve-uat -- human UAT approval -> production deploy."""
        if not self.orchestrator:
            return web.json_response({"error": "Orchestrator not connected"}, status=503)
        project_id = req.match_info["id"]
        result = await self.orchestrator.approve_uat(project_id)
        if result.get("success"):
            await self._broadcast_to_all(json.dumps({
                "event": "uat_approved",
                "project_id": project_id,
                "timestamp": datetime.now().isoformat(),
            }))
        return web.json_response(result)

    async def _api_validate_training_data(self, req):
        """POST /api/training-data/{id}/validate — set validated=True (Issue 7/G3)."""
        training_id = int(req.match_info["id"])
        if not self.watchdog:
            return web.json_response({"error": "Watchdog not connected"}, status=503)
        ok = self.watchdog.validate_training_data(training_id)
        return web.json_response({"success": ok, "training_id": training_id})

    async def _on_execute_complete(self, task: asyncio.Task, project_id: str):
        """Shared done-callback for execute_project tasks — broadcasts blueprint_ready / uat_ready."""
        try:
            result = task.result()
            awaiting = result.get("awaiting")
            if awaiting == "blueprint_approval":
                await self._broadcast_to_all(json.dumps({
                    "event": "blueprint_ready",
                    "project_id": project_id,
                    "timestamp": datetime.now().isoformat(),
                }))
            elif awaiting == "uat_approval":
                # Issue 1: Include e2e_passed so dashboard can show red warning (R4/R10/M4)
                e2e = result.get("e2e", {})
                e2e_passed = e2e.get("success", True) and not e2e.get("skipped", False)
                await self._broadcast_to_all(json.dumps({
                    "event": "uat_ready",
                    "project_id": project_id,
                    "phases_completed": result.get("phases_completed", 0),
                    "e2e_passed": e2e_passed,
                    "e2e_output": e2e.get("output_tail", "")[:500],
                    "timestamp": datetime.now().isoformat(),
                }))
        except Exception as e:
            logger.error(f"Project execution error ({project_id}): {e}")
            await self._broadcast_to_all(json.dumps({
                "event": "project_error",
                "project_id": project_id,
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }))

    async def _on_project_progress(self, phase: int, step: str, status: str, detail: str):
        """Progress callback invoked by execute_project() for live dashboard updates."""
        tdd_steps = {"AC", "RED", "GREEN", "BC", "BF", "SEA", "DS", "OA", "VB", "GIT", "CL", "CCP", "AD"}
        event_name = "tdd_step_update" if step.upper() in tdd_steps else "project_progress"
        await self._broadcast_to_all(json.dumps({
            "event": event_name,
            "phase": phase,
            "step": step,
            "status": status,
            "detail": detail,
            "timestamp": datetime.now().isoformat(),
        }))

    async def _api_project_delete(self, req):
        """DELETE /api/projects/{id} -- delete project and all its data."""
        project_id = req.match_info["id"]
        project = self.db.get_project(project_id)
        if not project:
            return web.json_response({"error": "Project not found"}, status=404)

        # Cancel any running task first
        if project_id in self._running_projects:
            task = self._running_projects[project_id]
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            del self._running_projects[project_id]

        # Delete project and all FK-dependent rows
        try:
            if self.watchdog and hasattr(self.watchdog, "db"):
                rows = self.watchdog.db.delete_project(project_id)
                if rows == 0:
                    logger.warning(f"delete_project({project_id}) affected 0 rows")
            else:
                return web.json_response({"error": "Watchdog not available"}, status=503)
        except Exception as e:
            logger.error(f"Project delete DB error: {e}")
            return web.json_response({"error": str(e)}, status=500)

        await self._broadcast_to_all(json.dumps({
            "event": "project_deleted",
            "project_id": project_id,
            "timestamp": datetime.now().isoformat(),
        }))
        return web.json_response({"success": True, "project_id": project_id})

    async def _api_config_mode(self, req):
        """POST /api/config/mode -- toggle local LLM test mode.
        Body: {"mode": "local"} or {"mode": "production"}
        """
        if not self.role_router:
            return web.json_response({"error": "Role router not connected"}, status=503)
        body = await req.json()
        mode = body.get("mode", "").lower()
        if mode not in ("local", "production"):
            return web.json_response(
                {"error": "mode must be 'local' or 'production'"}, status=400
            )
        result = self.role_router.set_local_mode(mode == "local")
        if not result.get("success"):
            return web.json_response(result, status=400)

        await self._broadcast_to_all(json.dumps({
            "event": "mode_changed",
            "local_mode": result["local_mode"],
            "timestamp": datetime.now().isoformat(),
        }))
        return web.json_response(result)

    async def _api_config_mode_get(self, req):
        """GET /api/config/mode -- get current mode."""
        local_mode = self.role_router.is_local_mode if self.role_router else False
        return web.json_response({"local_mode": local_mode})

    async def _api_chat_search(self, req):
        """GET /api/chat/search?q=keyword&worker=qwen&limit=20 — search Phi3 summaries."""
        q = req.query.get("q", "").strip()
        if not q:
            return web.json_response({"error": "Missing 'q' parameter"}, status=400)
        worker = req.query.get("worker")
        mode = req.query.get("mode")
        limit = int(req.query.get("limit", 20))
        results = self.db.search_chats_by_keyword(q, worker=worker, mode=mode, limit=limit)
        return web.json_response({"results": results, "count": len(results), "query": q})

    async def _api_chat_archive(self, req):
        """GET /api/chat/archive?q=keyword&worker=qwen&mode=direct&offset=0&limit=50 — browse cold archive."""
        q = req.query.get("q")
        worker = req.query.get("worker")
        mode = req.query.get("mode")
        session_id = req.query.get("session_id")
        offset = int(req.query.get("offset", 0))
        limit = int(req.query.get("limit", 50))
        results = self.db.search_archive(
            keyword=q, worker=worker, mode=mode,
            session_id=session_id, offset=offset, limit=limit)
        total = self.db.get_archive_count(keyword=q, worker=worker, mode=mode)
        return web.json_response({
            "results": results, "count": len(results),
            "total": total, "offset": offset, "limit": limit,
        })


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Autonomous Factory -- Dashboard v1.5</title>
<style>
:root{--bg:#0a0e17;--panel:#111827;--border:#1e293b;--text:#e2e8f0;--dim:#64748b;
--green:#22c55e;--yellow:#eab308;--red:#ef4444;--blue:#3b82f6;--orange:#f97316;--cyan:#06b6d4;--purple:#a855f7}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'SF Mono','Fira Code',monospace;background:var(--bg);color:var(--text);
height:100vh;overflow:hidden;display:flex;flex-direction:column}

/* --- Header --- */
.hdr{display:flex;justify-content:space-between;align-items:center;padding:8px 20px;
background:var(--panel);border-bottom:1px solid var(--border);flex-shrink:0}
.hdr h1{font-size:14px;color:var(--cyan);letter-spacing:2px}
.hdr .m{font-size:11px;color:var(--dim)}

/* --- Split Layout --- */
.split{display:flex;flex:1;overflow:hidden}
.sidebar{width:360px;min-width:280px;max-width:480px;overflow-y:auto;border-right:1px solid var(--border);
padding:12px;display:flex;flex-direction:column;gap:12px;flex-shrink:0;scrollbar-width:thin}
.sidebar::-webkit-scrollbar{width:5px}
.sidebar::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
.main-chat{flex:1;display:flex;flex-direction:column;overflow:hidden}

/* --- Resize Handle --- */
.resize-handle{width:4px;cursor:col-resize;background:transparent;flex-shrink:0;
position:relative;z-index:10;transition:background .2s}
.resize-handle:hover,.resize-handle.active{background:var(--cyan)}

/* --- Sidebar Panels --- */
.pnl{background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:12px}
.pnl h2{font-size:10px;color:var(--dim);letter-spacing:2px;text-transform:uppercase;
margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid var(--border);
display:flex;justify-content:space-between;align-items:center;cursor:pointer;user-select:none}
.pnl h2::after{content:'\25B2';font-size:8px;transition:transform .2s}
.pnl.collapsed h2::after{transform:rotate(180deg)}
.pnl.collapsed .pnl-body{display:none}
table{width:100%;border-collapse:collapse;font-size:11px}
th{text-align:left;color:var(--dim);font-size:10px;text-transform:uppercase;letter-spacing:1px;padding:4px 6px}
td{padding:5px 6px;border-top:1px solid var(--border)}

.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px}
.dot.idle,.dot.active,.dot.healthy{background:var(--green)}
.dot.working{background:var(--blue);animation:p 1.5s infinite}
.dot.respawning{background:var(--yellow);animation:p 1s infinite}
.dot.crashed,.dot.offline{background:var(--red)}
@keyframes p{0%,100%{opacity:1}50%{opacity:.4}}

.cb{display:inline-block;width:50px;height:6px;background:#1e293b;border-radius:3px;overflow:hidden;vertical-align:middle}
.cf{height:100%;border-radius:3px;transition:width .3s}
.cf.ok{background:var(--green)}.cf.warn{background:var(--yellow)}.cf.crit{background:var(--red)}
.tb{display:flex;gap:4px;align-items:center;margin:3px 0}
.tb .l{width:80px;font-size:11px;color:var(--dim)}
.tb .b{flex:1;height:14px;background:#1e293b;border-radius:3px;overflow:hidden}
.tb .f{height:100%;border-radius:3px;transition:width .5s;display:flex;align-items:center;
padding-left:5px;font-size:9px;color:white}
.f.pending{background:var(--dim)}.f.in_progress{background:var(--blue)}.f.testing{background:var(--cyan)}
.f.review{background:var(--yellow)}.f.blocked{background:var(--red)}.f.approved{background:var(--green)}
.ei{padding:8px;margin:4px 0;border-radius:4px;border-left:3px solid var(--red);background:#1a1a2e;font-size:11px}
.ei .t{color:var(--orange);font-weight:bold}.ei .r{color:var(--dim);margin-top:3px}
.ei .a{margin-top:6px}
.btn{padding:3px 10px;border:1px solid var(--border);border-radius:3px;background:0;
color:var(--text);cursor:pointer;font-size:10px;font-family:inherit;margin-right:4px}
.btn:hover{background:var(--border)}
.btn.ap{border-color:var(--green);color:var(--green)}
.btn.rj{border-color:var(--red);color:var(--red)}
.ai{padding:4px 0;border-bottom:1px solid var(--border);font-size:11px;display:flex;gap:8px}
.ai .ti{color:var(--dim);white-space:nowrap}.ai .mg{color:var(--text)}
.dbw{font-size:9px;color:var(--dim);text-align:center;margin-top:6px;padding:3px;
border:1px dashed var(--border);border-radius:3px;background:#0d1117}

/* Role Config */
.role-row{display:flex;align-items:center;gap:6px;padding:6px 0;border-bottom:1px solid var(--border);flex-wrap:wrap}
.role-row:last-child{border-bottom:none}
.role-name{width:140px;font-size:11px;color:var(--cyan);font-weight:bold}
.role-sel{background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:3px;
padding:3px 6px;font-family:inherit;font-size:11px;cursor:pointer;min-width:90px}
.role-sel:focus{border-color:var(--cyan);outline:none}
.role-label{font-size:9px;color:var(--dim);width:50px}
.role-active{font-size:10px;padding:1px 6px;border-radius:3px;background:#052e16;color:var(--green)}
.role-inactive{font-size:10px;padding:1px 6px;border-radius:3px;background:#3b0000;color:var(--red)}
.swap-btn{padding:3px 8px;border:1px solid var(--purple);border-radius:3px;background:transparent;
color:var(--purple);cursor:pointer;font-size:10px;font-family:inherit;transition:all .2s}
.swap-btn:hover{background:var(--purple);color:white}
.swap-ok{animation:flash .5s}
@keyframes flash{0%{background:var(--green);color:white}100%{background:transparent}}
.role-header{display:flex;justify-content:space-between;align-items:center}
.save-note{font-size:9px;color:var(--green);opacity:0;transition:opacity .3s}
.save-note.show{opacity:1}

/* --- Connection Status --- */
.cs{position:fixed;top:6px;right:12px;padding:3px 8px;border-radius:10px;font-size:10px;z-index:100}
.cs.c{background:#052e16;color:var(--green)}.cs.d{background:#3b0000;color:var(--red)}

/* --- Chat Panel (Right Side) --- */
.chat-header{padding:10px 16px;border-bottom:1px solid var(--border);background:var(--panel);flex-shrink:0}
.chat-toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.mode-tabs{display:flex;gap:0}
.mode-tab{padding:5px 12px;font-size:11px;font-family:inherit;border:1px solid var(--border);
background:transparent;color:var(--dim);cursor:pointer;transition:all .2s}
.mode-tab:first-child{border-radius:4px 0 0 4px}
.mode-tab:last-child{border-radius:0 4px 4px 0}
.mode-tab:not(:first-child){border-left:none}
.mode-tab.active{background:var(--cyan);color:var(--bg);border-color:var(--cyan);font-weight:bold}
.mode-tab.active.direct-active{background:var(--purple);border-color:var(--purple)}
.mode-tab.active.project-active{background:var(--orange);border-color:var(--orange)}
.mode-tab.active.discussion-active{background:var(--cyan);border-color:var(--cyan)}
.worker-pick{display:flex;align-items:center;gap:6px}
.worker-pick label{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:1px}
.worker-pick select{background:var(--bg);color:var(--text);border:1px solid var(--border);
border-radius:4px;padding:4px 8px;font-family:inherit;font-size:12px;cursor:pointer}
.worker-pick select:focus{border-color:var(--cyan);outline:none}
.mode-badge{font-size:10px;padding:2px 8px;border-radius:3px;margin-left:auto}
.mode-badge.orch{background:#0c2d48;color:var(--cyan)}
.mode-badge.direct{background:#2d1b4e;color:var(--purple)}
.mode-badge.project{background:#3d2008;color:var(--orange)}
.mode-badge.discussion{background:#0c2d48;color:var(--cyan)}

/* --- Discussion Participant Checkboxes --- */
.participant-panel{display:none;align-items:center;gap:8px;flex-wrap:wrap;padding:4px 0}
.participant-panel.show{display:flex}
.participant-panel label{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:1px;flex-shrink:0}
.participant-chk{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border:1px solid var(--border);
border-radius:12px;font-size:11px;cursor:pointer;transition:all .2s;user-select:none}
.participant-chk:hover{border-color:var(--dim)}
.participant-chk input{accent-color:var(--cyan);cursor:pointer}
.participant-chk.checked{border-color:var(--cyan);background:#0c2d4866}

/* --- Discussion Worker-Colored Bubbles --- */
.chat-msg.assistant.discussion{border-left:3px solid var(--dim);padding-left:12px}
.chat-msg.assistant.discussion[data-worker="claude"]{border-left-color:var(--orange)}
.chat-msg.assistant.discussion[data-worker="gemini"]{border-left-color:var(--blue)}
.chat-msg.assistant.discussion[data-worker="kimi"]{border-left-color:var(--purple)}
.chat-msg.assistant.discussion[data-worker="deepseek"]{border-left-color:var(--green)}
.chat-msg.assistant.discussion[data-worker="qwen"]{border-left-color:var(--yellow)}
.chat-msg.assistant.discussion[data-worker="grok"]{border-left-color:var(--red)}
.chat-msg.assistant.discussion .ts{font-weight:bold}
.you-chk{border-color:var(--green)!important}
.you-chk.checked{background:#052e1666!important;border-color:var(--green)!important}

.session-bar{display:flex;align-items:center;gap:4px;padding:6px 16px;
overflow-x:auto;scrollbar-width:thin;border-bottom:1px solid var(--border);background:var(--panel);flex-shrink:0}
.session-bar::-webkit-scrollbar{height:4px}
.session-bar::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
.session-tab{padding:4px 12px;font-size:11px;font-family:inherit;border:1px solid var(--border);
border-radius:12px;background:transparent;color:var(--dim);cursor:pointer;white-space:nowrap;
transition:all .2s;flex-shrink:0}
.session-tab:hover{border-color:var(--dim);color:var(--text)}
.session-tab.active{background:var(--cyan);color:var(--bg);border-color:var(--cyan);font-weight:bold}
.session-new{padding:4px 10px;font-size:11px;font-family:inherit;border:1px dashed var(--dim);
border-radius:12px;background:transparent;color:var(--dim);cursor:pointer;white-space:nowrap;
transition:all .2s;flex-shrink:0}
.session-new:hover{border-color:var(--green);color:var(--green)}
.session-close{margin-left:6px;font-size:13px;opacity:0.4;cursor:pointer;vertical-align:middle}
.session-close:hover{opacity:1;color:var(--red)}
.dl-dropdown{position:relative;display:inline-block;flex-shrink:0}
.dl-dropdown-btn{padding:4px 10px;font-size:11px;font-family:inherit;border:1px dashed var(--dim);
border-radius:12px;background:transparent;color:var(--dim);cursor:pointer;white-space:nowrap;transition:all .2s}
.dl-dropdown-btn:hover{border-color:var(--cyan);color:var(--cyan)}
.dl-dropdown-content{display:none;position:absolute;bottom:100%;left:0;background:var(--panel);
border:1px solid var(--border);border-radius:6px;min-width:120px;z-index:50;margin-bottom:4px}
.dl-dropdown:hover .dl-dropdown-content{display:block}
.dl-dropdown-content a{display:block;padding:6px 12px;font-size:11px;color:var(--text);
text-decoration:none;cursor:pointer;font-family:inherit}
.dl-dropdown-content a:hover{background:var(--border);color:var(--cyan)}

.project-bar{display:flex;align-items:center;gap:8px;padding:6px 16px;
background:#0d1117;border-bottom:1px solid var(--border);transition:opacity .3s;flex-shrink:0}
.project-bar.dimmed{opacity:0.4;pointer-events:none}
.project-bar label{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:1px}
.project-bar select{background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:4px;
padding:4px 8px;font-family:inherit;font-size:12px;cursor:pointer;min-width:160px}
.project-bar select:focus{border-color:var(--cyan);outline:none}
.proj-new-btn{padding:4px 10px;border:1px solid var(--green);border-radius:4px;background:transparent;
color:var(--green);cursor:pointer;font-size:11px;font-family:inherit;transition:all .2s}
.proj-new-btn:hover{background:var(--green);color:var(--bg)}

/* --- Chat Messages --- */
.chat-log{flex:1;overflow-y:auto;padding:16px;font-size:13px;background:var(--bg);
scrollbar-width:thin}
.chat-log::-webkit-scrollbar{width:6px}
.chat-log::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}

.chat-msg{margin:8px 0;padding:10px 14px;border-radius:12px;max-width:80%;word-wrap:break-word;
line-height:1.5;position:relative}
.chat-msg.user{background:#1e293b;margin-left:auto;border-bottom-right-radius:4px}
.chat-msg.assistant{background:#0f1e2d;border-left:3px solid var(--green);border-bottom-left-radius:4px}
.chat-msg.assistant.direct{background:#1a1030;border-left-color:var(--purple)}
.chat-msg.assistant.project{background:#1f1508;border-left-color:var(--orange)}
.chat-msg.system{background:#1a1a2e;border-left:3px solid var(--dim);font-style:italic;color:var(--dim);max-width:100%}
.chat-msg .ts{font-size:10px;color:var(--dim);margin-bottom:4px;font-weight:600}
.chat-msg .wk{font-size:10px;color:var(--cyan);margin-top:6px;opacity:0.7}
.chat-msg .el{font-size:10px;color:var(--dim);margin-top:2px;opacity:0.6}

/* --- Rendered Markdown in Chat --- */
.chat-msg .ct{color:var(--text);line-height:1.6}
.chat-msg .ct p{margin:0 0 8px 0}
.chat-msg .ct p:last-child{margin-bottom:0}
.chat-msg .ct strong{color:#f1f5f9;font-weight:700}
.chat-msg .ct em{color:var(--dim);font-style:italic}
.chat-msg .ct code{background:#1e293b;color:var(--cyan);padding:2px 5px;border-radius:3px;font-size:0.9em}
.chat-msg .ct pre{background:#0d1117;border:1px solid var(--border);border-radius:6px;padding:12px;
margin:8px 0;overflow-x:auto;position:relative}
.chat-msg .ct pre code{background:none;color:var(--text);padding:0;font-size:12px;line-height:1.5}
.chat-msg .ct ul,.chat-msg .ct ol{margin:6px 0;padding-left:20px}
.chat-msg .ct li{margin:3px 0}
.chat-msg .ct h1,.chat-msg .ct h2,.chat-msg .ct h3{color:var(--cyan);margin:10px 0 6px 0;font-size:14px}
.chat-msg .ct h1{font-size:16px}.chat-msg .ct h2{font-size:15px}
.chat-msg .ct blockquote{border-left:3px solid var(--dim);padding-left:10px;margin:6px 0;color:var(--dim)}
.chat-msg .ct a{color:var(--cyan);text-decoration:none}
.chat-msg .ct a:hover{text-decoration:underline}
.chat-msg .ct hr{border:none;border-top:1px solid var(--border);margin:10px 0}
.chat-msg .ct table{border:1px solid var(--border);margin:8px 0}
.chat-msg .ct table th,.chat-msg .ct table td{border:1px solid var(--border);padding:4px 8px}

/* User messages stay plain */
.chat-msg.user .ct{white-space:pre-wrap}

/* --- Chat Input --- */
.chat-footer{padding:12px 16px;border-top:1px solid var(--border);background:var(--panel);flex-shrink:0}
.chat-input{display:flex;gap:8px}
.chat-input textarea{flex:1;background:var(--bg);color:var(--text);border:1px solid var(--border);
border-radius:8px;padding:10px 14px;font-family:inherit;font-size:13px;outline:none;
resize:none;min-height:44px;max-height:120px;line-height:1.4}
.chat-input textarea:focus{border-color:var(--cyan)}
.chat-input button{padding:10px 20px;background:var(--cyan);color:var(--bg);border:none;
border-radius:8px;cursor:pointer;font-family:inherit;font-size:12px;font-weight:bold;
min-width:70px;align-self:flex-end}
.chat-input button:hover{opacity:.85}
.chat-input button:disabled{opacity:.4;cursor:not-allowed}
.chat-input button.direct-btn{background:var(--purple)}
.chat-input button.project-btn{background:var(--orange)}
.typing-indicator{font-size:11px;color:var(--dim);padding:4px 0;display:none}
.typing-indicator.show{display:block}

/* --- M4: Project Progress Timeline --- */
.project-timeline{display:flex;flex-direction:column;gap:2px;margin-bottom:8px}
.phase-step{display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:4px;
font-size:11px;transition:background .2s}
.phase-step.active{background:#0c2d48}
.phase-step.completed{opacity:0.7}
.phase-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0;border:2px solid var(--border)}
.phase-dot.pending{border-color:var(--dim)}
.phase-dot.running{border-color:var(--blue);background:var(--blue);animation:p 1.5s infinite}
.phase-dot.completed{border-color:var(--green);background:var(--green)}
.phase-dot.failed{border-color:var(--red);background:var(--red)}
.phase-dot.awaiting{border-color:var(--yellow);background:var(--yellow);animation:p 1s infinite}
.phase-label{flex:1;color:var(--text)}
.phase-status{font-size:9px;color:var(--dim);text-transform:uppercase;letter-spacing:1px}

/* --- M4: TDD Step Bar --- */
.tdd-bar{display:flex;gap:1px;margin:8px 0;flex-wrap:wrap}
.tdd-step{padding:3px 6px;font-size:9px;border-radius:3px;background:#1e293b;color:var(--dim);
text-transform:uppercase;letter-spacing:0.5px;transition:all .3s}
.tdd-step.running{background:var(--blue);color:white}
.tdd-step.completed{background:#052e16;color:var(--green)}
.tdd-step.failed{background:#3b0000;color:var(--red)}

/* --- M4: Blueprint Modal --- */
.modal-overlay{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);
z-index:200;display:flex;align-items:center;justify-content:center}
.modal-box{background:var(--panel);border:1px solid var(--border);border-radius:8px;
width:80%;max-width:800px;max-height:80vh;display:flex;flex-direction:column;overflow:hidden}
.modal-box h2{padding:16px 20px;border-bottom:1px solid var(--border);font-size:14px;
color:var(--cyan);letter-spacing:1px;flex-shrink:0}
.modal-body{flex:1;overflow-y:auto;padding:20px;font-size:12px;line-height:1.6;scrollbar-width:thin}
.modal-body::-webkit-scrollbar{width:5px}
.modal-body::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
.modal-actions{display:flex;gap:8px;padding:12px 20px;border-top:1px solid var(--border);flex-shrink:0}
.modal-actions .btn{padding:8px 20px;font-size:12px}
.contract-section{margin-top:16px;padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:6px}
.contract-section h3{font-size:11px;color:var(--cyan);margin-bottom:8px;text-transform:uppercase;letter-spacing:1px}
.contract-section pre{font-size:11px;max-height:200px;overflow-y:auto;white-space:pre-wrap;word-break:break-all}

/* --- M4: UAT Panel --- */
.uat-panel{padding:12px;background:#1a1a2e;border:1px solid var(--yellow);border-radius:6px;margin-top:8px}
.uat-panel .uat-title{font-size:12px;color:var(--yellow);font-weight:bold;margin-bottom:8px}
.uat-panel .uat-info{font-size:11px;color:var(--dim);margin-bottom:10px}

/* --- M4: Launch Button --- */
.launch-btn{padding:4px 12px;border:1px solid var(--green);border-radius:4px;background:transparent;
color:var(--green);cursor:pointer;font-size:11px;font-family:inherit;transition:all .2s}
.launch-btn:hover{background:var(--green);color:var(--bg)}
.launch-btn:disabled{opacity:0.4;cursor:not-allowed}
.launch-btn.running{border-color:var(--blue);color:var(--blue);animation:p 1.5s infinite}
</style>
</head>
<body>
<div id="cs" class="cs d">Disconnected</div>

<!-- Header -->
<div class="hdr">
<h1>AUTONOMOUS FACTORY v1.5 <span id="localModeBadge" style="display:none;background:#eab308;color:#000;font-size:10px;padding:2px 8px;border-radius:10px;margin-left:8px;vertical-align:middle;font-weight:700;letter-spacing:1px">LOCAL MODE</span></h1>
<div class="m"><span id="pn">--</span> | <span id="lu">--</span></div>
</div>

<!-- Split Layout: Sidebar Left | Chat Right -->
<div class="split">

<!-- LEFT SIDEBAR: Status Panels -->
<div class="sidebar" id="sidebar">

<!-- Workers -->
<div class="pnl" id="pnl-workers">
<h2 onclick="togglePanel('pnl-workers')">Worker Status</h2>
<div class="pnl-body">
<table><thead><tr><th>Instance</th><th>Status</th><th>Task</th><th>Ctx</th><th>#</th></tr></thead>
<tbody id="wt"></tbody></table>
<div class="dbw">DB: Watchdog-only writes | bus -> Watchdog -> SQLite</div>
</div></div>

<!-- Role Config -->
<div class="pnl" id="pnl-roles">
<div class="role-header">
<h2 onclick="togglePanel('pnl-roles')">Role Configuration</h2>
<span id="saveNote" class="save-note">Saved</span>
</div>
<div class="pnl-body" id="rc">Loading roles...</div>
</div>

<!-- Tasks -->
<div class="pnl" id="pnl-tasks">
<h2 onclick="togglePanel('pnl-tasks')">Task Queue</h2>
<div class="pnl-body" id="tq"></div>
</div>

<!-- Escalations -->
<div class="pnl" id="pnl-esc">
<h2 onclick="togglePanel('pnl-esc')">Escalations</h2>
<div class="pnl-body" id="es"><span style="color:var(--dim)">None</span></div>
</div>

<!-- Activity -->
<div class="pnl" id="pnl-activity">
<h2 onclick="togglePanel('pnl-activity')">Recent Activity</h2>
<div class="pnl-body" id="al"></div>
</div>

<!-- M4: Project Progress -->
<div class="pnl" id="pnl-progress" style="display:none">
<h2 onclick="togglePanel('pnl-progress')">Project Progress</h2>
<div class="pnl-body">
<div class="project-timeline" id="projectTimeline"></div>
<div class="tdd-bar" id="tddBar"></div>
<div id="progressBugs"></div>
<div id="uatPanel" class="uat-panel" style="display:none">
<div class="uat-title">Proto Ready -- Awaiting UAT Approval</div>
<div id="e2eWarning" style="display:none;background:#ff4444;color:#fff;padding:10px 14px;border-radius:4px;margin-bottom:10px;font-weight:bold">⚠️ E2E TESTS FAILED — do not approve until failures are investigated!</div>
<div class="uat-info">All build phases complete. Test the proto deployment before approving production release.</div>
<button class="btn ap" onclick="approveUAT()" style="padding:8px 20px;font-size:12px">Approve &amp; Deploy to Production</button>
</div>
</div>
</div>

</div><!-- /sidebar -->

<!-- Resize Handle -->
<div class="resize-handle" id="resizeHandle"></div>

<!-- RIGHT: Chat Panel -->
<div class="main-chat">

<!-- Chat Toolbar -->
<div class="chat-header">
<div class="chat-toolbar">
<div class="mode-tabs">
<button class="mode-tab active" onclick="setMode('orchestrator')" id="tab-orchestrator">Orchestrator</button>
<button class="mode-tab" onclick="setMode('direct')" id="tab-direct">Direct</button>
<button class="mode-tab" onclick="setMode('project')" id="tab-project">Project</button>
<button class="mode-tab" onclick="setMode('discussion')" id="tab-discussion">Discussion</button>
</div>
<div class="worker-pick" id="workerPick" style="display:none">
<label>Model:</label>
<select id="workerSel"></select>
</div>
<span class="mode-badge orch" id="modeBadge">Orchestrator</span>
</div>
<div class="participant-panel" id="participantPanel">
<label>Participants:</label>
<label class="participant-chk you-chk" data-worker="you"><input type="checkbox" id="youToggle" onchange="toggleYou(this)">You</label>
<div id="participantList"></div>
</div>
</div>

<!-- Session Tabs -->
<div class="session-bar" id="sessionBar">
<button class="session-tab active">Chat 1</button>
<button class="session-new" onclick="newSession()">+ New</button>
</div>

<!-- Project Bar -->
<div class="project-bar" id="projectBar">
<label>Project:</label>
<select id="projectSel" onchange="selectProject()">
<option value="">-- No Project --</option>
</select>
<button class="proj-new-btn" onclick="createProjectModal()">+ New</button>
<button class="launch-btn" id="launchBtn" onclick="launchProject()" style="display:none">Launch</button>
</div>

<!-- Chat Messages -->
<div class="chat-log" id="chatLog"></div>

<!-- Chat Input -->
<div class="chat-footer">
<div class="typing-indicator" id="typing">Waiting for response...</div>
<div class="chat-input">
<textarea id="chatIn" placeholder="Talk to the Orchestrator..." rows="1"></textarea>
<button id="chatBtn" onclick="sendChat()">Send</button>
<button id="stopBtn" onclick="stopChat()" style="display:none;background:#e74c3c;color:#fff;border:none;padding:6px 16px;border-radius:6px;cursor:pointer;font-weight:600;">Stop</button>
</div>
</div>

</div><!-- /main-chat -->
</div><!-- /split -->

<script>
const RM={{REFRESH_MS}};
let ws=null, currentRoles=[], availWorkers=[];
let chatMode='orchestrator';
let workerList=[];
let currentProjectId=null;
let discussionInProgress=false;

// --- Collapsible Panels ---
function togglePanel(id){
document.getElementById(id).classList.toggle('collapsed');
}

// --- Resizable Sidebar ---
(function(){
const handle=document.getElementById('resizeHandle');
const sidebar=document.getElementById('sidebar');
let dragging=false,startX,startW;
handle.addEventListener('mousedown',e=>{
dragging=true;startX=e.clientX;startW=sidebar.offsetWidth;
handle.classList.add('active');
document.body.style.cursor='col-resize';
document.body.style.userSelect='none';
e.preventDefault();
});
document.addEventListener('mousemove',e=>{
if(!dragging)return;
const w=startW+(e.clientX-startX);
if(w>=200&&w<=600)sidebar.style.width=w+'px';
});
document.addEventListener('mouseup',()=>{
if(dragging){dragging=false;handle.classList.remove('active');
document.body.style.cursor='';document.body.style.userSelect='';}
});
})();

// --- Auto-resize Textarea ---
function autoResize(el){
el.style.height='auto';
el.style.height=Math.min(el.scrollHeight,120)+'px';
}

// --- Markdown Renderer ---
function renderMd(src){
if(!src)return '';
let html=src;
// Escape HTML first
html=html.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
// Code blocks: ```lang\n...\n```
html=html.replace(/```(\w*)\n([\s\S]*?)```/g,function(_,lang,code){
return '<pre><code class="lang-'+lang+'">'+code.trim()+'</code></pre>';
});
// Inline code
html=html.replace(/`([^`\n]+)`/g,'<code>$1</code>');
// Headers
html=html.replace(/^### (.+)$/gm,'<h3>$1</h3>');
html=html.replace(/^## (.+)$/gm,'<h2>$1</h2>');
html=html.replace(/^# (.+)$/gm,'<h1>$1</h1>');
// Bold
html=html.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
// Italic
html=html.replace(/\*(.+?)\*/g,'<em>$1</em>');
// Blockquote
html=html.replace(/^&gt; (.+)$/gm,'<blockquote>$1</blockquote>');
// Horizontal rule
html=html.replace(/^---$/gm,'<hr>');
// Unordered lists
html=html.replace(/^[\-\*] (.+)$/gm,'<li>$1</li>');
// Ordered lists
html=html.replace(/^\d+\. (.+)$/gm,'<li>$1</li>');
// Wrap consecutive <li> in <ul>
html=html.replace(/((?:<li>.*<\/li>\n?)+)/g,'<ul>$1</ul>');
// Links [text](url)
html=html.replace(/\[([^\]]+)\]\(([^)]+)\)/g,'<a href="$2" target="_blank">$1</a>');
// Paragraphs: double newline
html=html.replace(/\n\n+/g,'</p><p>');
// Single newlines inside paragraphs
html=html.replace(/\n/g,'<br>');
// Wrap in paragraph
html='<p>'+html+'</p>';
// Clean up empty paragraphs and paragraph-wrapped block elements
html=html.replace(/<p><\/p>/g,'');
html=html.replace(/<p>(<h[1-3]>)/g,'$1');
html=html.replace(/(<\/h[1-3]>)<\/p>/g,'$1');
html=html.replace(/<p>(<pre>)/g,'$1');
html=html.replace(/(<\/pre>)<\/p>/g,'$1');
html=html.replace(/<p>(<ul>)/g,'$1');
html=html.replace(/(<\/ul>)<\/p>/g,'$1');
html=html.replace(/<p>(<blockquote>)/g,'$1');
html=html.replace(/(<\/blockquote>)<\/p>/g,'$1');
html=html.replace(/<p>(<hr>)<\/p>/g,'$1');
html=html.replace(/<p><br>/g,'<p>');
html=html.replace(/<br><\/p>/g,'</p>');
return html;
}

function escHtml(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}

// --- WebSocket ---
function conn(){
const p=location.protocol==='https:'?'wss:':'ws:';
ws=new WebSocket(`${p}//${location.host}/ws`);
ws.onopen=()=>{document.getElementById('cs').className='cs c';document.getElementById('cs').textContent='Connected';loadChatHistory();loadWorkers();loadProjects();if(currentProjectId)loadProjectProgress();
fetch('/api/config/mode').then(r=>r.json()).then(d=>{const badge=document.getElementById('localModeBadge');if(badge)badge.style.display=d.local_mode?'':'none';}).catch(()=>{})};
ws.onclose=()=>{document.getElementById('cs').className='cs d';document.getElementById('cs').textContent='Disconnected';setTimeout(conn,3000)};
ws.onerror=()=>ws.close();
ws.onmessage=e=>{try{
const d=JSON.parse(e.data);
if(d.event==='role_swapped'){showSaveNote();return}
if(d.event==='mode_changed'){
const badge=document.getElementById('localModeBadge');
if(badge)badge.style.display=d.local_mode?'':'none';
appendChat('system',d.local_mode?'LOCAL TEST MODE enabled — all roles using local LLMs (DeepSeek/Qwen).':'Production mode restored — online AI models active.');
return}
if(d.event==='project_deleted'){if(d.project_id===currentProjectId){currentProjectId=null;}loadProjects();return}
if(d.event==='project_selected'){loadProjects();loadChatHistory();return}
if(d.event==='session_changed'){loadSessions();loadChatHistory();return}
if(d.event==='chat_message'){
if(d.mode!=='discussion'||d.role==='user')hideTyping();
appendChat(d.role,d.content,d.timestamp,d.worker,d.mode,d.elapsed_ms,d.project_id);
return}
if(d.event==='discussion_start'){
discussionInProgress=true;showTyping();
document.getElementById('chatBtn').disabled=false;
return}
if(d.event==='discussion_end'){
discussionInProgress=false;hideTyping();
document.getElementById('chatBtn').disabled=false;
return}
if(d.event==='chat_stopped'){hideTyping();return}
if(d.event==='chat_error'){hideTyping();appendChat('system',d.error);return}
// M4: Project execution events
if(d.event==='project_launched'){
appendChat('system','Project execution started. Phase 0: Blueprint generating...');
showProgressPanel();
renderTimeline(0,'running');
const btn=document.getElementById('launchBtn');
if(btn){btn.textContent='Running';btn.classList.add('running');btn.disabled=true;}
return}
if(d.event==='project_progress'){
projectProgress.phase=d.phase;projectProgress.step=d.step;
renderTimeline(d.phase,d.status);
return}
if(d.event==='tdd_step_update'){
projectProgress.phase=d.phase;projectProgress.step=d.step;
if(d.status==='completed')projectProgress.tddSteps[d.step.toUpperCase()]='completed';
else if(d.status==='failed')projectProgress.tddSteps[d.step.toUpperCase()]='failed';
renderTimeline(d.phase,'running');
renderTddBar(d.status==='running'?d.step.toUpperCase():null);
return}
if(d.event==='blueprint_ready'){
showBlueprintModal();
renderTimeline(0,'awaiting');
return}
if(d.event==='blueprint_approved'){
closeBlueprintModal();
appendChat('system','Blueprint approved. Contracts locked. Starting build phases...');
projectProgress.tddSteps={};
renderTddBar(null);
return}
if(d.event==='uat_ready'){
document.getElementById('uatPanel').style.display='';
renderTimeline(4,'awaiting');
// Issue 1: Show e2e_warning banner if E2E failed (R4/R10/M4)
var e2eWarningEl=document.getElementById('e2eWarning');
if(e2eWarningEl){e2eWarningEl.style.display=d.e2e_passed===false?'':'none';}
appendChat('system','Proto deployment ready. Awaiting UAT approval.'+(d.e2e_passed===false?' ⚠️ E2E TESTS FAILED — review before approving!':''));
return}
if(d.event==='uat_approved'){
document.getElementById('uatPanel').style.display='none';
renderTimeline(5,'completed');
const btn=document.getElementById('launchBtn');
btn.textContent='Completed';btn.classList.remove('running');btn.disabled=true;
appendChat('system','UAT approved. Production deployed. v1.0.0 tagged.');
return}
if(d.event==='project_error'){
appendChat('system','Project error: '+(d.error||'Unknown'));
const btn=document.getElementById('launchBtn');
btn.textContent='Launch';btn.classList.remove('running');btn.disabled=false;
return}
upd(d);
}catch(x){}}}

function upd(d){
document.getElementById('lu').textContent=new Date(d.timestamp).toLocaleTimeString();

// Workers
const tb=document.getElementById('wt');tb.innerHTML='';
(d.workers||[]).forEach(w=>{
const p=((w.context_usage_percent||0)*100).toFixed(0);
const c=p>85?'crit':p>70?'warn':'ok';
tb.innerHTML+=`<tr><td><span class="dot ${w.status||'offline'}"></span>${w.instance_name}</td>
<td>${w.status||'offline'}</td><td>${w.current_task_id||'--'}</td>
<td><div class="cb"><div class="cf ${c}" style="width:${p}%"></div></div> ${p}%</td>
<td>${w.tasks_completed_today||0}</td></tr>`});

// Roles
if(d.roles){currentRoles=d.roles;availWorkers=d.available_workers||[];renderRoles()}

// Tasks
const tq=document.getElementById('tq');tq.innerHTML='';
const s=d.task_stats||{};const tot=Object.values(s).reduce((a,b)=>a+b,0)||1;
['pending','in_progress','testing','review','blocked','approved'].forEach(k=>{
const n=s[k]||0;const pc=(n/tot*100).toFixed(0);
tq.innerHTML+=`<div class="tb"><span class="l">${k.replace('_',' ')}: ${n}</span>
<div class="b"><div class="f ${k}" style="width:${pc}%">${pc>10?n:''}</div></div></div>`});

// Escalations
const ed=document.getElementById('es');const el=d.escalations||[];
if(!el.length){ed.innerHTML='<span style="color:var(--dim)">None</span>'}
else{ed.innerHTML=el.map(e=>{
// M4: Special rendering for blueprint/UAT escalations
if(e.escalation_type==='blueprint_approval'){
return `<div class="ei"><div class="t" style="color:var(--cyan)">Blueprint Approval Required</div>
<div class="r">${e.escalation_reason||'Blueprint generated and audited.'}</div>
<div class="a"><button class="btn ap" onclick="showBlueprintModal()">Review Blueprint</button>
<button class="btn rj" onclick="res(${e.escalation_id},'dismissed')">Dismiss</button></div></div>`}
if(e.escalation_type==='uat_approval'){
return `<div class="ei"><div class="t" style="color:var(--yellow)">UAT Approval Required</div>
<div class="r">${e.escalation_reason||'Proto deployment ready for testing.'}</div>
<div class="a"><button class="btn ap" onclick="approveUAT()">Approve &amp; Deploy</button>
<button class="btn rj" onclick="res(${e.escalation_id},'dismissed')">Dismiss</button></div></div>`}
return `<div class="ei"><div class="t">${e.escalation_type}</div>
<div class="r">${e.escalation_reason}</div>
<div style="font-size:10px;color:var(--dim)">Task: ${e.task_id}</div>
<div class="a"><button class="btn ap" onclick="res(${e.escalation_id},'approved')">Approve</button>
<button class="btn rj" onclick="res(${e.escalation_id},'dismissed')">Dismiss</button></div></div>`
}).join('')}

// Activity
const ad=document.getElementById('al');
ad.innerHTML=(d.activity||[]).map(a=>`<div class="ai"><span class="ti">${a.timestamp?new Date(a.timestamp).toLocaleTimeString():'-'}</span>
<span class="mg">${a.actor||''} ${a.type||''}: ${a.detail||''}</span></div>`).join('')
||'<span style="color:var(--dim)">No activity</span>'}

function renderRoles(){
const rc=document.getElementById('rc');
rc.innerHTML=currentRoles.map(r=>{
const pOpts=availWorkers.map(w=>`<option value="${w}" ${w===r.primary?'selected':''}>${w}</option>`).join('');
const fOpts=`<option value="">none</option>`+availWorkers.map(w=>`<option value="${w}" ${w===r.fallback?'selected':''}>${w}</option>`).join('');
const active=r.active_worker;
const badge=active?`<span class="role-active">${active}</span>`:`<span class="role-inactive">unassigned</span>`;
return `<div class="role-row" id="role-${r.role}">
<span class="role-name">${r.role.replace(/_/g,' ')}</span>
${badge}
<span class="role-label">Primary:</span>
<select class="role-sel" id="p-${r.role}" onchange="markDirty('${r.role}')">${pOpts}</select>
<span class="role-label">Fallback:</span>
<select class="role-sel" id="f-${r.role}" onchange="markDirty('${r.role}')">${fOpts}</select>
<button class="swap-btn" id="sb-${r.role}" onclick="swapRole('${r.role}')">Apply</button>
</div>`}).join('')}

function markDirty(role){
const btn=document.getElementById('sb-'+role);
if(btn){btn.style.borderColor='var(--orange)';btn.style.color='var(--orange)';btn.textContent='Apply \u25cf'}
}

function swapRole(role){
const primary=document.getElementById('p-'+role).value;
const fallback=document.getElementById('f-'+role).value;
if(ws&&ws.readyState===1){
ws.send(JSON.stringify({action:'swap_role',role,primary,fallback:fallback||null}));
const btn=document.getElementById('sb-'+role);
if(btn){btn.classList.add('swap-ok');btn.textContent='Applied \u2713';btn.style.borderColor='var(--green)';btn.style.color='var(--green)';
setTimeout(()=>{btn.classList.remove('swap-ok');btn.style.borderColor='var(--purple)';btn.style.color='var(--purple)';btn.textContent='Apply'},2000)}
}else{
fetch('/api/roles/swap',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({role,primary,fallback:fallback||null})}).then(r=>r.json()).then(d=>{if(d.success)showSaveNote()})
}}

function showSaveNote(){
const n=document.getElementById('saveNote');n.classList.add('show');
setTimeout(()=>n.classList.remove('show'),3000)}

function res(id,dec){if(ws&&ws.readyState===1)ws.send(JSON.stringify({action:'resolve_escalation',escalation_id:id,decision:dec}))}

// --- Chat Mode ---

function setMode(mode){
chatMode=mode;
document.querySelectorAll('.mode-tab').forEach(t=>{t.classList.remove('active','direct-active','project-active','discussion-active')});
const tab=document.getElementById('tab-'+mode);
tab.classList.add('active');
if(mode==='direct')tab.classList.add('direct-active');
if(mode==='project')tab.classList.add('project-active');
if(mode==='discussion')tab.classList.add('discussion-active');

const wp=document.getElementById('workerPick');
const badge=document.getElementById('modeBadge');
const inp=document.getElementById('chatIn');
const btn=document.getElementById('chatBtn');
const pb=document.getElementById('projectBar');
const pp=document.getElementById('participantPanel');

btn.className='';
pp.classList.remove('show');
if(mode==='orchestrator'){
wp.style.display='none';
badge.className='mode-badge orch';badge.textContent='Orchestrator';
inp.placeholder='Talk to the Orchestrator...';
pb.classList.remove('dimmed');
}else if(mode==='direct'){
wp.style.display='flex';
badge.className='mode-badge direct';badge.textContent='Direct';
inp.placeholder='Send directly to selected model...';
btn.className='direct-btn';
pb.classList.add('dimmed');
}else if(mode==='discussion'){
wp.style.display='none';
pp.classList.add('show');
badge.className='mode-badge discussion';badge.textContent='Discussion';
inp.placeholder='Start a discussion among selected models...';
pb.classList.add('dimmed');
}else{
wp.style.display='flex';
badge.className='mode-badge project';badge.textContent='Project';
inp.placeholder='Ask about the project (context auto-included)...';
btn.className='project-btn';
pb.classList.remove('dimmed');
}
}

function loadWorkers(){
fetch('/api/workers/status').then(r=>r.json()).then(list=>{
workerList=list;
const sel=document.getElementById('workerSel');
sel.innerHTML=list.map(w=>{
const label=w.name+(w.model&&w.model!==w.name?' ('+w.model+')':'');
return `<option value="${w.name}">${label}</option>`;
}).join('');
// Populate discussion participant checkboxes
const pl=document.getElementById('participantList');
pl.innerHTML=list.map(w=>{
const label=w.name+(w.model&&w.model!==w.name?' ('+w.model+')':'');
return `<label class="participant-chk" data-worker="${w.name}"><input type="checkbox" value="${w.name}" onchange="toggleParticipant(this)">${label}</label>`;
}).join('');
}).catch(()=>{})}

function toggleParticipant(cb){
const lbl=cb.closest('.participant-chk');
if(cb.checked)lbl.classList.add('checked');
else lbl.classList.remove('checked');
// If discussion in progress, push updated participant list to backend
if(discussionInProgress&&ws&&ws.readyState===1){
ws.send(JSON.stringify({action:'discussion_update_participants',participants:getSelectedParticipants()}));
}
}

function toggleYou(cb){
const lbl=cb.closest('.participant-chk');
if(cb.checked)lbl.classList.add('checked');
else lbl.classList.remove('checked');
}

function getSelectedParticipants(){
const checks=document.querySelectorAll('#participantList input[type=checkbox]:checked');
return Array.from(checks).map(c=>c.value);
}

function isAutoLoop(){
// Auto-loop when "You" is NOT checked (models discuss among themselves)
return !document.getElementById('youToggle').checked;
}

// --- Chat Messages ---

function appendChat(role,content,ts,worker,mode,elapsed,projectId){
const log=document.getElementById('chatLog');
const d=document.createElement('div');
let cls='chat-msg '+role;
if(role==='assistant'&&mode==='direct')cls+=' direct';
if(role==='assistant'&&mode==='project')cls+=' project';
if(role==='assistant'&&mode==='discussion')cls+=' discussion';
d.className=cls;
if(mode==='discussion'&&worker)d.setAttribute('data-worker',worker);

const time=ts?new Date(ts).toLocaleTimeString():'';
let sender=role==='user'?'You':role==='system'?'System':'Orchestrator';
if(role==='assistant'&&worker&&worker!=='orchestrator')sender=worker;

let extra='';
if(role==='assistant'&&worker&&worker!=='orchestrator')extra+=`<div class="wk">via ${worker}</div>`;
if(elapsed)extra+=`<div class="el">${elapsed}ms</div>`;
if(projectId)extra+=`<div class="el">project: ${projectId}</div>`;

// Render markdown for assistant messages, plain text for user messages
const rendered=role==='user'?escHtml(content):renderMd(content);
d.innerHTML=`<div class="ts">${sender} ${time}</div><div class="ct">${rendered}</div>${extra}`;
log.appendChild(d);
log.scrollTop=log.scrollHeight;
}

function showTyping(){
document.getElementById('typing').classList.add('show');
document.getElementById('stopBtn').style.display='inline-block';
document.getElementById('chatBtn').style.display='none';
}
function hideTyping(){
document.getElementById('typing').classList.remove('show');
document.getElementById('stopBtn').style.display='none';
document.getElementById('chatBtn').style.display='inline-block';
}
function stopChat(){
if(ws&&ws.readyState===1){
ws.send(JSON.stringify({action:'chat_stop'}));
}else{
fetch('/api/chat/stop',{method:'POST'});
}
hideTyping();
appendChat('system','Chat stopped by user.');
document.getElementById('chatBtn').disabled=false;
}

function sendChat(){
const inp=document.getElementById('chatIn');
const msg=inp.value.trim();
if(!msg)return;
const btn=document.getElementById('chatBtn');
btn.disabled=true;
inp.value='';
inp.style.height='auto';
showTyping();

const worker=document.getElementById('workerSel').value;

if(chatMode==='discussion'){
// If discussion already in progress, cancel first then start new round
if(discussionInProgress&&ws&&ws.readyState===1){
ws.send(JSON.stringify({action:'discussion_cancel'}));
}
const participants=getSelectedParticipants();
if(!participants.length){
appendChat('system','Select at least one participant for discussion.');
hideTyping();btn.disabled=false;return;
}
if(ws&&ws.readyState===1){
ws.send(JSON.stringify({action:'discussion_chat',message:msg,participants:participants,auto_loop:isAutoLoop()}));
btn.disabled=false;
}else{
fetch('/api/chat/discussion',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({message:msg,participants:participants,auto_loop:isAutoLoop()})}).then(r=>r.json()).then(d=>{
if(d.error)appendChat('system',d.error);
hideTyping();btn.disabled=false;
}).catch(()=>{hideTyping();btn.disabled=false});
}
return;
}

if(ws&&ws.readyState===1){
if(chatMode==='orchestrator'){
ws.send(JSON.stringify({action:'chat_message',message:msg}));
}else if(chatMode==='direct'){
ws.send(JSON.stringify({action:'direct_chat',message:msg,worker:worker}));
}else{
ws.send(JSON.stringify({action:'project_chat',message:msg,worker:worker}));
}
btn.disabled=false;
}else{
const endpoint=chatMode==='orchestrator'?'/api/chat':chatMode==='direct'?'/api/chat/direct':'/api/chat/project';
const body=chatMode==='orchestrator'?{message:msg}:{message:msg,worker:worker};
fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify(body)}).then(r=>r.json()).then(d=>{
if(d.error)appendChat('system',d.error);
hideTyping();
btn.disabled=false;
}).catch(()=>{hideTyping();btn.disabled=false})
}}

// Textarea: Enter to send, Shift+Enter for newline
document.getElementById('chatIn').addEventListener('keydown',e=>{
if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendChat()}
});
document.getElementById('chatIn').addEventListener('input',function(){autoResize(this)});

function loadChatHistory(){
let url='/api/chat/history';
if(currentProjectId)url+='?project_id='+encodeURIComponent(currentProjectId);
fetch(url).then(r=>r.json()).then(hist=>{
if(Array.isArray(hist)){
const log=document.getElementById('chatLog');
log.innerHTML='';
hist.forEach(h=>{
const mode=(h.metadata||{}).mode||'orchestrator';
const worker=(h.metadata||{}).worker||null;
appendChat(h.role==='user'?'user':'assistant',h.content,h.timestamp,worker,mode);
});
}
}).catch(()=>{})}

// --- Chat Export ---
function downloadChat(fmt){window.location.href='/api/chat/download?format='+fmt}

// --- Chat Sessions ---

function loadSessions(){
fetch('/api/chat/sessions').then(r=>r.json()).then(sessions=>{
const bar=document.getElementById('sessionBar');
bar.innerHTML='';
(sessions||[]).forEach(s=>{
const btn=document.createElement('button');
btn.className='session-tab'+(s.is_active?' active':'');
btn.title=s.session_id+' ('+(s.message_count||0)+' msgs)';
btn.onclick=()=>switchSession(s.session_id);
btn.ondblclick=(e)=>{e.stopPropagation();renameSession(s.session_id,s.name)};
const label=document.createElement('span');
label.textContent=s.name||(s.session_id.substring(0,16));
btn.appendChild(label);
if(sessions.length>1){
const x=document.createElement('span');
x.className='session-close';
x.textContent='\u00d7';
x.onclick=(e)=>{e.stopPropagation();closeSession(s.session_id,s.name)};
btn.appendChild(x);}
bar.appendChild(btn);
});
const nb=document.createElement('button');
nb.className='session-new';
nb.textContent='+ New';
nb.onclick=newSession;
bar.appendChild(nb);
const dd=document.createElement('div');
dd.className='dl-dropdown';
dd.innerHTML='<button class="dl-dropdown-btn">Export \u25BE</button>'
+'<div class="dl-dropdown-content">'
+'<a onclick="downloadChat(\'json\')">JSON</a>'
+'<a onclick="downloadChat(\'md\')">Markdown</a></div>';
bar.appendChild(dd);
}).catch(()=>{})}

function newSession(){
const name=prompt('Session name (optional):');
if(name===null)return;
document.getElementById('chatLog').innerHTML='';
const body=name?{name:name}:{};
fetch('/api/chat/sessions/new',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify(body)}).then(r=>r.json()).then(()=>{
loadSessions();loadChatHistory();
}).catch(()=>{})}

function switchSession(id){
document.getElementById('chatLog').innerHTML='';
if(ws&&ws.readyState===1){
ws.send(JSON.stringify({action:'switch_session',session_id:id}));
}else{
fetch('/api/chat/sessions/switch',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({session_id:id})}).then(r=>r.json()).then(()=>{
loadSessions();loadChatHistory();
}).catch(()=>{})
}}

function renameSession(id,current){
const name=prompt('Rename session:',current||'');
if(!name)return;
fetch('/api/chat/sessions/rename',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({session_id:id,name:name})}).then(r=>r.json()).then(()=>{
loadSessions();
}).catch(()=>{})}

function closeSession(id,name){
if(!confirm('Close "'+( name||id)+'"? Messages are kept in archive.'))return;
fetch('/api/chat/sessions/close',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({session_id:id})}).then(r=>r.json()).then(()=>{
loadSessions();loadChatHistory();
}).catch(()=>{})}

function loadProjects(){
fetch('/api/projects').then(r=>r.json()).then(d=>{
const sel=document.getElementById('projectSel');
const prev=currentProjectId;
currentProjectId=d.current_project||null;
sel.innerHTML='<option value="">-- No Project --</option>';
let selectedProject=null;
(d.projects||[]).forEach(p=>{
const opt=document.createElement('option');
opt.value=p.project_id;
opt.textContent=p.name+' ['+p.status+']';
if(p.project_id===currentProjectId){opt.selected=true;selectedProject=p}
sel.appendChild(opt);
});
// M4: Show/hide launch button based on project state
const btn=document.getElementById('launchBtn');
if(selectedProject&&selectedProject.status==='active'){
btn.style.display='';
if(currentProjectId in (window._runningProjects||{})){
btn.textContent='Running';btn.classList.add('running');btn.disabled=true;
}else{
btn.textContent='Launch';btn.classList.remove('running');btn.disabled=false;
}
loadProjectProgress();
}else{
btn.style.display='none';
document.getElementById('pnl-progress').style.display='none';
}
}).catch(()=>{})}

function selectProject(){
const pid=document.getElementById('projectSel').value||null;
currentProjectId=pid;
// Reset progress state on project change
projectProgress={phase:0,step:'',tddSteps:{}};
document.getElementById('uatPanel').style.display='none';
if(ws&&ws.readyState===1){
ws.send(JSON.stringify({action:'select_project',project_id:pid}));
}else{
fetch('/api/projects/select',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({project_id:pid})}).then(r=>r.json()).then(d=>{
currentProjectId=d.selected||null;
loadChatHistory();
}).catch(()=>{})
}
loadProjects();
}

function createProjectModal(){
document.getElementById('newProjectModal').style.display='flex';
document.getElementById('newProjName').value='';
document.getElementById('newProjDesc').value='';
setTimeout(()=>document.getElementById('newProjName').focus(),100);
}
function closeNewProjectModal(){
document.getElementById('newProjectModal').style.display='none';
}
function submitNewProject(){
const name=document.getElementById('newProjName').value.trim();
if(!name){alert('Project name required');return}
const desc=document.getElementById('newProjDesc').value.trim();
const btn=document.getElementById('newProjSubmit');
btn.disabled=true;btn.textContent='Creating...';
fetch('/api/projects/create',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({name:name,description:desc})}).then(r=>r.json()).then(d=>{
btn.disabled=false;btn.textContent='Create Project';
if(d.error){alert('Error: '+d.error);return}
currentProjectId=d.project_id;
closeNewProjectModal();
loadProjects();
loadChatHistory();
}).catch(e=>{btn.disabled=false;btn.textContent='Create Project';alert('Failed: '+e)})}
// Allow Enter key in name field to submit
document.addEventListener('DOMContentLoaded',()=>{
const nf=document.getElementById('newProjName');
if(nf)nf.addEventListener('keydown',(e)=>{if(e.key==='Enter')submitNewProject()});
});

// ─── M4: Project Execution ───

const PHASE_NAMES=['Blueprint','Build Phase 1','Build Phase 2','Build Phase 3','Proto Deploy','Production'];
const TDD_STEPS=['AC','RED','GREEN','BC','BF','SEA','DS','OA','VB','GIT','CL','CCP','AD'];
let projectProgress={phase:0,step:'',tddSteps:{}};

function launchProject(){
if(!currentProjectId){appendChat('system','Select a project first.');return}
const btn=document.getElementById('launchBtn');
btn.disabled=true;btn.textContent='Launching...';
if(ws&&ws.readyState===1){
ws.send(JSON.stringify({action:'launch_project',project_id:currentProjectId}));
btn.textContent='Running';btn.classList.add('running');
showProgressPanel();
}else{
fetch('/api/projects/launch',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({project_id:currentProjectId})}).then(r=>r.json()).then(d=>{
if(d.error){alert(d.error);btn.disabled=false;btn.textContent='Launch';return}
btn.textContent='Running';btn.classList.add('running');
showProgressPanel();
}).catch(e=>{alert('Launch failed: '+e);btn.disabled=false;btn.textContent='Launch'})
}}

function showProgressPanel(){
document.getElementById('pnl-progress').style.display='';
renderTimeline(0,'pending');
renderTddBar(null);
}

function renderTimeline(currentPhase,phaseStatus){
const el=document.getElementById('projectTimeline');
el.innerHTML=PHASE_NAMES.map((name,i)=>{
let dotCls='pending',statusTxt='pending';
if(i<currentPhase){dotCls='completed';statusTxt='done'}
else if(i===currentPhase){
if(phaseStatus==='running'){dotCls='running';statusTxt='running'}
else if(phaseStatus==='completed'){dotCls='completed';statusTxt='done'}
else if(phaseStatus==='failed'){dotCls='failed';statusTxt='failed'}
else if(phaseStatus==='awaiting'){dotCls='awaiting';statusTxt='awaiting approval'}
else{dotCls='running';statusTxt='running'}
}
const activeCls=i===currentPhase?' active':'';
return `<div class="phase-step${activeCls}"><span class="phase-dot ${dotCls}"></span><span class="phase-label">${name}</span><span class="phase-status">${statusTxt}</span></div>`
}).join('')
}

function renderTddBar(activeStep){
const el=document.getElementById('tddBar');
el.innerHTML=TDD_STEPS.map(s=>{
const st=projectProgress.tddSteps[s]||'pending';
let cls='tdd-step';
if(s===activeStep)cls+=' running';
else if(st==='completed')cls+=' completed';
else if(st==='failed')cls+=' failed';
return `<span class="${cls}">${s}</span>`
}).join('')
}

function loadProjectProgress(){
if(!currentProjectId)return;
fetch('/api/projects/'+encodeURIComponent(currentProjectId)+'/progress').then(r=>r.json()).then(d=>{
if(d.error)return;
const phase=d.current_phase||0;
const status=d.status||'active';
renderTimeline(phase,d.is_running?'running':status==='active'?'pending':'completed');
// Update launch button
const btn=document.getElementById('launchBtn');
if(d.is_running){
btn.textContent='Running';btn.classList.add('running');btn.disabled=true;
document.getElementById('pnl-progress').style.display='';
}
// Show UAT panel if at phase 4
if(phase>=4&&status==='active'){
document.getElementById('uatPanel').style.display='';
}
}).catch(()=>{})}

function showBlueprintModal(){
if(!currentProjectId)return;
document.getElementById('blueprintModal').style.display='flex';
document.getElementById('blueprintBody').innerHTML='<span style="color:var(--dim)">Loading...</span>';
fetch('/api/projects/'+encodeURIComponent(currentProjectId)+'/blueprint').then(r=>r.json()).then(d=>{
let html='';
if(d.blueprint&&d.blueprint.content){
html+='<div class="ct">'+renderMd(d.blueprint.content)+'</div>';
if(d.blueprint.approved_by){
html+='<div style="margin-top:12px;color:var(--green);font-size:11px">Approved by: '+escHtml(d.blueprint.approved_by)+' at '+escHtml(d.blueprint.approved_at||'')+'</div>';
}
}else{
html='<span style="color:var(--dim)">No blueprint generated yet.</span>';
}
if(d.contracts&&Object.keys(d.contracts).length){
Object.entries(d.contracts).forEach(([fname,content])=>{
html+=`<div class="contract-section"><h3>${escHtml(fname)}</h3><pre>${escHtml(content)}</pre></div>`;
});
}
document.getElementById('blueprintBody').innerHTML=html;
}).catch(e=>{document.getElementById('blueprintBody').innerHTML='Error: '+escHtml(String(e))})}

function closeBlueprintModal(){document.getElementById('blueprintModal').style.display='none'}

function approveBlueprint(){
if(!currentProjectId)return;
if(!confirm('Approve blueprint and lock contracts? This cannot be undone for this phase.'))return;
if(ws&&ws.readyState===1){
ws.send(JSON.stringify({action:'approve_blueprint',project_id:currentProjectId}));
}else{
fetch('/api/projects/'+encodeURIComponent(currentProjectId)+'/approve-blueprint',{method:'POST',
headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(d=>{
if(d.error)alert(d.error);
}).catch(e=>alert('Failed: '+e))
}
closeBlueprintModal();
}

function approveUAT(){
if(!currentProjectId)return;
if(!confirm('Approve UAT and deploy to production? This will merge to main and tag v1.0.0.'))return;
if(ws&&ws.readyState===1){
ws.send(JSON.stringify({action:'approve_uat',project_id:currentProjectId}));
}else{
fetch('/api/projects/'+encodeURIComponent(currentProjectId)+'/approve-uat',{method:'POST',
headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(d=>{
if(d.error)alert(d.error);
}).catch(e=>alert('Failed: '+e))
}
document.getElementById('uatPanel').style.display='none';
}

conn();setInterval(()=>{if(!ws||ws.readyState!==1){fetch('/api/status').then(r=>r.json()).then(upd).catch(()=>{})}},5000);
</script>

<!-- New Project Modal -->
<div class="modal-overlay" id="newProjectModal" style="display:none" onclick="if(event.target===this)closeNewProjectModal()">
<div class="modal-box" style="max-width:480px">
<h2>New Project</h2>
<div class="modal-body" style="padding:16px 0">
<div style="margin-bottom:12px">
<label style="font-size:11px;color:var(--dim);display:block;margin-bottom:4px">PROJECT NAME <span style="color:var(--red)">*</span></label>
<input id="newProjName" type="text" placeholder="e.g. Personal Library Manager" style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:4px;font-family:inherit;font-size:13px">
</div>
<div>
<label style="font-size:11px;color:var(--dim);display:block;margin-bottom:4px">DESCRIPTION</label>
<textarea id="newProjDesc" rows="3" placeholder="What should this project do?" style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:4px;font-family:inherit;font-size:13px;resize:vertical"></textarea>
</div>
</div>
<div class="modal-actions">
<button id="newProjSubmit" class="btn ap" onclick="submitNewProject()" style="padding:8px 20px;font-size:12px">Create Project</button>
<button class="btn" onclick="closeNewProjectModal()" style="padding:8px 20px;font-size:12px">Cancel</button>
</div>
</div>
</div>

<!-- M4: Blueprint Approval Modal -->
<div class="modal-overlay" id="blueprintModal" style="display:none" onclick="if(event.target===this)closeBlueprintModal()">
<div class="modal-box">
<h2>Blueprint Approval</h2>
<div class="modal-body" id="blueprintBody">Loading...</div>
<div class="modal-actions">
<button class="btn ap" onclick="approveBlueprint()" style="padding:8px 20px;font-size:12px">Approve &amp; Lock Contracts</button>
<button class="btn" onclick="closeBlueprintModal()" style="padding:8px 20px;font-size:12px">Close</button>
</div>
</div>
</div>

</body></html>"""
