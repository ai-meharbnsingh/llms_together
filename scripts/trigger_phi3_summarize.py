#!/usr/bin/env python3
"""
Trigger Phi3 to build a comprehensive DoC from ALL chat history.
Runs standalone (server must be stopped to avoid DB conflicts).
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import aiohttp
from orchestration.database import WatchdogDB, ReadOnlyDB

FACTORY_STATE = Path(os.path.expanduser("~/working/autonomous_factory/factory_state"))
DB_PATH = str(FACTORY_STATE / "factory.db")
HISTORY_FILE = Path(__file__).parent.parent / "factory_state" / "chat_history.json"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "phi3:mini"


async def call_phi3(prompt: str, max_tokens: int = 2048) -> str:
    """Call Phi3 via Ollama."""
    async with aiohttp.ClientSession() as session:
        async with session.post(OLLAMA_URL, json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": max_tokens}
        }, timeout=aiohttp.ClientTimeout(total=120)) as r:
            if r.status == 200:
                data = await r.json()
                return data.get("response", "").strip()
            return f"[Error: HTTP {r.status}]"


async def main():
    # Load chat history
    if not HISTORY_FILE.exists():
        print(f"No chat history at {HISTORY_FILE}")
        return

    history = json.loads(HISTORY_FILE.read_text())
    print(f"Loaded {len(history)} messages from chat_history.json")

    # Build conversation digest — group user/assistant pairs
    digest_lines = []
    for i, msg in enumerate(history):
        if msg.get("role") == "user":
            worker = msg.get("worker", "?")
            mode = msg.get("mode", "?")
            content = msg.get("content", "")[:300]
            # Find matching assistant reply
            reply = ""
            for j in range(i + 1, min(i + 3, len(history))):
                if history[j].get("role") == "assistant":
                    reply = history[j].get("content", "")[:300]
                    break
            digest_lines.append(f"[{mode}/{worker}] User: {content}")
            if reply:
                digest_lines.append(f"  -> AI: {reply}")

    digest = "\n".join(digest_lines)
    print(f"\nBuilt digest: {len(digest_lines)} lines, {len(digest)} chars")

    # Step 1: Ask Phi3 to create a comprehensive summary
    print("\n--- Step 1: Phi3 summarizing all chats ---")
    summary_prompt = f"""You are a project context summarizer. Analyze ALL these conversations and create a comprehensive Document of Context (DoC).

CONVERSATIONS:
{digest[:6000]}

Create a structured summary in this EXACT format:

## DECISIONS MADE
- [Each technical decision with rationale]

## REQUIREMENTS CAPTURED
- [Each requirement discussed]

## CURRENT STATE
- Project: [what's being built]
- Tech Stack: [technologies chosen]
- Architecture: [key design decisions]

## KEY CONTEXT
- [Important context for future conversations]

## TOPICS DISCUSSED
- [Major topics covered across all chats]

## ACTION ITEMS
- [ ] [Pending items]

Be thorough. This DoC will be used to restore context after a server restart."""

    doc_text = await call_phi3(summary_prompt, max_tokens=3000)
    print(f"Phi3 generated DoC: {len(doc_text)} chars, ~{len(doc_text)//4} tokens")
    print(f"\nPreview:\n{doc_text[:500]}...\n")

    if len(doc_text) < 100:
        print("DoC too short! Phi3 may have failed. Aborting.")
        return

    # Step 2: Collect all chat IDs
    chat_ids = [f"batch_{i}" for i in range(len(digest_lines) // 2)]
    token_count = len(doc_text) // 4

    # Step 3: Write to DB
    print("--- Step 2: Writing DoC to database ---")
    db = WatchdogDB(DB_PATH)
    db.save_context_summary(
        instance_name="phi3-orchestrator",
        chat_ids=chat_ids,
        summary_text=doc_text,
        keywords=["react", "todo", "auth", "drag-drop", "dark-mode", "architecture"],
        token_count=token_count,
        compression_ratio=round(len(doc_text) / max(len(digest), 1), 3),
    )
    print(f"Saved DoC: {token_count} tokens, covering {len(chat_ids)} chats")

    # Step 3: Verify
    print("\n--- Step 3: Verifying ---")
    read_db = ReadOnlyDB(DB_PATH)
    doc = read_db.get_doc("phi3-orchestrator")
    if doc:
        print(f"DoC verified in DB: {doc['token_count']} tokens")
        print(f"Summary preview: {doc['summary_text'][:300]}...")
    else:
        print("ERROR: DoC not found after write!")

    print("\nDone! Restart the server to test DoC recovery.")


if __name__ == "__main__":
    asyncio.run(main())
