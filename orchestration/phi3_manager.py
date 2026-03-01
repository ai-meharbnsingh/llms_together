"""
Phi3 Manager — Parallel summarization agents.
One per worker + orchestrator. Writes via message bus → Watchdog.
Orchestrator's scribe also maintains a rolling Document of Context (DoC).
"""

import asyncio
import json
import logging
import uuid
from typing import Dict, Optional

import aiohttp

from orchestration.database import ReadOnlyDB

logger = logging.getLogger("factory.phi3")

# ─── DoC Templates ───

DOC_INITIAL_TEMPLATE = """## DECISIONS MADE
(none yet)

## REQUIREMENTS CAPTURED
(none yet)

## CURRENT STATE
- Project: Not started
- Phase: 0
- Active Tasks: None

## KEY CONTEXT
(none yet)

## ACTION ITEMS
(none yet)

## SESSION HISTORY
(none yet)
"""

DOC_UPDATE_PROMPT = """You are a context document maintainer. Update the Document of Context (DoC) by merging new information into existing sections. Do NOT rewrite from scratch.

CURRENT DoC:
{current_doc}

NEW CHAT EXCHANGE:
USER: {user_query}
AI SUMMARY: {summary}
DECISIONS: {decisions}
KEYWORDS: {keywords}
CHAT ID: {chat_id}

RULES:
- Merge new info into existing sections. Keep unchanged sections as-is.
- Remove superseded decisions and completed action items.
- Keep total under 3000 words.
- Output ONLY the updated DoC in this exact format:

## DECISIONS MADE
- [Decision]: [Rationale] (Chat: chat_id)

## REQUIREMENTS CAPTURED
- [Requirement description]

## CURRENT STATE
- Project: [status]
- Phase: [current phase]
- Active Tasks: [summary]

## KEY CONTEXT
- [Important context that affects future decisions]

## ACTION ITEMS
- [ ] [Pending action]
- [x] [Completed action]

## SESSION HISTORY
- [One-line summary per major exchange, newest first, max 20 entries]
"""


class Phi3Instance:
    """Single Phi3 summarizer paired to a parent worker."""

    def __init__(self, parent_worker: str, read_db: ReadOnlyDB,
                 api_base: str = "http://localhost:11434", model: str = "phi3:mini"):
        self.parent = parent_worker
        self.name = f"phi3-{parent_worker}"
        self.db = read_db
        self.db.set_requester(self.name)
        self.api_base = api_base
        self.model = model
        self.alive = True
        self._session = None
        self._queue = asyncio.Queue(maxsize=50)
        self._task = None

    async def start(self):
        self._task = asyncio.create_task(self._loop())
        logger.info(f"{self.name}: Started")

    async def stop(self):
        self.alive = False
        if self._task:
            self._task.cancel()
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def queue_summary(self, user_query: str, llm_response: str,
                            session_id: str, persist_full: bool = False, **kw):
        """Queue a chat pair for summarization.
        persist_full=True stores full chat + updates DoC (orchestrator only).
        """
        chat_id = f"chat_{uuid.uuid4().hex[:12]}"
        req = {"chat_id": chat_id, "session_id": session_id,
               "user_query": user_query, "llm_response": llm_response,
               "persist_full": persist_full, **kw}
        try:
            self._queue.put_nowait(req)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(req)
            except Exception:
                pass
        return chat_id

    async def _loop(self):
        while self.alive:
            try:
                req = await asyncio.wait_for(self._queue.get(), timeout=5)
                await self._summarize(req)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"{self.name}: {e}")
                await asyncio.sleep(1)

    async def _summarize(self, req: dict):
        persist_full = req.get("persist_full", False)

        prompt = (
            f"Summarize concisely:\n"
            f"USER: {req['user_query'][:500]}\n"
            f"AI: {req['llm_response'][:1000]}\n\n"
            f"JSON: {{\"summary\":..., \"decisions\":[...], \"keywords\":[...]}}"
        )
        try:
            s = await self._get_session()
            async with s.post(f"{self.api_base}/api/generate",
                              json={"model": self.model, "prompt": prompt,
                                    "stream": False,
                                    "options": {"temperature": 0.1, "num_predict": 256}},
                              timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    data = await r.json()
                    txt = data.get("response", "")
                    try:
                        parsed = json.loads(txt)
                    except json.JSONDecodeError:
                        parsed = {"summary": txt[:200], "decisions": [], "keywords": []}

                    # Build write params
                    write_params = {
                        "chat_id": req["chat_id"],
                        "session_id": req["session_id"],
                        "instance_name": self.name,
                        "parent_worker": self.parent,
                        "user_query": req["user_query"] if persist_full else req["user_query"][:500],
                        "llm_response_summary": parsed.get("summary", ""),
                        "keywords": json.dumps(parsed.get("keywords", [])),
                        "decisions_made": json.dumps(parsed.get("decisions", [])),
                        "project_id": req.get("project_id"),
                        "phase": req.get("phase"),
                        "task_id": req.get("task_id"),
                    }

                    # Full persistence: store complete response (orchestrator only)
                    if persist_full:
                        write_params["full_llm_response"] = req["llm_response"]

                    # Write via bus → Watchdog
                    self.db.request_write("insert", "chat_summaries", write_params)

                    # Update DoC after writing chat (orchestrator only)
                    if persist_full:
                        await self._update_doc(req, parsed)

        except Exception as e:
            logger.warning(f"{self.name}: Summarize failed: {e}")

    async def _update_doc(self, req: dict, parsed_summary: dict):
        """Update the rolling Document of Context after each orchestrator chat pair."""
        try:
            # 1. Get current DoC
            current = self.db.get_context_summary(self.name)
            doc_text = current["summary_text"] if current else DOC_INITIAL_TEMPLATE

            # 2. Build update prompt
            prompt = DOC_UPDATE_PROMPT.format(
                current_doc=doc_text,
                user_query=req["user_query"][:500],
                summary=parsed_summary.get("summary", ""),
                decisions=json.dumps(parsed_summary.get("decisions", [])),
                keywords=json.dumps(parsed_summary.get("keywords", [])),
                chat_id=req["chat_id"],
            )

            # 3. Call Phi3 to produce updated DoC
            s = await self._get_session()
            async with s.post(f"{self.api_base}/api/generate",
                              json={"model": self.model, "prompt": prompt,
                                    "stream": False,
                                    "options": {"temperature": 0.1, "num_predict": 2048}},
                              timeout=aiohttp.ClientTimeout(total=45)) as r:
                if r.status == 200:
                    data = await r.json()
                    updated_doc = data.get("response", "").strip()

                    if not updated_doc or len(updated_doc) < 50:
                        logger.warning(f"{self.name}: DoC update too short, skipping")
                        return

                    # 4. Collect all chat_ids covered by this DoC version
                    existing_ids = []
                    if current and current.get("original_chat_ids"):
                        try:
                            existing_ids = json.loads(current["original_chat_ids"])
                        except (json.JSONDecodeError, TypeError):
                            existing_ids = []
                    existing_ids.append(req["chat_id"])

                    # 5. Token/compression stats
                    token_count = len(updated_doc) // 4
                    original_len = max(len(doc_text) + len(req.get("llm_response", "")[:1000]), 1)
                    compression_ratio = round(len(updated_doc) / original_len, 3)

                    # 6. Write new DoC row via message bus (append-only)
                    self.db.request_write("insert", "context_summaries", {
                        "instance_name": self.name,
                        "original_chat_ids": json.dumps(existing_ids),
                        "summary_text": updated_doc,
                        "keywords": json.dumps(parsed_summary.get("keywords", [])),
                        "token_count": token_count,
                        "compression_ratio": compression_ratio,
                    })

                    logger.info(f"{self.name}: DoC updated ({token_count} tokens, "
                                f"{len(existing_ids)} chats covered)")

        except Exception as e:
            logger.warning(f"{self.name}: DoC update failed: {e}")


class Phi3Manager:
    """Manages all Phi3 instances."""

    def __init__(self, config: dict, read_db: ReadOnlyDB):
        pc = config.get("workers", {}).get("phi3", {})
        self.api_base = pc.get("api_base", "http://localhost:11434")
        self.model = pc.get("model", "phi3:mini")
        self.read_db = read_db
        self.instances: Dict[str, Phi3Instance] = {}

    async def start_all(self, worker_names: list):
        for n in worker_names:
            inst = Phi3Instance(n, self.read_db, self.api_base, self.model)
            await inst.start()
            self.instances[n] = inst

    async def stop_all(self):
        for inst in self.instances.values():
            await inst.stop()
        self.instances.clear()

    def get(self, parent: str) -> Optional[Phi3Instance]:
        return self.instances.get(parent)
