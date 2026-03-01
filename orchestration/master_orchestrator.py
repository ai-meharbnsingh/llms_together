"""
Master Orchestrator (COO) — v1.1 Role-Based
═══════════════════════════════════════════════════════
Persistent in-memory object. Maintains chat history for the session.
Uses RoleRouter for all worker access. NO hardcoded worker names.
READS from ReadOnlyDB. ALL WRITES go through message bus → Watchdog.
═══════════════════════════════════════════════════════
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from orchestration.database import ReadOnlyDB
from orchestration.role_router import RoleRouter
from workers.adapters import WorkerAdapter

logger = logging.getLogger("factory.orchestrator")


class MasterOrchestrator:
    """
    COO — coordinates factory operations.
    Persistent in-memory object: stays alive for the entire session.
    Workers spawn/die per request, but the Orchestrator persists.
    Uses RoleRouter to resolve which worker handles each role.
    Uses ReadOnlyDB for queries. Writes via message bus.
    """

    def __init__(self, read_db: ReadOnlyDB, role_router: RoleRouter,
                 config: dict, working_dir: str):
        self.db = read_db
        self.db.set_requester("orchestrator")
        self.router = role_router
        self.config = config
        self.working_dir = Path(working_dir).expanduser()
        self.current_project = None

        # Chat history — persisted to disk so it survives restarts
        state_dir = config.get("factory", {}).get("factory_state_dir")
        if state_dir:
            self._history_file = Path(state_dir) / "chat_history.json"
            self._sessions_file = Path(state_dir) / "chat_sessions.json"
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            self._history_file = None
            self._sessions_file = None
        self.chat_history: List[dict] = self._load_chat_history()

        # Phi3 scribe — set by main.py after Phi3Manager starts
        self.phi3 = None

        # Session ID for tracking
        self.session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Session management — multiple chat sessions (tabs)
        self._session_meta: List[dict] = self._load_session_meta()
        self._ensure_current_session()

        # Discussion mode — cancellation event + live participant list
        self._discussion_cancel = asyncio.Event()
        self._discussion_participants: List[str] = []

        # Document of Context — loaded from DB on recovery
        self._doc_context = None
        self._load_doc_context()

    def _get_worker(self, role: str) -> Optional[WorkerAdapter]:
        """Get worker for role via router. Logs if unavailable."""
        w = self.router.get_worker(role)
        if not w:
            logger.error(f"No worker assigned to role '{role}'. "
                         f"Check dashboard Role Config or factory_config.json")
        return w

    def _get_worker_name(self, role: str) -> str:
        return self.router.get_worker_name(role) or "unknown"

    # ─── Chat History (file-backed) ───

    def _load_chat_history(self) -> List[dict]:
        """Load chat history from disk. Returns empty list if none exists."""
        if not self._history_file:
            return []
        try:
            if self._history_file.exists():
                data = json.loads(self._history_file.read_text())
                if isinstance(data, list):
                    logger.info(f"Chat history loaded: {len(data)} messages from {self._history_file}")
                    return data
        except Exception as e:
            logger.warning(f"Failed to load chat history (starting fresh): {e}")
        return []

    def _save_chat_history(self):
        """Persist chat history to disk. Flushes overflow to cold storage, keeps last 200."""
        if not self._history_file:
            return
        try:
            if len(self.chat_history) > 200:
                overflow = self.chat_history[:-200]
                self._flush_to_cold_storage(overflow)
                self.chat_history = self.chat_history[-200:]
            self._history_file.write_text(
                json.dumps(self.chat_history, default=str, ensure_ascii=False))
            self._update_current_session_count()
            self._save_session_meta()
        except Exception as e:
            logger.warning(f"Failed to save chat history: {e}")

    # ─── Session Management ───

    def _load_session_meta(self) -> List[dict]:
        """Load session metadata from disk."""
        if not self._sessions_file:
            return []
        try:
            if self._sessions_file.exists():
                data = json.loads(self._sessions_file.read_text())
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.warning(f"Failed to load session meta: {e}")
        return []

    def _save_session_meta(self):
        """Persist session metadata to disk."""
        if not self._sessions_file:
            return
        try:
            self._sessions_file.write_text(
                json.dumps(self._session_meta, default=str, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"Failed to save session meta: {e}")

    def _ensure_current_session(self):
        """Ensure the current session_id is tracked in session metadata.
        On restart: flush leftover chat_history to the previous session's cold storage,
        then start the new session clean."""
        for s in self._session_meta:
            if s["session_id"] == self.session_id:
                return

        # If we have old sessions and leftover history from a previous boot,
        # archive those messages under the previous session's ID
        if self._session_meta and self.chat_history:
            prev = self._session_meta[-1]  # most recent previous session
            # Tag messages with the old session_id before flushing
            for msg in self.chat_history:
                meta = msg.get("metadata") or {}
                if "session_id" not in meta or meta["session_id"] != prev["session_id"]:
                    meta["session_id"] = prev["session_id"]
                    msg["metadata"] = meta
            self._flush_to_cold_storage(self.chat_history)
            prev["message_count"] = prev.get("message_count", 0) + len(self.chat_history)
            self.chat_history = []
            if self._history_file:
                self._history_file.write_text("[]")
            logger.info(f"Archived {prev['message_count']} msgs from previous session '{prev['name']}'")

        self._session_meta.append({
            "session_id": self.session_id,
            "name": f"Chat {len(self._session_meta) + 1}",
            "created_at": datetime.now().isoformat(),
            "message_count": 0,
        })
        self._save_session_meta()

    def _update_current_session_count(self):
        """Update message_count for the active session."""
        for s in self._session_meta:
            if s["session_id"] == self.session_id:
                s["message_count"] = len(self.chat_history)
                break

    def new_chat_session(self, name: str = None) -> dict:
        """Create a new chat session. Flushes current history to cold storage."""
        # Flush current session to cold storage
        if self.chat_history:
            self._flush_to_cold_storage(self.chat_history)
            self._update_current_session_count()

        # Generate new session
        new_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
        session_name = name or f"Chat {len(self._session_meta) + 1}"

        self._session_meta.append({
            "session_id": new_id,
            "name": session_name,
            "created_at": datetime.now().isoformat(),
            "message_count": 0,
        })

        # Clear warm state
        self.chat_history = []
        self.session_id = new_id
        self._save_chat_history()
        self._save_session_meta()

        logger.info(f"New chat session: {new_id} ({session_name})")
        return {"session_id": new_id, "name": session_name}

    def switch_chat_session(self, session_id: str) -> dict:
        """Switch to a different chat session. Flushes current, loads target from archive."""
        if session_id == self.session_id:
            return {
                "session_id": self.session_id,
                "name": self._get_session_name(self.session_id),
                "message_count": len(self.chat_history),
                "status": "already_active",
            }

        # Verify target exists in metadata
        target = None
        for s in self._session_meta:
            if s["session_id"] == session_id:
                target = s
                break
        if not target:
            return {"error": f"Session '{session_id}' not found"}

        # Flush current session to cold storage
        if self.chat_history:
            self._flush_to_cold_storage(self.chat_history)
            self._update_current_session_count()

        # Load target session from cold archive
        loaded = self.db.get_session_messages(session_id)
        self.chat_history = loaded
        self.session_id = session_id
        self._save_chat_history()
        self._save_session_meta()

        logger.info(f"Switched to session: {session_id} ({target['name']}, {len(loaded)} msgs)")
        return {
            "session_id": session_id,
            "name": target["name"],
            "message_count": len(loaded),
        }

    def list_chat_sessions(self) -> List[dict]:
        """Return session metadata with is_active flag."""
        result = []
        for s in self._session_meta:
            entry = dict(s)
            entry["is_active"] = (s["session_id"] == self.session_id)
            if entry["is_active"]:
                entry["message_count"] = len(self.chat_history)
            result.append(entry)
        return result

    def rename_chat_session(self, session_id: str, name: str) -> dict:
        """Rename a chat session."""
        for s in self._session_meta:
            if s["session_id"] == session_id:
                s["name"] = name
                self._save_session_meta()
                return {"session_id": session_id, "name": name}
        return {"error": f"Session '{session_id}' not found"}

    def close_chat_session(self, session_id: str) -> dict:
        """Close/remove a session tab. Cannot close the active session if it's the only one."""
        # Find the session
        target = None
        for s in self._session_meta:
            if s["session_id"] == session_id:
                target = s
                break
        if not target:
            return {"error": f"Session '{session_id}' not found"}

        # Don't allow closing the last session
        if len(self._session_meta) <= 1:
            return {"error": "Cannot close the only session"}

        # If closing the active session, switch to another first
        if session_id == self.session_id:
            # Pick the nearest session that isn't this one
            others = [s for s in self._session_meta if s["session_id"] != session_id]
            self.switch_chat_session(others[0]["session_id"])

        # Remove from metadata (cold storage messages are preserved)
        self._session_meta = [s for s in self._session_meta if s["session_id"] != session_id]
        self._save_session_meta()
        logger.info(f"Closed session: {session_id} ({target['name']})")
        return {"closed": session_id, "name": target["name"]}

    def _get_session_name(self, session_id: str) -> str:
        for s in self._session_meta:
            if s["session_id"] == session_id:
                return s["name"]
        return "Unknown"

    def _flush_to_cold_storage(self, messages: list):
        """Queue overflow messages to chat_archive via message bus."""
        if not messages:
            return
        for msg in messages:
            meta = msg.get("metadata") or {}
            self.db.request_write("insert", "chat_archive", {
                "session_id": meta.get("session_id", self.session_id),
                "role": msg.get("role", "unknown"),
                "content": msg.get("content", ""),
                "mode": meta.get("mode"),
                "worker": meta.get("worker"),
                "project_id": meta.get("project_id"),
                "metadata": json.dumps(meta) if meta else None,
                "original_timestamp": msg.get("timestamp", datetime.now().isoformat()),
            })
        logger.info(f"Flushed {len(messages)} messages to cold storage")

    def _append_history(self, role: str, content: str, metadata: dict = None):
        """Append to chat history and persist to disk."""
        entry = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }
        if metadata is None:
            metadata = {}
        metadata["session_id"] = self.session_id
        entry["metadata"] = metadata
        self.chat_history.append(entry)
        self._save_chat_history()

    # ─── DoC Recovery ───

    def _load_doc_context(self):
        """
        On init, check for existing DoC in context_summaries.
        If found, prepend as system context in chat_history.
        Enables crash recovery without raw chat replay.
        """
        try:
            doc = self.db.get_doc("phi3-orchestrator")
            if doc and doc.get("summary_text"):
                self._doc_context = doc["summary_text"]
                self._append_history("system", f"[RECOVERED CONTEXT]\n{self._doc_context}",
                                     metadata={
                                         "type": "doc_recovery",
                                         "doc_token_count": doc.get("token_count"),
                                         "doc_created_at": str(doc.get("created_at", "")),
                                         "chats_covered": len(doc.get("original_chat_ids_parsed", [])),
                                     })
                logger.info(f"DoC loaded: {doc.get('token_count', '?')} tokens, "
                            f"covering {len(doc.get('original_chat_ids_parsed', []))} chats")
            else:
                logger.info("No existing DoC found — fresh session")
        except Exception as e:
            logger.warning(f"DoC recovery failed (non-fatal): {e}")

    # ─── Main Entry Point ───

    async def handle_message(self, user_msg: str, session_id: str = None) -> dict:
        """
        Main entry point from Dashboard.
        Appends to chat_history, routes to appropriate method, queues Phi3 summary.
        """
        sid = session_id or self.session_id
        self._append_history("user", user_msg)

        # Route based on intent (simple keyword routing for now)
        response = await self._route_intent(user_msg, sid)

        # Append response to history
        resp_text = response.get("response", response.get("error", str(response)))
        self._append_history("assistant", resp_text, metadata={
            "handler": response.get("_handler", "unknown"),
            "worker": response.get("_worker"),
        })

        # Queue summary to paired Phi3 scribe (persist_full for DoC + full chat)
        if self.phi3 and resp_text:
            await self.phi3.queue_summary(
                user_query=user_msg,
                llm_response=resp_text,
                session_id=sid,
                project_id=self.current_project,
                persist_full=True,
            )

        return response

    async def _route_intent(self, msg: str, session_id: str) -> dict:
        """Simple intent classification — expandable later."""
        lower = msg.lower().strip()

        if lower.startswith("create project"):
            parts = msg.split(maxsplit=2)
            name = parts[2] if len(parts) > 2 else "untitled"
            return await self.create_project(name, msg)

        if "blueprint" in lower and ("generate" in lower or "create" in lower):
            return await self.generate_blueprint(msg)

        if "plan" in lower and ("tasks" in lower or "phase" in lower):
            return await self.plan_tasks_gsd(1, msg)

        # Default: echo back with context for now
        return {
            "response": f"Received: {msg}",
            "_handler": "default",
            "history_length": len(self.chat_history),
        }

    # ─── Conversation Context Builder ───

    def _build_conversation_context(self, mode: str, worker_name: str,
                                    max_turns: int = 20) -> str:
        """
        Build conversation history string for a specific mode+worker.
        Fuses DoC (long-term context) with recent history (warm memory).

        When history is sparse (< 5 relevant messages), prepend DoC as
        "LONG-TERM CONTEXT" using 1/4 of token budget. This ensures models
        retain context across restarts and after cold flushes.

        When history is rich (>= 5 messages), skip DoC — recent history
        provides enough context.
        """
        # Get model's max context tokens, use 1/3 for history
        worker = self.router.workers.get(worker_name)
        max_ctx = 32000  # safe default
        if worker:
            max_ctx = worker.config.get("max_context_tokens", 32000)
        token_budget = max_ctx // 3  # reserve 2/3 for new message + response

        relevant = []
        for entry in self.chat_history:
            meta = entry.get("metadata") or {}
            # Skip doc_recovery system entries — they're metadata, not conversation
            if meta.get("type") == "doc_recovery":
                continue
            if meta.get("mode") != mode:
                continue
            # Discussion: show all participants (don't filter by single worker)
            if mode in ("direct", "project") and meta.get("worker") != worker_name:
                continue
            # Filter by current project in project mode
            if mode == "project" and self.current_project:
                if meta.get("project_id") != self.current_project:
                    continue
            relevant.append(entry)

        # Determine if DoC prefix is needed — only for orchestrator mode.
        # Direct chat is raw (no context injection), project chat uses project context instead.
        use_doc = mode == "orchestrator" and len(relevant) < 5 and self._doc_context

        if use_doc:
            doc_char_limit = (token_budget // 4) * 4  # 1/4 budget for DoC
            history_char_limit = (token_budget * 3 // 4) * 4  # 3/4 for history
        else:
            doc_char_limit = 0
            history_char_limit = token_budget * 4  # full budget for history

        # Keep only last N turns
        relevant = relevant[-max_turns:]

        # Build history from newest to oldest, stop when budget exceeded
        lines = []
        chars_used = 0
        for entry in reversed(relevant):
            role = entry["role"]
            content = entry["content"]
            meta = entry.get("metadata") or {}
            if role == "user":
                line = f"User: {content}"
            elif mode == "discussion" and meta.get("worker"):
                line = f"{meta['worker']}: {content}"
            else:
                line = f"Assistant: {content}"
            if chars_used + len(line) > history_char_limit:
                break
            lines.append(line)
            chars_used += len(line)

        lines.reverse()
        history_text = "\n\n".join(lines)

        if use_doc:
            doc_text = self._doc_context[:doc_char_limit]
            return (
                "=== LONG-TERM CONTEXT (from previous sessions) ===\n"
                + doc_text
                + "\n=== END LONG-TERM CONTEXT ===\n\n"
                + history_text
            )

        return history_text

    # ─── Direct Chat (bypass role router) ───

    async def direct_chat(self, worker_name: str, message: str,
                          system_prompt: str = None) -> dict:
        """
        Send message directly to a named worker, bypassing role router.
        Includes conversation history for continuity.
        Used by Dashboard "Direct Chat" mode.
        """
        worker = self.router.workers.get(worker_name)
        if not worker:
            return {"error": f"Worker '{worker_name}' not found",
                    "available": list(self.router.workers.keys()),
                    "_handler": "direct_chat"}

        self._append_history("user", message, metadata={
            "mode": "direct", "worker": worker_name})

        # Build conversation context from previous direct chats with this worker
        history = self._build_conversation_context("direct", worker_name)
        if history:
            full_prompt = (
                "Previous conversation:\n"
                + history
                + "\n\nUser: " + message
                + "\n\nContinue the conversation naturally. "
                "Remember what was discussed above."
            )
        else:
            full_prompt = message

        try:
            self.db.request_write("update", "dashboard_state",
                                  {"_where": {"instance_name": worker_name},
                                   "status": "working"})
            result = await worker.send_message(
                full_prompt,
                system_prompt=system_prompt or (
                    "You are a helpful AI assistant. Maintain context from the "
                    "conversation history provided. Reference earlier messages "
                    "when relevant."
                ),
            )
        except Exception as e:
            logger.error(f"Direct chat to {worker_name} failed: {e}")
            return {"error": str(e), "_handler": "direct_chat"}
        finally:
            self.db.request_write("update", "dashboard_state",
                                  {"_where": {"instance_name": worker_name},
                                   "status": "idle"})

        if not result.get("success"):
            err = result.get("error", "Unknown error")
            self._append_history("assistant", f"[{worker_name}] Error: {err}",
                                 metadata={"mode": "direct", "worker": worker_name})
            return {"error": err, "worker": worker_name, "_handler": "direct_chat"}

        resp_text = result["response"]
        self._append_history("assistant", resp_text, metadata={
            "mode": "direct", "worker": worker_name,
            "elapsed_ms": result.get("elapsed_ms"),
        })

        if self.phi3:
            await self.phi3.queue_summary(
                user_query=message,
                llm_response=resp_text,
                session_id=self.session_id,
                project_id=self.current_project,
                persist_full=True,
            )

        return {
            "response": resp_text,
            "worker": worker_name,
            "elapsed_ms": result.get("elapsed_ms"),
            "_handler": "direct_chat",
        }

    # ─── Project Chat (context-aware) ───

    async def project_chat(self, worker_name: str, message: str) -> dict:
        """
        Send message to a named worker with project context prepended.
        Includes: project info, blueprint, current phase tasks.
        """
        worker = self.router.workers.get(worker_name)
        if not worker:
            return {"error": f"Worker '{worker_name}' not found",
                    "available": list(self.router.workers.keys()),
                    "_handler": "project_chat"}

        # Build project context
        context_parts = []
        project = None
        if self.current_project:
            project = self.db.get_project(self.current_project)
        if not project:
            project = self.db.get_active_project()
        if project:
            self.current_project = project["project_id"]
            context_parts.append(
                f"PROJECT: {project['name']}\n"
                f"Status: {project.get('status', 'unknown')}\n"
                f"Phase: {project.get('current_phase', 0)}\n"
                f"Description: {project.get('description', 'N/A')}"
            )
            bp = self.db.get_latest_blueprint(project["project_id"])
            if bp and bp.get("blueprint_content"):
                context_parts.append(
                    f"BLUEPRINT v{bp.get('version', '?')}:\n"
                    f"{bp['blueprint_content']}"
                )
            phase = project.get("current_phase", 0)
            if phase:
                tasks = self.db.get_tasks_by_phase(project["project_id"], phase)
                if tasks:
                    task_lines = "\n".join(
                        f"  - [{t.get('status','?')}] {t.get('task_id','')}: "
                        f"{t.get('description','')}"
                        for t in tasks
                    )
                    context_parts.append(f"TASKS (Phase {phase}):\n{task_lines}")

        # Build conversation history for this worker in project mode
        conv_history = self._build_conversation_context("project", worker_name)

        prompt_parts = []
        if context_parts:
            prompt_parts.append(
                "=== PROJECT CONTEXT ===\n"
                + "\n\n".join(context_parts)
                + "\n=== END CONTEXT ==="
            )
        if conv_history:
            prompt_parts.append(
                "=== CONVERSATION HISTORY ===\n"
                + conv_history
                + "\n=== END HISTORY ==="
            )
        prompt_parts.append("User: " + message)

        full_prompt = "\n\n".join(prompt_parts)

        system = (
            "You are an AI assistant working on a software project. "
            "Use the project context provided to give informed, specific answers. "
            "Reference the blueprint, tasks, and current phase when relevant. "
            "Maintain continuity with the conversation history."
        )

        self._append_history("user", message, metadata={
            "mode": "project", "worker": worker_name,
            "project_id": self.current_project,
        })

        try:
            self.db.request_write("update", "dashboard_state",
                                  {"_where": {"instance_name": worker_name},
                                   "status": "working"})
            result = await worker.send_message(full_prompt, system_prompt=system)
        except Exception as e:
            logger.error(f"Project chat to {worker_name} failed: {e}")
            return {"error": str(e), "_handler": "project_chat"}
        finally:
            self.db.request_write("update", "dashboard_state",
                                  {"_where": {"instance_name": worker_name},
                                   "status": "idle"})

        if not result.get("success"):
            err = result.get("error", "Unknown error")
            self._append_history("assistant", f"[{worker_name}] Error: {err}",
                                 metadata={"mode": "project", "worker": worker_name})
            return {"error": err, "worker": worker_name, "_handler": "project_chat"}

        resp_text = result["response"]
        self._append_history("assistant", resp_text, metadata={
            "mode": "project", "worker": worker_name,
            "project_id": self.current_project,
            "elapsed_ms": result.get("elapsed_ms"),
        })

        if self.phi3:
            await self.phi3.queue_summary(
                user_query=message,
                llm_response=resp_text,
                session_id=self.session_id,
                project_id=self.current_project,
                persist_full=True,
            )

        return {
            "response": resp_text,
            "worker": worker_name,
            "project_id": self.current_project,
            "elapsed_ms": result.get("elapsed_ms"),
            "_handler": "project_chat",
        }

    # ─── Discussion Chat (multi-model panel) ───

    async def discussion_chat(self, participants: List[str], message: str,
                              on_response: Callable = None,
                              auto_loop: bool = False) -> dict:
        """
        Multi-model sequential discussion. Each participant sees full history
        plus all prior responses in the current round. Calls on_response(worker, text, elapsed)
        after each model finishes so the dashboard can stream results in real-time.

        If auto_loop=True, after one full round of all participants, loop back
        and start another round automatically. Models keep discussing until
        cancel_discussion_round() is called (user interrupts or sends new message).
        """
        discussion_id = f"disc_{uuid.uuid4().hex[:8]}"
        self._discussion_cancel.clear()

        # Record user message
        self._append_history("user", message, metadata={
            "mode": "discussion", "discussion_id": discussion_id,
        })

        self._discussion_participants = list(participants)
        all_responses = []
        round_num = 0

        while True:
            round_num += 1

            # Re-read live participant list each round (UI can update between rounds)
            active_participants = list(self._discussion_participants)
            if not active_participants:
                logger.info(f"Discussion {discussion_id}: no participants, stopping")
                break

            round_responses = []
            cancelled = False

            # Notify round start (for rounds 2+)
            if round_num > 1 and on_response:
                await on_response("__round__",
                                  f"[Round {round_num}]", None)

            for worker_name in active_participants:
                # Skip if participant was removed mid-round (user unchecked)
                if worker_name not in self._discussion_participants:
                    logger.info(f"Discussion {discussion_id}: {worker_name} "
                                f"removed mid-round, skipping")
                    continue

                # Check cancellation between participants
                if self._discussion_cancel.is_set():
                    logger.info(f"Discussion {discussion_id} cancelled "
                                f"(round {round_num}, after {len(round_responses)} responses)")
                    cancelled = True
                    break

                worker = self.router.workers.get(worker_name)
                if not worker:
                    error_msg = f"[{worker_name}: unavailable, skipping]"
                    if on_response:
                        await on_response(worker_name, error_msg, None)
                    self._append_history("assistant", error_msg, metadata={
                        "mode": "discussion", "worker": worker_name,
                        "discussion_id": discussion_id, "error": True,
                    })
                    continue

                # Build per-worker history trimmed to this worker's context budget
                history = self._build_conversation_context("discussion", worker_name)

                # Build prompt: history + user message + prior responses in this round
                prompt_parts = []
                if history:
                    prompt_parts.append(
                        "=== DISCUSSION HISTORY ===\n" + history
                        + "\n=== END HISTORY ==="
                    )
                if round_num == 1:
                    prompt_parts.append(f"User: {message}")
                for prev_worker, prev_text in round_responses:
                    prompt_parts.append(f"{prev_worker}: {prev_text}")

                if round_num == 1:
                    prompt_parts.append(
                        f"You are {worker_name}. Continue the discussion. "
                        "Be concise and add your unique perspective."
                    )
                else:
                    prompt_parts.append(
                        f"You are {worker_name}. This is round {round_num} of an ongoing discussion. "
                        "Build on what has been said so far. Go deeper, challenge ideas, "
                        "or explore new angles. Stay concise."
                    )
                full_prompt = "\n\n".join(prompt_parts)

                system = (
                    f"You are {worker_name} participating in a multi-AI discussion panel. "
                    "Other AI models are also responding. Build on what others said, "
                    "offer your unique perspective, and keep responses focused and concise. "
                    "Do not repeat what others have already said."
                )

                try:
                    result = await worker.send_message(full_prompt, system_prompt=system)
                except Exception as e:
                    logger.error(f"Discussion: {worker_name} failed: {e}")
                    error_msg = f"[{worker_name}: error/timeout, skipping]"
                    if on_response:
                        await on_response(worker_name, error_msg, None)
                    self._append_history("assistant", error_msg, metadata={
                        "mode": "discussion", "worker": worker_name,
                        "discussion_id": discussion_id, "error": True,
                    })
                    continue

                if not result.get("success"):
                    err = result.get("error", "Unknown error")
                    error_msg = f"[{worker_name}: {err}, skipping]"
                    if on_response:
                        await on_response(worker_name, error_msg, None)
                    self._append_history("assistant", error_msg, metadata={
                        "mode": "discussion", "worker": worker_name,
                        "discussion_id": discussion_id, "error": True,
                    })
                    continue

                resp_text = result["response"]
                elapsed = result.get("elapsed_ms")
                round_responses.append((worker_name, resp_text))

                self._append_history("assistant", resp_text, metadata={
                    "mode": "discussion", "worker": worker_name,
                    "discussion_id": discussion_id,
                    "round": round_num,
                    "elapsed_ms": elapsed,
                })

                if on_response:
                    await on_response(worker_name, resp_text, elapsed)

            all_responses.extend(round_responses)

            # Queue Phi3 summary after each round
            if self.phi3 and round_responses:
                combined = "\n\n".join(
                    f"[{w}]: {t}" for w, t in round_responses
                )
                await self.phi3.queue_summary(
                    user_query=message,
                    llm_response=combined,
                    session_id=self.session_id,
                    project_id=self.current_project,
                    persist_full=True,
                )

            # If not auto-looping or cancelled, break after first round
            if not auto_loop or cancelled or self._discussion_cancel.is_set():
                break

            # Safety: if all workers failed this round, stop looping
            if not round_responses:
                logger.warning(f"Discussion {discussion_id}: all workers failed in round {round_num}, stopping loop")
                break

        return {
            "discussion_id": discussion_id,
            "participants": participants,
            "responses": [{"worker": w, "text": t} for w, t in all_responses],
            "rounds": round_num,
            "cancelled": self._discussion_cancel.is_set(),
            "_handler": "discussion_chat",
        }

    def cancel_discussion_round(self):
        """Cancel the current discussion round. Current model finishes, then loop breaks."""
        self._discussion_cancel.set()
        logger.info("Discussion round cancellation requested")

    def update_discussion_participants(self, participants: List[str]):
        """Update participant list mid-loop. Takes effect next round."""
        self._discussion_participants = list(participants)
        logger.info(f"Discussion participants updated: {participants}")

    # ─── Project Selection ───

    def select_project(self, project_id: Optional[str]) -> dict:
        """Select a project by ID. None deselects. Returns project info or error."""
        if project_id is None:
            self.current_project = None
            return {"selected": None, "message": "Project deselected"}
        project = self.db.get_project(project_id)
        if not project:
            return {"error": f"Project '{project_id}' not found"}
        self.current_project = project_id
        return {
            "selected": project_id,
            "name": project["name"],
            "status": project.get("status"),
            "current_phase": project.get("current_phase"),
        }

    def get_chat_history_filtered(self, project_id: Optional[str] = None) -> List[dict]:
        """Filter chat_history by project. None = return all (current behavior)."""
        if project_id is None:
            return self.chat_history
        filtered = []
        for entry in self.chat_history:
            meta = entry.get("metadata") or {}
            mode = meta.get("mode")
            # Include orchestrator mode messages (global, project-agnostic)
            if mode == "orchestrator" or mode is None:
                filtered.append(entry)
            # Include discussion mode messages (cross-worker, project-agnostic)
            elif mode == "discussion":
                filtered.append(entry)
            # Include project/direct messages matching this project
            elif meta.get("project_id") == project_id:
                filtered.append(entry)
            # Include system messages (doc recovery etc)
            elif entry.get("role") == "system":
                filtered.append(entry)
        return filtered

    def get_available_workers_with_status(self) -> list:
        """Return list of workers with name and type for UI dropdown."""
        results = []
        for name, adapter in self.router.workers.items():
            results.append({
                "name": name,
                "type": adapter.config.get("type", "unknown"),
                "model": adapter.config.get("model", name),
                "timeout": adapter.config.get("timeout", 120),
            })
        return results

    # ─── Project Setup ───

    async def create_project(self, name: str, description: str,
                             git_repo: str = None) -> dict:
        project_id = f"proj_{uuid.uuid4().hex[:8]}"
        project_path = self.working_dir / name.lower().replace(" ", "_")
        project_path.mkdir(parents=True, exist_ok=True)

        for sub in ["backend", "frontend", "database", "migrations",
                     "tests", "docs", "config"]:
            (project_path / sub).mkdir(exist_ok=True)

        if git_repo:
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", git_repo, str(project_path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await proc.communicate()
        else:
            proc = await asyncio.create_subprocess_exec(
                "git", "init", str(project_path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await proc.communicate()

        self.db.request_write("insert", "projects", {
            "project_id": project_id,
            "name": name,
            "description": description,
            "status": "active",
            "git_repo": git_repo,
            "project_path": str(project_path),
        })

        self.current_project = project_id
        logger.info(f"Project created: {project_id} at {project_path}")
        return {
            "project_id": project_id,
            "name": name,
            "path": str(project_path),
            "response": f"Project '{name}' created ({project_id})",
            "_handler": "create_project",
        }

    # ─── Phase 0: Blueprint ───

    async def generate_blueprint(self, requirements: str) -> dict:
        worker = self._get_worker("blueprint_generation")
        if not worker:
            return {"error": "No worker for blueprint_generation role",
                    "_handler": "generate_blueprint"}

        project = self.db.get_project(self.current_project)
        prompt = self._blueprint_prompt(requirements, project or {})

        worker_name = self._get_worker_name("blueprint_generation")
        logger.info(f"Phase 0: Generating blueprint via {worker_name}...")
        result = await worker.send_message(prompt, system_prompt=BLUEPRINT_SYSTEM)
        if not result.get("success"):
            return {"error": result.get("error"), "_handler": "generate_blueprint"}

        content = result["response"]

        self.db.request_write("insert", "blueprint_revisions", {
            "project_id": self.current_project,
            "version": 1,
            "blueprint_content": content,
            "changes_summary": "Initial blueprint",
        })

        # Dual audit: gatekeeper (completeness) + auditor (architecture)
        audits = {}
        tasks = []

        gatekeeper = self._get_worker("gatekeeper_review")
        auditor = self._get_worker("architecture_audit")

        if gatekeeper:
            tasks.append(self._audit(
                gatekeeper, self._get_worker_name("gatekeeper_review"),
                content, "completeness"))
        if auditor:
            tasks.append(self._audit(
                auditor, self._get_worker_name("architecture_audit"),
                content, "architecture"))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, dict):
                    audits.update(r)

        return {
            "blueprint": content,
            "version": 1,
            "audits": {k: v.get("feedback", "") for k, v in audits.items()},
            "generated_by": worker_name,
            "status": "ready_for_human_approval",
            "response": f"Blueprint v1 generated by {worker_name}",
            "_handler": "generate_blueprint",
            "_worker": worker_name,
        }

    async def _audit(self, worker, name, blueprint, focus):
        p = ("Review blueprint for completeness, clarity, acceptance criteria.\n"
             if focus == "completeness" else
             "Review blueprint architecture: API, DB, security, scalability.\n")
        p += f"Report issues only. Do NOT fix.\n\nBlueprint:\n{blueprint}"
        r = await worker.send_message(p)
        if r.get("success"):
            resp = r["response"].lower()
            has = any(w in resp for w in ["issue", "problem", "missing", "concern"])
            return {name: {"feedback": r["response"], "has_issues": has}}
        return {name: {"feedback": "Audit failed", "has_issues": False}}

    async def request_blueprint_approval(self) -> dict:
        self.db.request_write("update", "blueprint_revisions", {
            "_where": {"project_id": self.current_project, "version": 1},
            "approved_by": "HUMAN",
            "approved_at": datetime.now().isoformat(),
        })
        self.db.request_write("update", "projects", {
            "_where": {"project_id": self.current_project},
            "blueprint_approved_by": "HUMAN",
            "blueprint_approved_at": datetime.now().isoformat(),
            "current_phase": 1,
        })
        return {"status": "approved", "next_phase": 1}

    # ─── Task Planning (GSD) ───

    async def plan_tasks_gsd(self, phase: int, phase_requirements: str) -> dict:
        """Uses role: task_planning_gsd."""
        planner = self._get_worker("task_planning_gsd")
        if not planner:
            return {"error": "No worker for task_planning_gsd role",
                    "_handler": "plan_tasks_gsd"}

        prompt = (
            f"You are a GSD (Get Shit Done) task planner.\n"
            f"Break this phase into concrete, actionable development tasks.\n\n"
            f"Phase: {phase}\nRequirements:\n{phase_requirements}\n\n"
            f"For EACH task output JSON array:\n"
            f'[{{"module": "backend|frontend|database|config|tests", '
            f'"description": "...", "complexity_hint": "low|high", '
            f'"acceptance_criteria": ["...", "..."], '
            f'"dependencies": ["task_id or none"]}}]\n'
            f"Be specific. No vague tasks."
        )

        planner_name = self._get_worker_name("task_planning_gsd")
        logger.info(f"GSD Planning phase {phase} via {planner_name}...")
        result = await planner.send_message(prompt)
        if not result.get("success"):
            logger.error(f"GSD planning failed: {result.get('error')}")
            return {"error": result.get("error"), "_handler": "plan_tasks_gsd"}

        try:
            text = result["response"]
            start = text.find("[")
            end = text.rfind("]") + 1
            if start >= 0 and end > start:
                task_defs = json.loads(text[start:end])
            else:
                task_defs = []
        except json.JSONDecodeError:
            logger.error("GSD output not valid JSON — falling back to raw")
            task_defs = [{"module": "backend", "description": result["response"],
                          "complexity_hint": "high"}]

        return {
            "tasks": task_defs,
            "count": len(task_defs),
            "planned_by": planner_name,
            "response": f"GSD planned {len(task_defs)} tasks for phase {phase}",
            "_handler": "plan_tasks_gsd",
            "_worker": planner_name,
        }

    # ─── Phase 1-3: Tasks ───

    async def create_phase_tasks(self, phase: int, defs: list) -> list:
        ids = []
        for i, d in enumerate(defs):
            tid = f"task_{self.current_project}_{phase}_{i+1:03d}"
            self.db.request_write("insert", "tasks", {
                "task_id": tid,
                "project_id": self.current_project,
                "phase": phase,
                "module": d.get("module", "backend"),
                "description": d.get("description", ""),
                "status": "pending",
            })
            ids.append(tid)
        return ids

    async def classify_task(self, task_id: str) -> dict:
        """Uses role: gatekeeper_review for classification."""
        classifier = self._get_worker("gatekeeper_review")
        task = self.db.get_task(task_id)
        if not task:
            return {"error": "Task not found"}

        prompt = (
            f"Classify this task complexity as LOW or HIGH.\n"
            f"Task: {task['description']}\nModule: {task['module']}\n"
            f"Respond JSON: {{\"complexity\": \"LOW\" or \"HIGH\", "
            f"\"task_detail\": ..., \"acceptance_criteria\": ...}}"
        )

        complexity = "low"
        task_content = ""
        if classifier:
            r = await classifier.send_message(prompt)
            if r.get("success"):
                task_content = r["response"]
                complexity = "high" if "HIGH" in r["response"].upper() else "low"

        tf = self._save_task_file(task_id, task_content or task["description"])

        self.db.request_write("update", "tasks", {
            "_where": {"task_id": task_id},
            "complexity": complexity,
            "task_file_path": tf,
        })

        return {"task_id": task_id, "complexity": complexity, "task_file": tf}

    def _save_task_file(self, task_id, content) -> str:
        d = self.working_dir / "autonomous_factory" / "factory_state" / "tasks"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{task_id}.md"
        p.write_text(content)
        return str(p)

    async def send_to_tdd(self, task_id: str) -> dict:
        """Uses role: tdd_testing."""
        tdd_worker = self._get_worker("tdd_testing")
        if not tdd_worker:
            return {"error": "No worker for tdd_testing role"}

        tdd_name = self._get_worker_name("tdd_testing")
        task = self.db.get_task(task_id)
        ctx = ""
        tf = (task or {}).get("task_file_path", "")
        if tf and os.path.exists(tf):
            ctx = Path(tf).read_text()

        prompt = self._tdd_prompt(task or {}, ctx)

        self.db.request_write("update", "tasks", {
            "_where": {"task_id": task_id},
            "status": "testing", "assigned_to": tdd_name, "current_step": "AC",
        })

        logger.info(f"TDD: {task_id} → {tdd_name}")
        r = await tdd_worker.send_message(prompt, system_prompt=TDD_SYSTEM)
        if r.get("success"):
            self.db.request_write("insert", "checkpoints", {
                "task_id": task_id, "worker": tdd_name,
                "step": "TDD_COMPLETE",
                "state_data": json.dumps({"len": len(r["response"])}),
            })
            self.db.request_write("update", "tasks", {
                "_where": {"task_id": task_id},
                "current_step": "TDD_COMPLETE", "status": "review",
            })
            return {"success": True, "task_id": task_id, "tdd_by": tdd_name}
        return {"error": r.get("error")}

    async def gatekeeper_review(self, task_id: str) -> dict:
        """Uses role: gatekeeper_review."""
        gk = self._get_worker("gatekeeper_review")
        if not gk:
            return {"error": "No worker for gatekeeper_review role"}

        gk_name = self._get_worker_name("gatekeeper_review")
        task = self.db.get_task(task_id)
        threshold = self.config.get("quality_gates", {}).get("confidence_threshold", 0.90)

        r = await gk.send_message(
            f"Review task quality.\nTask: {(task or {}).get('description','')}\n"
            f"Provide confidence 0.0-1.0 and APPROVED/REJECTED.\n"
            f"Threshold: {threshold}"
        )

        if r.get("success"):
            approved = "APPROVED" in r["response"].upper()
            conf = 0.95 if approved else 0.75

            self.db.request_write("insert", "quality_gates", {
                "task_id": task_id, "gate_type": "gatekeeper",
                "passed": approved, "confidence_score": conf,
                "executed_by": gk_name,
            })

            if approved:
                self.db.request_write("update", "tasks", {
                    "_where": {"task_id": task_id}, "status": "approved"})
                return {"verdict": "APPROVED", "confidence": conf, "by": gk_name}
            else:
                retry = (task or {}).get("retry_count", 0) or 0
                if retry < 2:
                    self.db.request_write("update", "tasks", {
                        "_where": {"task_id": task_id},
                        "status": "testing", "retry_count": retry + 1})
                    return {"verdict": "REJECTED", "retry": retry + 1, "by": gk_name}
                else:
                    self.db.request_write("insert", "escalations", {
                        "task_id": task_id, "escalation_type": "gatekeeper_rejection",
                        "escalated_by": gk_name,
                        "escalation_reason": "Rejected 2x",
                        "status": "pending"})
                    self.db.request_write("update", "tasks", {
                        "_where": {"task_id": task_id}, "status": "blocked"})
                    return {"verdict": "ESCALATED", "by": gk_name}
        return {"error": "Review failed"}

    async def handle_escalation(self, esc_id: int, decision: str):
        self.db.request_write("update", "escalations", {
            "_where": {"escalation_id": esc_id},
            "human_decision": decision, "status": "resolved",
            "resolved_at": datetime.now().isoformat(),
        })
        return {"resolved": True}

    # ─── Prompt builders ───

    def _blueprint_prompt(self, reqs, proj):
        return (
            f"Generate project blueprint for: {proj.get('name','')}\n"
            f"Description: {proj.get('description','')}\n\n"
            f"Requirements:\n{reqs}\n\n"
            f"Include: architecture, DB schema, APIs, frontend components, "
            f"5 phases, tasks + acceptance criteria, security, testing."
        )

    def _tdd_prompt(self, task, ctx):
        return (
            f"Execute 12-step TDD protocol:\n\n"
            f"Task: {task.get('description','')}\nModule: {task.get('module','')}\n\n"
            f"Context:\n{ctx}\n\n"
            f"Steps: AC→TDE-RED→TDE-GREEN→BC→BF→SEA→DS→OA→VB→GIT→CL→CCP→AD"
        )


BLUEPRINT_SYSTEM = (
    "You are a senior architect. Generate comprehensive blueprints with: "
    "architecture, DB schema, APIs, frontend, 5 phases, tasks + acceptance criteria, "
    "security, testing. Output structured markdown."
)

TDD_SYSTEM = (
    "You are a TDD engineer. Follow 12-step protocol precisely: "
    "AC, TDE-RED, TDE-GREEN, BC, BF, SEA, DS, OA, VB, GIT, CL, CCP, AD. "
    "Report each step."
)
