#!/usr/bin/env python3
"""
Model-to-Model Conversation Script
===================================
Makes qwen and deepseek talk to each other for N rounds.
All messages flow through POST /api/chat/direct so Phi3 auto-summarizes into DoC.
Free local ollama models — zero API cost.
"""

import requests
import time
import sys
import json

BASE = "http://127.0.0.1:8420"
TOTAL_ROUNDS = 50  # 50 rounds = 100 messages (50 qwen + 50 deepseek)

SEED_TOPIC = (
    "Let's design a React todo app together. It needs: "
    "1) User authentication with JWT, "
    "2) Drag-and-drop task reordering, "
    "3) Dark mode toggle, "
    "4) Categories/tags for tasks, "
    "5) Due dates with reminders. "
    "Start by proposing the project structure and tech stack. "
    "Be specific about libraries, folder layout, and architecture decisions."
)

def chat(worker: str, message: str) -> str:
    """Send a message to a worker via the direct chat endpoint."""
    try:
        resp = requests.post(
            f"{BASE}/api/chat/direct",
            json={"worker": worker, "message": message},
            timeout=120
        )
        data = resp.json()
        return data.get("response", data.get("error", "no response"))
    except Exception as e:
        return f"[ERROR: {e}]"


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else TOTAL_ROUNDS
    print(f"=== Model Conversation: qwen <-> deepseek ({rounds} rounds = {rounds*2} messages) ===")
    print(f"Topic: React Todo App\n")

    # Round 1: seed qwen with the topic
    print(f"[1/{rounds}] qwen <- SEED TOPIC")
    qwen_reply = chat("qwen", SEED_TOPIC)
    print(f"  qwen: {qwen_reply[:150]}...")
    print()

    last_reply = qwen_reply
    speaker = "deepseek"  # deepseek responds to qwen first

    for i in range(2, rounds + 1):
        # Alternate: deepseek responds to qwen, then qwen responds to deepseek
        prompt = (
            f"Your colleague ({('qwen' if speaker == 'deepseek' else 'deepseek')}) said:\n\n"
            f"{last_reply}\n\n"
            f"Continue the technical discussion. Build on their points, "
            f"suggest improvements, or drill into implementation details. "
            f"Be specific with code examples when relevant."
        )

        print(f"[{i}/{rounds}] {speaker} <- responding...")
        reply = chat(speaker, prompt)
        print(f"  {speaker}: {reply[:150]}...")
        print()

        last_reply = reply
        speaker = "qwen" if speaker == "deepseek" else "deepseek"

        # Small delay to let Phi3 process
        time.sleep(1)

    print(f"\n=== Done! {rounds} rounds = {rounds * 2} messages sent through chat pipeline ===")
    print("Phi3 should have auto-summarized these into DoC entries.")
    print("Check: SELECT COUNT(*) FROM context_summaries WHERE instance_name='phi3-orchestrator';")


if __name__ == "__main__":
    main()
