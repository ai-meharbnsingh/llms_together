# 🏎️ TEST PILOT MANUAL: AUTONOMOUS FACTORY

> **Project**: Autonomous Factory v1.5
> **Protocol**: Level-5 Behavioral Verification
> **Status**: ACTIVE TESTING PHASE

This document tracks the specific "Stress Tests" and verification steps required to ensure the **Autonomous Factory** (The "Most Advanced Car") remains stable under high-load autonomous development.

---

## 🛠️ TIER 1: THE ENGINE ROOM (CORE SYSTEMS)

### 1.1 Watchdog PID 1 Integrity
- [ ] **Test**: Manually `kill -9` the Orchestrator while a task is active.
- [ ] **Verification**: Watchdog detects heartbeat loss within 30s and triggers `recover.sh`.
- [ ] **Success Metric**: System resumes at the exact TDD step (e.g., `RED` or `GREEN`) without data loss.

### 1.2 The "Black Box" (DB Write Integrity)
- [ ] **Test**: Launch 10 parallel `Ollama` worker calls (Qwen + DeepSeek).
- [ ] **Verification**: The `asyncio.Queue` message bus correctly orders and executes 100+ telemetry writes without "Database is locked" errors.
- [ ] **Success Metric**: `SELECT COUNT(*) FROM chat_summaries` matches the number of sent messages.

---

## ✈️ TIER 2: THE PILOT (ORCHESTRATOR & TDD)

### 2.1 13-Step TDD Validation
- [ ] **Test**: Execute a complex Python backend task (e.g., "Add JWT Auth").
- [ ] **Verification**: Pipeline executes sequentially: `AC` → `RED` → `GREEN` → `BC` → `BF` → `SEA` → `DS` → `OA` → `VB` → `GIT` → `CL` → `CCP` → `AD`.
- [ ] **Success Metric**: `git_manager` creates an atomic commit with the `task_id` in the message.

### 2.2 Fast-Track Switching (Pilot Intelligence)
- [ ] **Test**: Provide a CSS-only task ("Change dashboard theme to Dark Mode").
- [ ] **Verification**: Orchestrator detects `FAST_TRACK_PATTERN` and executes only 5 steps (`AC`, `GREEN`, `OA`, `GIT`, `AD`).
- [ ] **Success Metric**: Task completes in < 60 seconds.

---

## 📡 TIER 3: THE SCRIBE (PHI3 & DOC RECOVERY)

### 3.1 Document of Context (DoC) Synthesis
- [ ] **Test**: Conduct a 20-message technical discussion using `scripts/model_conversation.py`.
- [ ] **Verification**: `phi3-orchestrator` updates the `context_summaries` table after every 3-5 messages.
- [ ] **Success Metric**: A new session successfully "recalls" the previous decisions via the `Recall API`.

### 3.2 Token Compression Stability
- [ ] **Test**: Feed a 5000-word requirements document into the blueprint phase.
- [ ] **Verification**: Phi3 compresses the DoC to < 2000 tokens while maintaining all "DECISIONS MADE."
- [ ] **Success Metric**: `compression_ratio` is recorded in the DB as < 0.5.

---

## 💀 TIER 4: BRUTAL STRESS TESTS (THE "CRASH" PATH)

### 4.1 The "Ghost Hunter" Sweep
- [ ] **Test**: Force-stop the server during a heavy `npm install` or `git clone` operation.
- [ ] **Verification**: Run `ps aux | grep autonomous_factory` immediately after stop.
- [ ] **Success Metric**: Zero orphan/zombie processes remain. `ProcessReaper` has reaped all children.

### 4.2 Silent Error Detection (SEA Step)
- [ ] **Test**: Inject a "silent" race condition (e.g., `asyncio.sleep` without `await`) into a worker task.
- [ ] **Verification**: The `SEA` (Silent Error Analysis) step in the TDD pipeline flags this as a `HAL` or `DOM` DaC tag.
- [ ] **Success Metric**: A `dac_tag` entry is created in the DB for the task.

---

## 📊 TELEMETRY COMMANDS FOR TESTING

| Command | Description |
|:---|:---|
| `tail -f factory_state/logs/factory.log` | Real-time "Engine" diagnostics |
| `sqlite3 factory_state/factory.db "SELECT * FROM dac_tags;"` | View all failure signals |
| `ps aux --forest` | Visual process tree for ghost detection |
| `python3 scripts/trigger_phi3_summarize.py` | Force a manual context rebuild |

---

## 📝 TEST LOG NOTES (GEMINI)

*   **Mar 01**: Initial "Advanced Car" Audit completed.
*   **Mar 01**: Identified "God Object" congestion in `MasterOrchestrator`. High priority to monitor performance during Phase 3-4 (Build).
*   **Mar 01**: Confirmed `except: pass` in Watchdog is a blind spot. Added Tier 1.1 test to verify recovery despite this blindness.
