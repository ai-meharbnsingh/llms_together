# Autonomous Factory - Multi-LLM Orchestration Engine

A production-grade system that orchestrates **multiple LLM models** (local + cloud) as specialized workers under a single Watchdog supervisor. Instead of relying on one model, the factory assigns roles to different models based on their strengths — DeepSeek for complex code, Qwen for quick tasks, Claude for testing, Kimi for reviews, Gemini for architecture.

**Dashboard:** Real-time control panel at `http://127.0.0.1:8420`

## Architecture

```
                    +------------------+
                    |   WATCHDOG (PID 1)  |  Sole DB writer, health monitor
                    +--------+---------+
                             |
          +------------------+------------------+
          |                  |                  |
   +------+------+   +------+------+   +-------+-------+
   | Role Router  |   | Orchestrator |   | Phi3 Scribe   |
   | 10 roles     |   | Chat + Tasks |   | Summarizer    |
   | hot-swappable|   | DoC memory   |   | (local ollama)|
   +------+------+   +------+------+   +---------------+
          |                  |
   +------+------+   +------+------+
   |   WORKERS   |   |  Dashboard   |
   | deepseek    |   |  :8420       |
   | qwen        |   |  Chat Panel  |
   | claude      |   |  Role Config |
   | kimi        |   |  Worker Status|
   | gemini      |   +--------------+
   +-------------+
```

## Key Features

- **Multi-LLM orchestration** — 5 workers (local Ollama + cloud CLIs) assigned to 10 specialized roles
- **Hot-swappable roles** — reassign any role to any worker from the dashboard, no restart needed
- **Watchdog supervisor** — sole DB writer, health monitoring, crash recovery, ghost process reaping
- **Document of Context (DoC)** — Phi3 summarizes every chat into a rolling context document for crash recovery
- **Warm/Cold memory** — recent chats in warm memory, overflow archived to cold storage with search API
- **Chat sessions** — named, switchable, archivable tabs in the dashboard
- **Project scoping** — conversations and context scoped per project
- **ReadOnly pattern** — all components read from DB, only Watchdog writes (via message bus)

## Quick Start

### Prerequisites

You need **at least one** of these:

**Option A: Local models via Ollama (free)**
```bash
# Install Ollama
brew install ollama   # macOS
# or: curl -fsSL https://ollama.ai/install.sh | sh   # Linux

# Start Ollama and pull models
ollama serve
ollama pull deepseek-coder-v2:16b
ollama pull qwen2.5-coder:7b
ollama pull phi3:mini    # for summarization
```

**Option B: Cloud CLI tools (requires accounts)**
```bash
# Claude (Anthropic)
npm install -g @anthropic-ai/claude-code
claude login

# Kimi (Moonshot)
npm install -g @anthropic-ai/kimi
kimi login

# Gemini (Google)
npm install -g @anthropic-ai/gemini
gemini login
```

You can mix and match — run with just Ollama models, just cloud CLIs, or any combination.

### Install and Run

```bash
git clone https://github.com/ai-meharbnsingh/llms_together.git
cd llms_together

# Python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Playwright (for E2E tests only)
npm install

# Start the factory
python main.py
```

Open **http://127.0.0.1:8420** in your browser.

## Worker Configuration

Edit `config/factory_config.json` to configure which workers you have:

```json
{
  "workers": {
    "deepseek": {
      "type": "local_ollama",
      "model": "deepseek-coder-v2:16b",
      "api_base": "http://localhost:11434"
    },
    "qwen": {
      "type": "local_ollama",
      "model": "qwen2.5-coder:7b",
      "api_base": "http://localhost:11434"
    },
    "claude": {
      "type": "cli_login",
      "cli_command": "claude"
    }
  }
}
```

Remove any worker you don't have. The system boots with whatever is available — unavailable workers show warnings but don't block startup.

### Role Assignments

Roles map **what needs to be done** to **who does it**:

| Role | Default Primary | Fallback |
|------|----------------|----------|
| code_generation_simple | qwen | deepseek |
| code_generation_complex | deepseek | qwen |
| tdd_testing | claude | gemini |
| gatekeeper_review | kimi | claude |
| architecture_audit | gemini | claude |
| task_planning_gsd | claude | kimi |
| blueprint_generation | claude | gemini |
| summarization | deepseek | — |
| frontend_design | claude | gemini |
| project_classification | kimi | claude |

Roles are **hot-swappable** from the dashboard UI — no restart needed.

## Chat Modes

The dashboard chat panel supports 3 modes:

- **Orchestrator** — messages routed through the master orchestrator with full context (DoC + history)
- **Direct** — talk to a specific worker directly, bypassing the role router
- **Project** — project-scoped conversation with context filtering

## Project Structure

```
autonomous_factory/
  main.py                    # Entry point, boot sequence
  config/
    factory_config.json      # Worker + role configuration
  orchestration/
    master_watchdog.py       # PID 1 supervisor, DB writer
    master_orchestrator.py   # Chat routing, context builder
    role_router.py           # Role -> worker mapping
    phi3_manager.py          # Phi3 summarization scribes
    database.py              # WatchdogDB + ReadOnlyDB
    state_persistence.py     # Crash recovery state
    process_reaper.py        # Ghost process cleanup
  workers/
    adapters.py              # Ollama + CLI worker adapters
  dashboard/
    dashboard_server.py      # aiohttp REST + WebSocket server
  tests/                     # PyTest + Playwright E2E tests
  scripts/                   # Utility scripts
```

## Tests

```bash
# Run all tests
pytest -v

# Run with coverage
pytest --cov=orchestration --cov=workers -v
```

## License

MIT License - see [LICENSE](LICENSE)
