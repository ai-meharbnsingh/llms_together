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
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from orchestration.database import ReadOnlyDB
from orchestration.role_router import RoleRouter
from workers.adapters import WorkerAdapter

# Autonomous execution engine imports — guarded so existing functionality
# continues even if these modules aren't present yet.
try:
    from orchestration.context_manager import ContextManager
    from orchestration.contract_generator import ContractGenerator
    from orchestration.contract_validator import ContractValidator
    from orchestration.output_parser import OutputParser
    from orchestration.rules_engine import RulesEngine
    from orchestration.tdd_pipeline import TDDPipeline
    from orchestration.git_manager import GitManager
    from orchestration.dac_tagger import DaCTagger
    from orchestration.learning_log import LearningLog
    from orchestration.cicd_generator import CICDGenerator
except ImportError as _exc:
    logging.getLogger("factory.orchestrator").warning(
        f"Optional execution-engine imports unavailable: {_exc}"
    )

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

        # Chat history — persisted to disk so it survives restarts.
        # FER-AF-001 FIX: resolve relative factory_state_dir against working_dir so
        # Orchestrator and Watchdog always write to the same physical directory.
        state_dir_cfg = config.get("factory", {}).get("factory_state_dir")
        if state_dir_cfg:
            _sd = Path(state_dir_cfg)
            if not _sd.is_absolute():
                _sd = self.working_dir / "autonomous_factory" / _sd
            self._history_file = _sd / "chat_history.json"
            self._sessions_file = _sd / "chat_sessions.json"
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

        # FER-AF-036: Semaphore to cap concurrent task execution
        _max_concurrent = config.get("execution", {}).get("max_concurrent_tasks", 4)
        self._task_semaphore = asyncio.Semaphore(_max_concurrent)

        # FER-AF-040: Lock to serialise merge_to_develop calls
        self._merge_lock = asyncio.Lock()

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
        """Return list of workers with name, type, and live health status."""
        results = []
        for name, adapter in self.router.workers.items():
            entry = {
                "name": name,
                "type": adapter.config.get("type", "unknown"),
                "model": adapter.config.get("model", name),
                "timeout": adapter.config.get("timeout", 120),
            }
            # Enrich with live health data from worker_health table
            health = self.db.get_worker_health(name) if self.db else None
            if health:
                entry["status"] = health.get("status", "offline")
                entry["last_heartbeat"] = health.get("last_heartbeat")
                entry["failure_count"] = health.get("failure_count", 0)
                entry["total_tasks_completed"] = health.get("total_tasks_completed", 0)
            else:
                entry["status"] = "offline"
            results.append(entry)
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

        # FER-AF-026: Use fire-and-forget request_write — not request_write_and_wait.
        # request_write_and_wait blocks until Watchdog drains the queue; in environments
        # without a running Watchdog it times out. Fire-and-forget matches every other
        # write in this codebase.
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
                task_defs = None
        except json.JSONDecodeError:
            task_defs = None

        # FER-AF-013 FIX: Never store raw LLM response as task description.
        # If the planner returned unparseable output, surface a hard error so
        # callers can retry or abort — not silently create a garbage task.
        if task_defs is None:
            snippet = result["response"][:200]
            logger.error(
                f"GSD task-planner returned non-JSON output (phase {phase}). "
                f"Snippet: {snippet!r}"
            )
            return {
                "error": "GSD planner output was not valid JSON — cannot create tasks",
                "_handler": "plan_tasks_gsd",
                "_worker": planner_name,
            }

        return {
            "tasks": task_defs,
            "count": len(task_defs),
            "planned_by": planner_name,
            "response": f"GSD planned {len(task_defs)} tasks for phase {phase}",
            "_handler": "plan_tasks_gsd",
            "_worker": planner_name,
        }

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
            f"Execute 13-step TDD protocol:\n\n"
            f"Task: {task.get('description','')}\nModule: {task.get('module','')}\n\n"
            f"Context:\n{ctx}\n\n"
            f"Steps: AC→TDE-RED→TDE-GREEN→BC→BF→SEA→DS→OA→VB→GIT→CL→CCP→AD"
        )

    # ═══════════════════════════════════════════════════════════
    # AUTONOMOUS EXECUTION ENGINE
    # Phases 0-5: Blueprint → Build → Test → Gate → Deploy
    # ═══════════════════════════════════════════════════════════

    async def execute_project(self, project_id: str,
                               on_progress: Callable = None) -> dict:
        """
        Execute a full autonomous project lifecycle.

        Phases:
        0. Blueprint generation + audit + contract creation
        1-3. Task planning + parallel execution + TDD + gates
        4. Proto deploy + E2E tests
        5. Production deploy

        Args:
            project_id: The project to execute
            on_progress: Callback(phase, step, status, detail) for live updates

        Returns:
            {"success": bool, "phases_completed": int, "errors": []}
        """
        project = self.db.get_project(project_id)
        if not project:
            return {"success": False, "phases_completed": 0,
                    "errors": [f"Project {project_id} not found"]}

        project_path = project.get("project_path") or str(
            self.working_dir / "projects" / project["name"].replace(" ", "_").lower()
        )

        # Initialize execution components
        context_mgr = ContextManager(str(self.working_dir), self.db)
        rules_engine = RulesEngine(self.db)
        dac_tagger = DaCTagger(self.db)
        learning_log = LearningLog(self.db)
        git_mgr = GitManager(project_path)

        errors = []
        phases_completed = 0

        try:
            # ─── Phase 0: Blueprint ───
            if on_progress:
                await on_progress(0, "blueprint", "running", "Generating blueprint")

            blueprint_result = await self._phase_blueprint(
                project, project_path, context_mgr, rules_engine, on_progress
            )
            if not blueprint_result.get("approved"):
                return {"success": False, "phases_completed": 0,
                        "errors": ["Blueprint not approved"],
                        "awaiting": "blueprint_approval",
                        "project_path": project_path}

            # Init git repo — abort if git fails (FER-AF-017)
            if not git_mgr.init_repo():
                logger.error(f"Git init failed for project {project_id} — aborting")
                return {"success": False, "phases_completed": 0,
                        "errors": ["Git repository initialization failed"],
                        "project_path": project_path}
            phases_completed = 0

            # ─── Generate tasks from blueprint ───────────────────────────────
            # Parse blueprint into concrete task rows in DB so _phase_build()
            # finds real work to execute instead of skipping with "No tasks".
            tasks_count = await self._generate_tasks_from_blueprint(
                project, blueprint_result.get("blueprint", "")
            )
            if on_progress:
                await on_progress(0, "task_gen", "running",
                                  f"Generated {tasks_count} tasks across 3 phases")
            logger.info(f"Task generation: {tasks_count} tasks created across 3 phases")

            # ─── Phases 1-3: Build ───
            total_phases = blueprint_result.get("total_phases", 3)
            for phase_num in range(1, total_phases + 1):
                if on_progress:
                    await on_progress(phase_num, "planning", "running",
                                     f"Phase {phase_num} planning")

                phase_result = await self._phase_build(
                    project, project_path, phase_num,
                    context_mgr, rules_engine, dac_tagger,
                    learning_log, git_mgr, on_progress
                )

                errors.extend(phase_result.get("errors", []))

                # Only hard-stop if the phase produced ZERO completed tasks
                # (i.e. nothing was built at all). Partial failures are expected
                # and should not block subsequent phases.
                if phase_result.get("tasks_completed", 0) == 0 and phase_result.get("tasks_failed", 0) > 0:
                    logger.error(f"Phase {phase_num}: all tasks failed — stopping build")
                    break

                phases_completed = phase_num

            # ─── Phase 4: Proto + CI/CD + E2E ───
            if phases_completed >= total_phases:
                if on_progress:
                    await on_progress(4, "proto", "running", "Proto deployment")

                git_mgr.tag_version(f"v0.{phases_completed}-proto")

                # N/O: Generate CI/CD files (GH Actions, Dockerfile, docker-compose)
                project_type = project.get("project_type", "web")
                try:
                    cicd = CICDGenerator(project_path)
                    cicd_result = cicd.generate(project_type)
                    logger.info(
                        f"CI/CD generated: {len(cicd_result['files_created'])} files "
                        f"for {project_type}"
                    )
                    if on_progress:
                        await on_progress(
                            4, "cicd", "running",
                            f"CI/CD generated: {len(cicd_result['files_created'])} files",
                        )
                except Exception as _cicd_err:
                    logger.error(f"CI/CD generation failed: {_cicd_err}")
                    cicd_result = {"files_created": []}

                # M: Auto-run E2E tests on generated project
                e2e_result = await self._run_e2e_tests(project_path, on_progress)

                # Update project status
                self.db.request_write("update", "projects", {
                    "current_phase": 4,
                    "status": "active",
                    "_where": {"project_id": project_id},
                })

                # FER-AF-010 FIX: Propagate E2E failure so dashboard can block UAT button.
                e2e_failed = not e2e_result.get("success", True)

                if e2e_failed:
                    logger.warning(
                        f"E2E tests failed for {project_id} — UAT approval is blocked until tests pass"
                    )

                # Pause for human UAT — blocked if E2E failed
                return {
                    "success": True,
                    "phases_completed": phases_completed,
                    "errors": errors,
                    "awaiting": "uat_blocked_e2e" if e2e_failed else "uat_approval",
                    "project_path": project_path,
                    "e2e": e2e_result,
                    "e2e_failed": e2e_failed,
                    "cicd_files": cicd_result["files_created"],
                }

            # ─── Phase 5: Production ───
            # Triggered separately after UAT approval

        except Exception as e:
            logger.error(f"Project execution failed: {e}", exc_info=True)
            errors.append(str(e))

        return {
            "success": phases_completed > 0,
            "phases_completed": phases_completed,
            "errors": errors,
            "project_path": project_path,
        }

    async def _phase_blueprint(self, project: dict, project_path: str,
                                context_mgr: ContextManager,
                                rules_engine: RulesEngine,
                                on_progress: Callable = None) -> dict:
        """Phase 0: Generate blueprint, dual audit, create contracts."""
        project_id = project["project_id"]
        project_type = project.get("project_type", "web")

        # 0. Check if blueprint already approved — skip generation if so
        existing = self.db.get_latest_blueprint(project_id)
        if existing and existing.get("approved_by"):
            logger.info(f"Blueprint already approved for {project_id} — skipping generation")
            return {
                "approved": True,
                "blueprint": existing.get("blueprint_content", ""),
                "version": existing.get("version", 1),
                "total_phases": 3,
            }

        # 1. Generate blueprint
        blueprint_worker = self._get_worker("blueprint_generation")
        if not blueprint_worker:
            return {"approved": False, "error": "No blueprint worker"}

        protocol = context_mgr.load_protocol(project_type)
        prompt = (
            f"Generate a detailed software blueprint for:\n"
            f"Project: {project['name']}\n"
            f"Description: {project.get('description', '')}\n"
            f"Type: {project_type}\n\n"
            f"Protocol:\n{protocol}\n\n"
            f"Include: architecture, phases (1-3), task breakdown, "
            f"API endpoints, DB schema, types, tech stack."
        )

        result = await blueprint_worker.send_message(
            prompt, system_prompt="Generate a comprehensive software blueprint"
        )
        if not result.get("success"):
            return {"approved": False, "error": "Blueprint generation failed"}

        blueprint_content = result["response"]

        # 2. Dual audit: Kimi + Gemini
        audit_result = await self._dual_audit_blueprint(
            blueprint_content, project_type
        )

        # 3. Auto-revise if issues found (max 3 iterations)
        revision = 0
        while audit_result.get("issues") and revision < 3:
            revision += 1
            logger.info(f"Blueprint revision {revision}: {len(audit_result['issues'])} issues")

            blueprint_content = await self._revise_blueprint(
                blueprint_worker, blueprint_content, audit_result
            )
            audit_result = await self._dual_audit_blueprint(
                blueprint_content, project_type
            )

        # 4. Save blueprint
        self.db.request_write("insert", "blueprint_revisions", {
            "project_id": project_id,
            "version": revision + 1,
            "blueprint_content": blueprint_content,
            "changes_summary": f"v{revision + 1}: {len(audit_result.get('issues', []))} remaining issues",
            "reason": "auto_generated",
        })

        # 5. Generate contracts
        contract_gen = ContractGenerator(project_path)
        contracts = await contract_gen.generate_from_blueprint(
            blueprint_content, blueprint_worker
        )

        # 6. Kimi validates contracts
        kimi = self._get_worker("gatekeeper_review")
        if kimi:
            validation = await contract_gen.validate_with_kimi(
                kimi, blueprint_content
            )
            if not validation.get("valid"):
                logger.warning(f"Contract validation issues: {validation.get('issues')}")

        # 7. Generate rules file
        rules_engine.generate_rules_file(project_path, project_type)

        # 8. Create folder structure (via watchdog reference)
        Path(project_path).mkdir(parents=True, exist_ok=True)

        # Create a task row for the blueprint phase so escalation FK constraint is satisfied
        self.db.request_write("insert", "tasks", {
            "task_id": f"blueprint_{project_id}",
            "project_id": project_id,
            "phase": 0,
            "module": "blueprint/approval",
            "description": "Blueprint generation, audit, and human approval",
            "status": "pending",
        })

        # Signal: waiting for human approval
        self.db.request_write("insert", "escalations", {
            "task_id": f"blueprint_{project_id}",
            "escalation_type": "blueprint_approval",
            "escalated_by": "orchestrator",
            "escalation_reason": "Blueprint ready for human review and approval",
            "context_data": json.dumps({
                "blueprint_version": revision + 1,
                "audit_issues": audit_result.get("issues", []),
                "contracts_generated": bool(contracts.get("generated_files")),
            }),
            "status": "pending",
        })

        return {
            "approved": False,  # Will be set to True via dashboard approval
            "blueprint": blueprint_content,
            "contracts": contracts,
            "audit": audit_result,
            "version": revision + 1,
            "awaiting_approval": True,
        }

    async def _dual_audit_blueprint(self, blueprint: str,
                                      project_type: str) -> dict:
        """Dual audit: Kimi (gatekeeper) + Gemini (architecture)."""
        issues = []

        # Kimi audit
        kimi = self._get_worker("gatekeeper_review")
        if kimi:
            # FER-AF-029: Increase truncation limit and warn when triggered
            _bp_kimi = blueprint
            if len(_bp_kimi) > 15_000:
                logger.warning(
                    f"Blueprint truncated {len(_bp_kimi)} → 15000 chars for Kimi completeness audit"
                )
                _bp_kimi = _bp_kimi[:15_000]
            kimi_result = await kimi.send_message(
                f"Review this {project_type} blueprint for completeness, "
                f"feasibility, and potential issues:\n\n{_bp_kimi}",
                system_prompt="Blueprint quality review"
            )
            if kimi_result.get("success"):
                issues.append({"source": "kimi", "feedback": kimi_result["response"]})

        # Gemini audit
        gemini = self._get_worker("architecture_audit")
        if gemini:
            # FER-AF-029: Increase truncation limit and warn when triggered
            _bp_gemini = blueprint
            if len(_bp_gemini) > 15_000:
                logger.warning(
                    f"Blueprint truncated {len(_bp_gemini)} → 15000 chars for Gemini architecture audit"
                )
                _bp_gemini = _bp_gemini[:15_000]
            gemini_result = await gemini.send_message(
                f"Audit this {project_type} blueprint architecture for "
                f"scalability, security, and best practices:\n\n{_bp_gemini}",
                system_prompt="Architecture audit"
            )
            if gemini_result.get("success"):
                issues.append({"source": "gemini", "feedback": gemini_result["response"]})

        return {"issues": issues, "audited": True}

    async def _revise_blueprint(self, worker, blueprint: str,
                                  audit_result: dict) -> str:
        """Auto-revise blueprint based on audit feedback."""
        feedback = "\n".join(
            f"[{i['source']}]: {i['feedback'][:1000]}"
            for i in audit_result.get("issues", [])
        )

        # FER-AF-029: Increase truncation limit and warn when triggered
        _bp_rev = blueprint
        if len(_bp_rev) > 15_000:
            logger.warning(
                f"Blueprint truncated {len(_bp_rev)} → 15000 chars for revision prompt"
            )
            _bp_rev = _bp_rev[:15_000]
        result = await worker.send_message(
            f"Revise this blueprint based on audit feedback:\n\n"
            f"BLUEPRINT:\n{_bp_rev}\n\n"
            f"FEEDBACK:\n{feedback}",
            system_prompt="Revise blueprint to address audit feedback"
        )
        return result.get("response", blueprint) if result.get("success") else blueprint

    async def _classify_dependencies(self, tasks: list) -> dict:
        """Ask Kimi to build a dependency graph for the given task list.

        Returns {task_id: [dep_task_id, ...]} — all deps must complete before
        the task starts.  Falls back to module-path heuristics when Kimi is
        unavailable or returns unparseable output.
        """
        if not tasks:
            return {}

        task_ids = [t["task_id"] for t in tasks]

        def _heuristic(t):
            mod = t.get("module", "")
            # Routers / views / controllers depend on models and DB modules
            if any(x in mod for x in ("/routers/", "/views/", "/routes/", "/controllers/")):
                return [o["task_id"] for o in tasks
                        if o["task_id"] != t["task_id"] and
                        any(k in o.get("module", "")
                            for k in ("model", "database", "db", "schema"))]
            # Tests depend on all non-test tasks
            if mod.startswith("tests/") or "/tests/" in mod:
                return [o["task_id"] for o in tasks
                        if o["task_id"] != t["task_id"] and
                        not (o.get("module", "").startswith("tests/") or
                             "/tests/" in o.get("module", ""))]
            # Entry-points depend on everything else
            if mod.endswith(("main.py", "app.py", "index.ts", "index.tsx")):
                return [o["task_id"] for o in tasks
                        if o["task_id"] != t["task_id"] and
                        not o.get("module", "").endswith(
                            ("main.py", "app.py", "index.ts", "index.tsx"))]
            return []

        kimi = self._get_worker("gatekeeper_review")
        if not kimi:
            logger.info("No Kimi available — using module-path heuristics for dep graph")
            return {t["task_id"]: _heuristic(t) for t in tasks}

        task_list_str = "\n".join(
            f"  {t['task_id']}: module={t.get('module', '')!r}, "
            f"desc={t.get('description', '')[:80]!r}"
            for t in tasks
        )
        example = (f'{{"{task_ids[0]}": [], '
                   f'"{task_ids[1] if len(task_ids) > 1 else task_ids[0]}": '
                   f'["{task_ids[0]}"]}}')
        prompt = (
            "Analyze these software tasks and identify dependencies.\n"
            "Task B depends on task A if B imports from, extends, or requires "
            "files that A creates.\n\n"
            f"Tasks:\n{task_list_str}\n\n"
            "Return ONLY a JSON object mapping each task_id to a list of "
            "task_ids it depends on. Use [] for no dependencies. "
            f"All task_ids must appear as keys.\nExample: {example}"
        )
        try:
            result = await kimi.send_message(
                prompt, system_prompt="Task dependency classification"
            )
            if result.get("success"):
                m = re.search(r'\{[\s\S]*\}', result["response"])
                if m:
                    dep_graph = json.loads(m.group(0))
                    valid = set(task_ids)
                    return {
                        tid: [d for d in deps if d in valid]
                        for tid, deps in dep_graph.items()
                        if tid in valid
                    }
        except Exception as exc:
            logger.warning(f"Kimi dep classification failed ({exc}) — using heuristics")

        return {t["task_id"]: _heuristic(t) for t in tasks}

    async def _execute_single_task(
        self, task: dict, project: dict, project_path: str,
        context_mgr: "ContextManager", rules_engine: "RulesEngine",
        dac_tagger: "DaCTagger", git_mgr: "GitManager",
        on_progress: Callable = None,
        learning_log: "LearningLog" = None,
    ) -> dict:
        """Execute one task through the full 13-step pipeline.

        Returns {"success": bool, "task_id": str, "files": int,
                 "tdd": bool, "gate": str, "error": str}
        """
        task_id = task["task_id"]
        project_id = project["project_id"]
        phase_num = task.get("phase", 0)

        # Mark in_progress immediately so Watchdog stuck-task detection fires (FER-AF-042)
        self.db.request_write("update", "tasks", {
            "_where": {"task_id": task_id},
            "status": "in_progress",
            "current_step": "START",
        })

        if on_progress:
            await on_progress(phase_num, task_id, "running",
                             f"Executing: {task['description'][:50]}")

        # FER-AF-006: Budget cap enforcement — check cost before running task
        max_cost = getattr(self, "config", {}).get("cost_controls", {}).get(
            "max_api_cost_per_project", 50.0
        )
        cost_rows = self.db.get_cost_summary(project_id)
        total_spent = sum(row.get("total_cost", 0.0) or 0.0 for row in cost_rows)
        if total_spent >= max_cost:
            logger.warning(
                f"Task {task_id} aborted: project {project_id} has exceeded budget "
                f"(${total_spent:.4f} >= ${max_cost:.2f})"
            )
            self.db.request_write("update", "tasks", {
                "_where": {"task_id": task_id},
                "status": "failed",
                "current_step": "BUDGET_EXCEEDED",
            })
            return {
                "success": False,
                "task_id": task_id,
                "files": 0,
                "tdd": False,
                "gate": None,
                "error": (
                    f"Budget exceeded: project has spent ${total_spent:.4f} "
                    f"which meets or exceeds the ${max_cost:.2f} cap"
                ),
            }

        # 1. Git pull latest (async — serialised via _commit_lock, FER-AF-038)
        _pull_result = git_mgr.pull_latest()
        if hasattr(_pull_result, "__await__") or asyncio.iscoroutine(_pull_result):
            await _pull_result

        # 2. Kimi classifies complexity
        complexity = await self._classify_task(task)

        # 3. Role router assigns worker
        module = task.get("module", "")
        if module.startswith("frontend/components"):
            role = "frontend_design"
        elif complexity == "high":
            role = "code_generation_complex"
        else:
            role = "code_generation_simple"

        worker = self._get_worker(role)
        if not worker:
            return {"success": False, "task_id": task_id, "files": 0,
                    "tdd": False, "gate": None,
                    "error": f"No worker for role {role}"}

        # 4. Build task prompt
        prompt = context_mgr.build_task_prompt(task, project, project_path)

        # FER-AF-003 FIX: Inject past learnings so workers benefit from prior
        # failures.  Only non-empty results are appended to avoid prompt bloat.
        if learning_log is not None:
            learning_text = learning_log.inject_learnings(
                task.get("description", ""),
                project_type=project.get("project_type"),
            )
            if learning_text:
                prompt = f"{prompt}\n\n{learning_text}"

        # 5. Worker executes
        worker_result = await worker.send_message(
            prompt, system_prompt=f"Execute task {task_id}"
        )

        # Issue 4: Wire cost tracking — queue token counts after every worker call (R12)
        try:
            tokens = worker_result.get("tokens", {})
            prompt_tok = tokens.get("prompt", 0)
            comp_tok = tokens.get("completion", 0)
            from orchestration.database import queue_write as _qw
            _qw(operation="insert", table="cost_tracking", params={
                "task_id": task_id, "project_id": project_id,
                "worker": self._get_worker_name(role), "operation": "task_execution",
                "prompt_tokens": prompt_tok, "completion_tokens": comp_tok,
                "total_tokens": prompt_tok + comp_tok,
                "elapsed_ms": worker_result.get("elapsed_ms"),
            }, requester="orchestrator")
        except Exception as _ce:
            logger.warning(f"Cost tracking write failed (non-fatal): {_ce}")

        # Update context usage on dashboard for this worker
        try:
            tokens = worker_result.get("tokens", {})
            total_tok = tokens.get("prompt", 0) + tokens.get("completion", 0)
            if total_tok > 0:
                w_name = self._get_worker_name(role)
                w_cfg = self.config.get("workers", {}).get(w_name, {})
                max_ctx = w_cfg.get("max_context_tokens", 0)
                ctx_pct = (total_tok / max_ctx) if max_ctx > 0 else 0.0
                self.db.request_write("raw", "dashboard_state", {
                    "sql": (
                        "UPDATE dashboard_state SET context_usage_percent=?, "
                        "context_token_count=? WHERE instance_name=?"
                    ),
                    "args": [ctx_pct, total_tok, w_name],
                })
        except Exception as _ctx_e:
            logger.debug(f"Context usage update failed (non-fatal): {_ctx_e}")

        if not worker_result.get("success"):
            dac_tagger.tag(task_id, "worker_crash",
                           f"Worker {role} failed on task",
                           source_worker=self._get_worker_name(role),
                           project_id=project_id)
            return {"success": False, "task_id": task_id, "files": 0,
                    "tdd": False, "gate": None, "error": "Worker failed"}

        # 6. Parse structured output
        parser = OutputParser(project_path)
        task_module = task.get("module")
        summary, violations = parser.parse_and_apply(
            worker_result["response"], task_id,
            worker_name=self._get_worker_name(role),
            task_module=task_module,
        )
        if violations:
            logger.warning(f"Task {task_id}: retrying due to violations")
            # Build a targeted retry message: call out scope violations explicitly
            scope_viols = [
                v for v in violations
                if v.get("violation_tag") == "TRAP"
                and "out_of_scope_write" in v.get("detail", "")
            ]
            if scope_viols:
                allowed_prefix = parser._get_allowed_prefix(task_module) or task_module
                viol_lines = "\n".join(
                    f"  - OUT OF SCOPE: You wrote '{v['path']}' but your module is "
                    f"'{task_module}'. ONLY write files under '{v['allowed_prefix']}'."
                    for v in scope_viols
                )
                retry_prompt = (
                    f"{prompt}\n\n"
                    f"SCOPE VIOLATIONS — previous attempt wrote files outside your module:\n"
                    f"{viol_lines}\n"
                    f"Redo the task writing ONLY files under '{allowed_prefix}'."
                )
            else:
                retry_prompt = prompt + "\n\nPREVIOUS ATTEMPT HAD ISSUES. Return VALID JSON."
            retry = await worker.send_message(
                retry_prompt,
                system_prompt=f"Retry task {task_id} — must return valid structured JSON"
            )
            if retry.get("success"):
                summary, violations = parser.parse_and_apply(
                    retry["response"], task_id,
                    worker_name=self._get_worker_name(role),
                    task_module=task_module,
                )

        # --- FER-AF-012: Abort task if TRAP violations persist after retry ---
        # TRAP = worker violated module-scope boundaries. Files are already
        # skipped by OutputParser, but we must also abort the task so broken
        # code never enters the codebase or triggers the quality gate.
        persistent_traps = [
            v for v in violations
            if v.get("violation_tag") == "TRAP" or v.get("tag_type") == "TRAP"
        ]
        if persistent_traps:
            trap_path = persistent_traps[0].get("path", "unknown file")
            logger.error(
                f"Task {task_id}: TRAP scope violation persists after retry — "
                f"aborting task. File: {trap_path!r}"
            )
            dac_tagger.tag(task_id, "trap_abort",
                           f"TRAP abort: scope violation on {trap_path}",
                           source_worker=self._get_worker_name(role),
                           project_id=project_id)
            self.db.request_write("update", "tasks", {
                "_where": {"task_id": task_id},
                "status": "failed", "current_step": "TRAP",
            })
            return {
                "success": False,
                "task_id": task_id,
                "files": 0,
                "tdd": False,
                "gate": None,
                "error": f"TRAP: scope violation — {trap_path}",
            }

        # 7. Contract validation
        validator = ContractValidator(project_path)
        if validator.load_contracts():
            code_files = [{"path": f["path"], "content": f.get("content", "")}
                          for f in summary.get("files_written", [])
                          if f.get("action") != "deleted"]
            for cf in code_files:
                fp = Path(project_path) / cf["path"]
                if fp.exists():
                    try:
                        cf["content"] = fp.read_text()
                    except IOError:
                        pass
            val_result = validator.validate(code_files)
            if not val_result["valid"]:
                for mismatch in val_result["mismatches"]:
                    dac_tagger.tag(task_id, "contract_violation",
                                   f"{mismatch['type']}: {mismatch['detail']}",
                                   project_id=project_id)

        # 8. Rules check
        rules_engine.check_automated_rules(
            task_id, {"files": summary.get("files_written", [])}
        )

        # 9. TDD Pipeline
        tdd = TDDPipeline(self.db, self.router, git_mgr, project_path=project_path)
        files_with_content = []
        for fw in summary.get("files_written", []):
            entry = {"path": fw["path"], "action": fw.get("action", ""),
                     "size": fw.get("size", 0), "content": ""}
            fp = Path(project_path) / fw["path"]
            if fp.exists():
                try:
                    entry["content"] = fp.read_text(errors="replace")[:5000]
                except IOError:
                    pass
            files_with_content.append(entry)
        parsed_output = {"files": files_with_content,
                         "decisions": summary.get("decisions_logged", [])}

        tdd_result = await tdd.execute(
            task, project, parsed_output, on_progress=on_progress
        )

        # 10. DaC tagging from TDD
        dac_tagger.tag_from_tdd_result(task_id, tdd_result, project_id)

        # 11. Kimi quality gate (with retry loop)
        gate_result = await self._quality_gate(
            task, parsed_output, project_path, validator
        )
        rejection_count = 0
        while gate_result.get("verdict") == "REJECTED" and rejection_count < 2:
            rejection_count += 1
            dac_tagger.tag_gate_rejection(
                task_id, rejection_count, gate_result, project_id
            )
            retry = await worker.send_message(
                prompt + f"\n\nPREVIOUS ATTEMPT REJECTED: {gate_result.get('issues', [])}",
                system_prompt=f"Retry task {task_id} — address gate feedback"
            )
            if retry.get("success"):
                summary, _ = parser.parse_and_apply(
                    retry["response"], task_id,
                    worker_name=self._get_worker_name(role),
                    task_module=task_module,
                )
                gate_result = await self._quality_gate(
                    task, parsed_output, project_path, validator
                )

        succeeded = gate_result.get("verdict") != "REJECTED"
        worker_name = self._get_worker_name(role)
        if succeeded:
            self.db.request_write("update", "tasks", {
                "_where": {"task_id": task_id},
                "status": "approved", "current_step": "AD",
            })
            # Clear worker's current_task_id and increment completed count
            self.db.request_write("raw", "dashboard_state", {
                "sql": (
                    "UPDATE dashboard_state SET current_task_id=NULL, "
                    "tasks_completed_today=tasks_completed_today+1, "
                    "status='idle', last_activity=CURRENT_TIMESTAMP "
                    "WHERE instance_name=?"
                ),
                "args": [worker_name],
            })
            self.db.request_write("raw", "worker_health", {
                "sql": (
                    "UPDATE worker_health SET "
                    "total_tasks_completed=total_tasks_completed+1 "
                    "WHERE worker_id=?"
                ),
                "args": [worker_name],
            })
        else:
            dac_tagger.tag(task_id, "double_rejection",
                           f"Task rejected {rejection_count + 1}x by gatekeeper",
                           project_id=project_id)
            self.db.request_write("update", "tasks", {
                "_where": {"task_id": task_id},
                "status": "failed", "current_step": "AD",
            })
            # Clear worker's current_task_id on failure too
            self.db.request_write("raw", "dashboard_state", {
                "sql": (
                    "UPDATE dashboard_state SET current_task_id=NULL, "
                    "status='idle', last_activity=CURRENT_TIMESTAMP "
                    "WHERE instance_name=?"
                ),
                "args": [worker_name],
            })

        # 12. Git commit (async — serialised via _commit_lock to prevent index.lock races)
        await git_mgr.atomic_commit(task_id, f"Complete: {task['description'][:50]}")

        # Phi3 summary
        if self.phi3:
            await self.phi3.queue_summary(
                f"[AUTO] Task {task_id}: {task['description'][:100]}",
                f"Result: {'success' if succeeded else 'failed'}. "
                f"Files: {len(summary.get('files_written', []))}. "
                f"TDD: {tdd_result.get('success')}",
                self.session_id, persist_full=True
            )

        return {
            "success": succeeded,
            "task_id": task_id,
            "files": len(summary.get("files_written", [])),
            "tdd": tdd_result.get("success"),
            "gate": gate_result.get("verdict"),
            "error": "" if succeeded else "Gate rejected",
        }

    async def _phase_build(self, project: dict, project_path: str,
                            phase_num: int, context_mgr: "ContextManager",
                            rules_engine: "RulesEngine", dac_tagger: "DaCTagger",
                            learning_log: "LearningLog", git_mgr: "GitManager",
                            on_progress: Callable = None) -> dict:
        """Execute a single build phase (1-3) with dependency-aware parallel tasks."""
        project_id = project["project_id"]

        # FER-AF-016 FIX: Verify git state before touching any tasks.
        # Detached HEAD causes cryptic failures mid-build; surface it early.
        git_state = git_mgr.verify_state()
        if not git_state.get("ok"):
            issue = git_state.get("issue", "unknown git state problem")
            logger.error(f"Phase {phase_num}: aborting — git state invalid: {issue}")
            return {"success": False, "error": f"git state invalid: {issue}",
                    "tasks_completed": 0}

        # Create phase branch
        branch = git_mgr.create_phase_branch(phase_num, f"phase-{phase_num}")

        # Load rules
        rules_engine.load_rules(project_path)

        # Get tasks for this phase
        tasks = self.db.get_tasks_by_phase(project_id, phase_num)
        if not tasks:
            logger.warning(f"No tasks for phase {phase_num}")
            return {"success": True, "tasks_completed": 0}

        # Build dependency graph via Kimi (or heuristic fallback)
        logger.info(
            f"Phase {phase_num}: classifying dependencies for {len(tasks)} tasks"
        )
        dep_graph = await self._classify_dependencies(tasks)
        logger.info(f"Phase {phase_num}: dep graph = {dep_graph}")

        completed_task_ids: set = set()
        failed_tasks: list = []
        task_results: dict = {}
        pending = {t["task_id"]: t for t in tasks}

        while pending:
            # Find tasks whose dependencies are all satisfied
            ready = [
                t for tid, t in pending.items()
                if all(dep in completed_task_ids for dep in dep_graph.get(tid, []))
            ]
            if not ready:
                # Deadlock: run the first pending task to unblock
                logger.warning(
                    f"Phase {phase_num}: dependency deadlock among "
                    f"{list(pending.keys())} — running first task to unblock"
                )
                ready = [next(iter(pending.values()))]

            logger.info(
                f"Phase {phase_num}: wave — running {len(ready)} task(s) in parallel: "
                f"{[t['task_id'] for t in ready]}"
            )

            # Execute this wave concurrently — semaphore limits concurrency (FER-AF-036)
            async def _guarded(task):
                async with self._task_semaphore:
                    return await self._execute_single_task(
                        task, project, project_path,
                        context_mgr, rules_engine, dac_tagger, git_mgr, on_progress,
                        learning_log=learning_log,
                    )

            wave_results = await asyncio.gather(
                *[_guarded(t) for t in ready],
                return_exceptions=True,
            )

            for task, result in zip(ready, wave_results):
                tid = task["task_id"]
                del pending[tid]
                if isinstance(result, Exception):
                    logger.error(f"Task {tid} raised exception: {result}")
                    failed_tasks.append({"task_id": tid, "error": str(result)})
                    task_results[tid] = {"success": False, "error": str(result)}
                elif result.get("success"):
                    completed_task_ids.add(tid)
                    task_results[tid] = result
                else:
                    failed_tasks.append({
                        "task_id": tid,
                        "error": result.get("error", "Unknown"),
                    })
                    task_results[tid] = result

        # Phase complete
        if failed_tasks:
            logger.warning(f"Phase {phase_num}: {len(failed_tasks)} failed tasks")

        # Kimi phase PR review
        kimi = self._get_worker("gatekeeper_review")
        if kimi:
            changed = git_mgr.get_changed_files("develop")
            await kimi.send_message(
                f"Review phase {phase_num} changes for merge to develop:\n"
                f"Files changed: {changed[:20]}\n"
                f"Tasks completed: {len(completed_task_ids)}/{len(tasks)}",
                system_prompt="Phase PR review"
            )

        # F: Conflict detection before merge (plan Decision #25)
        conflicts = git_mgr.check_conflicts("develop")
        if conflicts:
            logger.warning(
                f"Phase {phase_num}: {len(conflicts)} conflict(s) before merge: {conflicts}"
            )
            dac_tagger.tag(
                f"phase_{phase_num}_merge",
                "merge_conflict",
                f"Phase {phase_num}: {branch} → develop conflicts in: "
                f"{', '.join(conflicts[:10])}",
                source_step="phase_merge",
                project_id=project_id,
            )

        # FER-AF-040: Serialise merge_to_develop across concurrent phases
        async with self._merge_lock:
            git_mgr.merge_to_develop(branch, f"Phase {phase_num} complete")

        # Update project phase
        self.db.request_write("update", "projects", {
            "current_phase": phase_num,
            "_where": {"project_id": project["project_id"]},
        })

        return {
            "success": len(failed_tasks) == 0,
            "tasks_completed": len(completed_task_ids),
            "tasks_failed": len(failed_tasks),
            "task_results": task_results,
            "errors": [f["error"] for f in failed_tasks],
        }

    async def _run_e2e_tests(self, project_path: str,
                              on_progress: Callable = None) -> dict:
        """
        M: Auto-run E2E tests on the generated project (phase 4).
        Runs pytest on tests/ directory. Non-blocking; failure does not block UAT.
        Returns result summary dict.
        """
        if on_progress:
            await on_progress(4, "e2e", "running", "Running E2E tests...")

        tests_dir = Path(project_path) / "tests"
        if not tests_dir.exists() or not any(tests_dir.iterdir()):
            logger.warning("E2E: no tests/ directory or empty — skipping")
            if on_progress:
                await on_progress(4, "e2e", "skipped", "No tests found — skipping E2E")
            return {"success": True, "skipped": True, "reason": "No tests/ directory"}

        try:
            proc = await asyncio.create_subprocess_exec(
                "python", "-m", "pytest", "tests/", "-v", "--tb=short", "-q",
                cwd=str(project_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=300  # 5-min hard cap
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                logger.warning("E2E tests timed out (300s)")
                if on_progress:
                    await on_progress(4, "e2e", "timeout", "E2E tests timed out")
                return {"success": False, "error": "Tests timed out after 300s"}

            passed = proc.returncode == 0
            output_tail = stdout.decode(errors="replace")[-2000:]
            status = "passed" if passed else "failed"
            msg = f"E2E {status.upper()} (exit {proc.returncode})"
            logger.info(msg)
            if on_progress:
                await on_progress(4, "e2e", status, msg)
            return {
                "success": passed,
                "returncode": proc.returncode,
                "output_tail": output_tail,
            }

        except FileNotFoundError:
            # pytest not installed in project env — skip gracefully
            logger.warning("E2E: pytest not found — skipping")
            if on_progress:
                await on_progress(4, "e2e", "skipped", "pytest not found — skipping E2E")
            return {"success": True, "skipped": True, "reason": "pytest not found"}
        except Exception as e:
            logger.error(f"E2E test runner error: {e}")
            if on_progress:
                await on_progress(4, "e2e", "error", f"E2E error: {e}")
            return {"success": False, "error": str(e)}

    async def _generate_tasks_from_blueprint(
        self, project: dict, blueprint_content: str
    ) -> int:
        """
        Parse blueprint with LLM and create task rows in DB for each phase.
        Called once after blueprint approval. Returns count of tasks created.
        """
        import re as _re
        project_id = project["project_id"]
        project_type = project.get("project_type", "web")

        logger.info(f"Generating tasks from blueprint for {project_id}")

        worker = self._get_worker("task_planning_gsd")
        phases_data = None

        if worker:
            # FER-AF-029: Increase truncation limit and warn when triggered
            _bp_tg = blueprint_content
            if len(_bp_tg) > 15_000:
                logger.warning(
                    f"Blueprint truncated {len(_bp_tg)} → 15000 chars for task generation"
                )
                _bp_tg = _bp_tg[:15_000]
            prompt = (
                f"You are a senior software architect. Analyze this {project_type} "
                f"project blueprint and generate a concrete task breakdown.\n\n"
                f"BLUEPRINT:\n{_bp_tg}\n\n"
                f"Return ONLY valid JSON (no markdown, no explanation):\n"
                f'{{\n'
                f'  "phases": [\n'
                f'    {{\n'
                f'      "phase": 1,\n'
                f'      "name": "Backend",\n'
                f'      "tasks": [\n'
                f'        {{"module": "backend/database.py", "description": "Setup SQLite database with tables from db_schema.sql"}},\n'
                f'        {{"module": "backend/models.py", "description": "Pydantic models matching types.json"}},\n'
                f'        {{"module": "backend/routers/books.py", "description": "FastAPI CRUD endpoints"}},\n'
                f'        {{"module": "backend/main.py", "description": "FastAPI app entry point with CORS and routing"}}\n'
                f'      ]\n'
                f'    }},\n'
                f'    {{"phase": 2, "name": "Frontend", "tasks": ['
                f'{{"module": "frontend/src/App.tsx", "description": "React app root"}}'
                f']}},\n'
                f'    {{"phase": 3, "name": "Integration", "tasks": ['
                f'{{"module": "tests/test_api.py", "description": "API integration tests"}}'
                f']}}\n'
                f'  ]\n'
                f'}}\n\n'
                f"Generate 4-8 tasks per phase. Modules must be relative paths. "
                f"Descriptions must be specific and actionable based on the blueprint."
            )
            result = await worker.send_message(
                prompt,
                system_prompt=(
                    "Return ONLY valid JSON with the task breakdown. "
                    "No markdown fences, no extra text before or after JSON."
                ),
            )
            if result.get("success"):
                response = result["response"].strip()
                # Strip markdown code fences if present
                if "```" in response:
                    parts = response.split("```")
                    for part in parts:
                        part = part.strip()
                        if part.startswith("json"):
                            part = part[4:].strip()
                        if part.startswith("{"):
                            response = part
                            break
                try:
                    phases_data = json.loads(response)
                except json.JSONDecodeError:
                    # Try to extract JSON object from response
                    m = _re.search(r'\{[\s\S]*\}', response)
                    if m:
                        try:
                            phases_data = json.loads(m.group())
                        except json.JSONDecodeError:
                            logger.warning("Task LLM returned unparseable JSON — using fallback")
                    else:
                        logger.warning("Task LLM returned no JSON — using fallback")

        # Fallback: use sensible defaults if LLM failed
        if not phases_data or "phases" not in phases_data:
            logger.warning(f"Using fallback task definitions for {project_id}")
            phases_data = self._fallback_tasks(project)

        # Queue all task inserts
        tasks_created = 0
        for phase_info in phases_data.get("phases", []):
            phase_num = int(phase_info.get("phase", 1))
            for i, task_info in enumerate(phase_info.get("tasks", []), 1):
                task_id = f"{project_id}_p{phase_num}_t{i:02d}"
                module = task_info.get("module", f"phase{phase_num}/task{i}.py")
                description = task_info.get("description", f"Phase {phase_num} task {i}")
                try:
                    self.db.request_write("insert", "tasks", {
                        "task_id": task_id,
                        "project_id": project_id,
                        "phase": phase_num,
                        "module": module,
                        "description": description,
                        "status": "pending",
                    })
                    tasks_created += 1
                except RuntimeError as e:
                    logger.error(f"Failed to queue task {task_id}: {e}")

        # Poll until tasks are actually visible in DB (Watchdog drain is async).
        # FER-AF-037: Reduced from range(20) to range(10) — 10s is sufficient.
        for attempt in range(10):
            await asyncio.sleep(1)
            confirmed = self.db.get_tasks_by_phase(project_id, 1)
            if confirmed:
                logger.info(
                    f"Task generation confirmed in DB after {attempt + 1}s "
                    f"({len(confirmed)} phase-1 tasks visible)"
                )
                break
        else:
            logger.warning(
                f"Tasks not visible in DB after 10s — Watchdog may be lagging"
            )

        logger.info(f"Task generation complete: {tasks_created} tasks created for {project_id}")
        return tasks_created

    def _fallback_tasks(self, project: dict) -> dict:
        """Default task breakdown when LLM task planning fails or returns bad JSON."""
        return {
            "phases": [
                {
                    "phase": 1,
                    "name": "Backend",
                    "tasks": [
                        {"module": "backend/database.py",
                         "description": "Setup SQLite database with all tables from db_schema.sql contracts"},
                        {"module": "backend/models.py",
                         "description": "Pydantic request/response models matching types.json contracts"},
                        {"module": "backend/routers/books.py",
                         "description": "FastAPI CRUD: GET/POST /api/books, GET/PUT/DELETE /api/books/{id}"},
                        {"module": "backend/routers/search.py",
                         "description": "FastAPI search and filter: GET /api/books/search, GET /api/books/filter"},
                        {"module": "backend/auth.py",
                         "description": "JWT token creation, verification, and Bearer middleware"},
                        {"module": "backend/main.py",
                         "description": "FastAPI app entry: CORS, router registration, lifespan, health check"},
                    ],
                },
                {
                    "phase": 2,
                    "name": "Frontend",
                    "tasks": [
                        {"module": "frontend/src/api/client.ts",
                         "description": "Axios HTTP client with auth headers and error handling"},
                        {"module": "frontend/src/types/index.ts",
                         "description": "TypeScript types: Book, User, BookFilter matching types.json contracts"},
                        {"module": "frontend/src/components/BookCard.tsx",
                         "description": "Book card: title, author, genre, 1-5 star rating, status badge"},
                        {"module": "frontend/src/components/BookForm.tsx",
                         "description": "Add/Edit book form with validation: title, author, genre, rating, notes, status"},
                        {"module": "frontend/src/components/BookList.tsx",
                         "description": "Book list with search bar, genre/author filter dropdowns, pagination"},
                        {"module": "frontend/src/App.tsx",
                         "description": "React Router: /, /books, /books/:id routes with Tailwind layout"},
                    ],
                },
                {
                    "phase": 3,
                    "name": "Integration",
                    "tasks": [
                        {"module": "tests/test_books_api.py",
                         "description": "pytest: CRUD, search, filter, auth for books API endpoints"},
                        {"module": "tests/test_auth.py",
                         "description": "pytest: login, register, token refresh, protected route access"},
                        {"module": "requirements.txt",
                         "description": "Python deps: fastapi, uvicorn, sqlalchemy, pydantic, python-jose, passlib"},
                        {"module": "frontend/package.json",
                         "description": "Node deps: react, react-router-dom, axios, tailwindcss, typescript, vite"},
                    ],
                },
            ]
        }

    async def _classify_task(self, task: dict) -> str:
        """Kimi classifies task complexity (CALL #1)."""
        kimi = self._get_worker("gatekeeper_review")
        if not kimi:
            return "low"

        result = await kimi.send_message(
            f"Classify this task complexity as 'low' or 'high':\n"
            f"Module: {task['module']}\n"
            f"Description: {task['description'][:500]}",
            system_prompt="Classify task complexity: respond with ONLY 'low' or 'high'"
        )
        if result.get("success"):
            resp = result["response"].strip().lower()
            return "high" if "high" in resp else "low"
        return "low"

    async def _quality_gate(self, task: dict, code_output: dict,
                             project_path: str,
                             validator: ContractValidator = None) -> dict:
        """
        Quality gate review — logic-based verdict (Issue 5, R2).
        APPROVED only when issue_count == 0 AND dac_tags == [].
        Confidence score is telemetry only, never the decision gate.

        Kimi SPOF fix (Issue 3, R6): health-checks Kimi first;
        falls back to Gemini (architecture_audit role) if Kimi offline.
        """
        import re

        # --- Issue 3: Kimi health check → Gemini fallback ---
        gate_worker = self._get_worker("gatekeeper_review")
        gate_worker_name = self._get_worker_name("gatekeeper_review")

        if gate_worker:
            try:
                health = await gate_worker.check_health()
                if health in ("offline", "crashed"):
                    logger.warning(
                        f"Gatekeeper '{gate_worker_name}' is {health} — "
                        "falling back to Gemini (architecture_audit)"
                    )
                    gate_worker = self._get_worker("architecture_audit")
                    gate_worker_name = self._get_worker_name("architecture_audit")
            except Exception as _he:
                logger.warning(f"Health check failed for {gate_worker_name}: {_he}")

        if not gate_worker:
            logger.error("No gate worker available — REJECTING task (cannot auto-approve without gatekeeper)")
            return {
                "verdict": "REJECTED",
                "confidence": 0.0,
                "issues": ["No gate worker available; manual review required before approval"],
                "dac_tags": [],
                "by": "none",
            }

        context_mgr = ContextManager(str(self.working_dir), self.db)
        contracts = context_mgr.load_contracts(project_path)

        prompt = context_mgr.build_gate_prompt(
            task, code_output, contracts,
            validator_report={"valid": True, "mismatches": []} if not validator else {}
        )

        result = await gate_worker.send_message(prompt, system_prompt="Quality gate review")

        if result.get("success"):
            gate = {}
            parse_failed = False
            try:
                resp = result["response"]
                json_match = re.search(r'\{[\s\S]*\}', resp)
                if json_match:
                    gate = json.loads(json_match.group())
                else:
                    parse_failed = True
            except (json.JSONDecodeError, AttributeError):
                parse_failed = True

            # --- FER-AF-044: If gate LLM returned no parseable JSON, REJECT ---
            # An empty/unstructured response must never silently APPROVE a task.
            if not gate or parse_failed:
                logger.warning(
                    f"Gate worker '{gate_worker_name}' returned no parseable JSON — "
                    f"REJECTING task (raw response snippet: {result.get('response', '')[:200]!r})"
                )
                return {
                    "verdict": "REJECTED",
                    "confidence": 0.0,
                    "issues": ["Gate LLM returned no parseable JSON — manual review required"],
                    "dac_tags": [],
                    "by": gate_worker_name,
                }

            # --- Issue 5: Logic-based verdict, not confidence-based ---
            issues = gate.get("issues", [])
            dac_tags = gate.get("dac_tags", [])

            # Normalise: empty string entries don't count as real issues
            real_issues = [i for i in issues if i and str(i).strip()]
            real_tags = [t for t in dac_tags if t and str(t).strip()]

            if real_issues or real_tags:
                verdict = "REJECTED"
            else:
                # Also respect explicit REJECTED from the LLM even without issues listed
                raw_verdict = gate.get("verdict", "").upper()
                verdict = "REJECTED" if raw_verdict == "REJECTED" else "APPROVED"

            return {
                "verdict": verdict,
                "confidence": gate.get("confidence", 0.5),  # telemetry only
                "issues": real_issues,
                "dac_tags": real_tags,
                "by": gate_worker_name,
            }

        # Worker call failed — REJECT, never silently approve
        logger.error(
            f"Gate worker '{gate_worker_name}' call failed — REJECTING task "
            f"(result: {result})"
        )
        return {
            "verdict": "REJECTED",
            "confidence": 0.0,
            "issues": [f"Gate worker '{gate_worker_name}' call failed; manual review required"],
            "dac_tags": [],
            "by": gate_worker_name,
        }

    async def approve_blueprint(self, project_id: str) -> dict:
        """Called from dashboard when human approves blueprint."""
        project = self.db.get_project(project_id)
        if not project:
            return {"success": False, "error": "Project not found"}

        # Mark blueprint as approved — use wait to ensure DB write completes
        # before execute_project is re-launched (which reads approved_by to skip regen).
        latest = self.db.get_latest_blueprint(project_id)
        if latest:
            # P1 FIX: Use fire-and-forget request_write — not request_write_and_wait.
            # request_write_and_wait blocks until Watchdog drains the queue; in tests
            # (and any environment without a running Watchdog) it times out after 10s.
            self.db.request_write("update", "blueprint_revisions", {
                "approved_by": "HUMAN",
                "approved_at": datetime.now().isoformat(),
                "_where": {"project_id": project_id, "version": latest["version"]},
            })

        # Lock contracts
        project_path = project.get("project_path", "")
        if project_path:
            contract_gen = ContractGenerator(project_path)
            contract_gen.lock_contracts()

        logger.info(f"Blueprint approved for project {project_id}")
        return {"success": True, "message": "Blueprint approved, contracts locked"}

    async def approve_uat(self, project_id: str) -> dict:
        """Called from dashboard when human approves UAT."""
        project = self.db.get_project(project_id)
        if not project:
            return {"success": False, "error": "Project not found"}

        # FER-AF-010: Block UAT approval when E2E tests failed
        if project.get("e2e_failed"):
            return {
                "success": False,
                "error": "UAT blocked: E2E tests failed. Fix tests before approving.",
            }

        project_path = project.get("project_path", "")
        git_mgr = GitManager(project_path)

        # Phase 5: merge to main
        git_mgr.merge_to_main("Production release — UAT approved")
        git_mgr.tag_version("v1.0.0", "Production release")

        # Update project
        self.db.request_write("update", "projects", {
            "status": "completed",
            "current_phase": 5,
            "_where": {"project_id": project_id},
        })

        logger.info(f"UAT approved, production deployed for {project_id}")
        return {"success": True, "message": "Production deployed, v1.0.0 tagged"}


BLUEPRINT_SYSTEM = (
    "You are a senior architect. Generate comprehensive blueprints with: "
    "architecture, DB schema, APIs, frontend, 5 phases, tasks + acceptance criteria, "
    "security, testing. Output structured markdown."
)

TDD_SYSTEM = (
    "You are a TDD engineer. Follow 13-step protocol precisely: "
    "AC, TDE-RED, TDE-GREEN, BC, BF, SEA, DS, OA, VB, GIT, CL, CCP, AD. "
    "Report each step."
)
