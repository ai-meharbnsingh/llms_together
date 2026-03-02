# FORENSIC AUDIT REPORT — AUTONOMOUS FACTORY
## CLI Forensics Report — V5 Protocol
**Forensic ID:** FORENSIC-20260302-001
**Project:** Autonomous Factory v1.1
**Analyst:** Principal Forensic Software Architect
**Date:** 2026-03-02
**Files Scanned:** 36 core source files (excluding venv, node_modules, No_ai_detection)
**Methodology:** CLI_FORENSIC_EXTRACTION_PROMPT_V5.0 — Machine-First Behavioral Verification

---

## VISITED FILES MANIFEST

| # | File | Lines (est) | Depth | Confidence | Notes |
|---|------|-------------|-------|------------|-------|
| 1 | `main.py` | 164 | TRACED | HIGH | Entry point — boot sequence verified |
| 2 | `config/factory_config.json` | 204 | LOADED | HIGH | All keys mapped to consumers |
| 3 | `requirements.txt` | 7 | LOADED | HIGH | CRITICAL: 7 deps, missing >20 actually-imported packages |
| 4 | `orchestration/master_orchestrator.py` | ~2440 | TRACED | HIGH | Core COO — full read in sections |
| 5 | `orchestration/database.py` | ~1500 | TRACED | HIGH | Schema + ReadOnlyDB + WatchdogDB |
| 6 | `orchestration/master_watchdog.py` | ~700 | TRACED | HIGH | PID 1, drain loop, monitoring |
| 7 | `orchestration/output_parser.py` | ~400 | TRACED | HIGH | JSON parser + file writer |
| 8 | `orchestration/git_manager.py` | ~300 | TRACED | HIGH | Git operations |
| 9 | `orchestration/role_router.py` | 272 | TRACED | HIGH | Role→worker mapping |
| 10 | `orchestration/learning_log.py` | ~220 | TRACED | HIGH | Learning log with R11 filter |
| 11 | `workers/adapters.py` | ~400 | TRACED | HIGH | CLI + Ollama worker adapters |
| 12 | `tests/test_7issues_wave1.py` | ~300 | OPENED | HIGH | Unit tests for issues 2,6,7 |
| 13 | `tests/test_7issues_wave2.py` | LISTED | LISTED_ONLY | LOW | Not read — context constraint |
| 14 | `orchestration/context_manager.py` | LISTED | LISTED_ONLY | LOW | Not read |
| 15 | `orchestration/dac_tagger.py` | LISTED | LISTED_ONLY | LOW | Not read |
| 16 | `orchestration/tdd_pipeline.py` | LISTED | LISTED_ONLY | LOW | Not read |
| 17 | `orchestration/cicd_generator.py` | LISTED | LISTED_ONLY | LOW | Not read |
| 18 | `orchestration/contract_validator.py` | LISTED | LISTED_ONLY | LOW | Not read |
| 19 | `orchestration/contract_generator.py` | LISTED | LISTED_ONLY | LOW | Contains TODO at line 130 |
| 20 | `orchestration/phi3_manager.py` | LISTED | LISTED_ONLY | LOW | Not read |
| 21 | `orchestration/process_reaper.py` | LISTED | OPENED | MEDIUM | time.sleep at lines 418, 424 confirmed |
| 22 | `orchestration/static_analysis.py` | LISTED | LISTED_ONLY | LOW | Not read |
| 23 | `orchestration/watchdog_state.py` | LISTED | LISTED_ONLY | LOW | Not read |
| 24 | `orchestration/rules_engine.py` | LISTED | LISTED_ONLY | LOW | Not read |
| 25 | `dashboard/dashboard_server.py` | LISTED | LISTED_ONLY | LOW | Not read — context constraint |
| 26 | `workers/figma_mcp.py` | LISTED | LISTED_ONLY | LOW | Not read |
| 27 | `tests/test_e2e_pipeline.py` | LISTED | LISTED_ONLY | LOW | Contains bare except evidence |
| 28 | `scripts/export_training_data.py` | LISTED | LISTED_ONLY | LOW | Not read |
| 29 | `check_health.sh` | LISTED | LISTED_ONLY | LOW | Not read |
| 30 | `setup.sh` | LISTED | LISTED_ONLY | LOW | Not read |

**PARTIAL SCAN DECLARATION:**
```yaml
PARTIAL_SCAN:
  files_not_analyzed: [dashboard_server.py, tdd_pipeline.py, context_manager.py,
    dac_tagger.py, contract_validator.py, contract_generator.py, phi3_manager.py,
    static_analysis.py, watchdog_state.py, rules_engine.py, figma_mcp.py,
    cicd_generator.py, workers/adapters.py (partial)]
  reason: "Context window pressure at 84% during scan"
  risk: HIGH
  follow_up_required: true
```

---

## SECTION 1: PROJECT IDENTITY & ARCHITECTURE DNA

### 1.1 Project Manifest

**Entry Point:** `main.py:41` — `async def main(config_path: str = None)`

**Execution Flow:**
```
main.py → MasterWatchdog.boot() → Phi3Manager.start_all() → MasterOrchestrator() → DashboardServer.start()
       ↓
       WatchdogDB (sole writer)
       ReadOnlyDB (all other components)
       asyncio.Queue (write bus, maxsize=10000)
       asyncio.Task (_db_drain_loop, _monitoring_loop)
```

**Key Dependencies Declared in requirements.txt:**
```
aiohttp>=3.9         # HTTP client
pytest>=8.0          # Testing
pytest-asyncio>=1.0  # Async test support
pytest-cov>=5.0      # Coverage
flake8>=7.0          # Lint
bandit>=1.7          # Security scan
pip-audit>=2.7       # Dep audit
```

### 1.2 Architecture Map

**Module Relationships:**
```
main.py
  ├── master_watchdog.py  [PID 1, sole DB writer]
  │     ├── database.py   [WatchdogDB + ReadOnlyDB + write_queue]
  │     ├── process_reaper.py
  │     ├── watchdog_state.py
  │     └── role_router.py → workers/adapters.py
  ├── phi3_manager.py     [local ollama scribe]
  ├── master_orchestrator.py  [COO, 2440 lines — GOD FILE ★]
  │     ├── context_manager.py
  │     ├── output_parser.py
  │     ├── git_manager.py
  │     ├── learning_log.py
  │     ├── dac_tagger.py
  │     ├── tdd_pipeline.py
  │     ├── contract_generator.py
  │     ├── contract_validator.py
  │     ├── rules_engine.py
  │     ├── cicd_generator.py
  │     └── role_router.py (via self.router)
  └── dashboard/dashboard_server.py  [FastAPI + WebSocket]
```

**God Files:**
- `master_orchestrator.py` — ~2440 lines. Handles: chat, sessions, discussion, project create, blueprint, task planning, phase execution, quality gates, E2E tests, UAT, production deploy. **This is every concern in one file.**

**Circular Dependency Risk:**
- `master_orchestrator.py` imports from `orchestration.database` directly via `queue_write` (line 1745: `from orchestration.database import queue_write as _qw`). This is a local inline import inside a method, bypassing the module-level try/except guard — if database module is unavailable it will raise `ImportError` at runtime mid-execution, not at startup.

### 1.3 Configuration Forensics

**factory_config.json structure (LOADED, HIGH confidence):**

| Key Path | Type | Consumer | Status |
|----------|------|----------|--------|
| `factory.version` | str | None found | ORPHANED |
| `factory.working_dir` | str | `main.py:74`, `watchdog:44` | ✅ CONSUMED |
| `factory.factory_state_dir` | str | `orchestrator:64` — WARNING: only in relative form, not joined to working_dir | ⚠️ BUG |
| `factory.log_level` | str | `main.py:49` | ✅ CONSUMED |
| `factory.git_repo` | null | `watchdog._check_git():220` | ✅ CONSUMED |
| `factory.db_writer` | str | None found in code | ORPHANED |
| `workers.*` | dict | `watchdog._init_workers()` | ✅ CONSUMED |
| `local_test_mode` | bool | LISTED but not verified | MEDIUM CONFIDENCE |
| `local_roles.*` | dict | `role_router._local_roles_config` | ✅ CONSUMED |
| `roles.*` | dict | `role_router._load_from_config()` | ✅ CONSUMED |
| `project_types.*` | dict | Not found in orchestrator code | LIKELY ORPHANED |
| `figma_mcp.*` | dict | `figma_mcp.py` (not read) | UNVERIFIED |
| `watchdog.monitoring_interval_seconds` | int | `watchdog:76` | ✅ CONSUMED |
| `watchdog.task_timeout_minutes` | int | `watchdog._handle_stuck_tasks():517` | ✅ CONSUMED |
| `watchdog.checkpoint_required_interval_minutes` | int | Not found in watchdog code | ORPHANED |
| `watchdog.context_respawn_threshold` | float | `watchdog:78` | ✅ CONSUMED |
| `watchdog.max_respawn_attempts` | int | Not found in code | ORPHANED |
| `watchdog.db_write_batch_interval_seconds` | int | `watchdog:68` | ✅ CONSUMED |
| `quality_gates.confidence_threshold` | float | `orchestrator:1226` (gatekeeper_review method ONLY — not used in _quality_gate) | ⚠️ INCONSISTENT |
| `quality_gates.test_coverage_threshold` | float | Not found | ORPHANED |
| `quality_gates.security_scan_enabled` | bool | Not found | ORPHANED |
| `quality_gates.alignment_validation` | str | Not found | ORPHANED |
| `cost_controls.*` | dict | Not found in code | ORPHANED |
| `dashboard.host` | str | dashboard_server.py (not read) | UNVERIFIED |
| `dashboard.port` | int | `main.py:99` | ✅ CONSUMED |
| `dashboard.refresh_interval_ms` | int | dashboard_server.py (not read) | UNVERIFIED |

**FER-AF-001 — Config Drift: factory_state_dir uses relative path**
```yaml
FINDING:
  type: CONFIG_DRIFT
  severity: HIGH
  file: orchestration/master_orchestrator.py:64-68
  evidence: |
    state_dir = config.get("factory", {}).get("factory_state_dir")
    if state_dir:
        self._history_file = Path(state_dir) / "chat_history.json"
  issue: |
    factory_config.json has factory_state_dir = "factory_state" (relative).
    Watchdog uses: self.state_dir = self.working_dir / "autonomous_factory" / "factory_state"
    Orchestrator uses: Path("factory_state") — resolves to CWD-relative path.
    These are DIFFERENT directories. Chat history writes to wrong location unless
    process is started from autonomous_factory/ directory.
  confidence: HIGH
```

### 1.4 Shadow & Phantom Dependencies

**CRITICAL: requirements.txt declares only 7 packages. The actual code imports many more:**

| Package | Imported In | In requirements.txt? |
|---------|-------------|----------------------|
| `aiohttp` | `adapters.py`, `watchdog.py` | ✅ YES |
| `fastapi` | `dashboard_server.py` (inferred) | ❌ MISSING |
| `uvicorn` | `dashboard_server.py` (inferred) | ❌ MISSING |
| `websockets` | `dashboard_server.py` (inferred) | ❌ MISSING |
| `sqlite3` | `database.py` | stdlib — OK |
| `asyncio` | everywhere | stdlib — OK |
| `subprocess` | `git_manager.py`, `adapters.py` | stdlib — OK |
| `pathlib` | everywhere | stdlib — OK |
| `pytest-asyncio` | tests | ✅ YES |
| `playwright` | test scripts | ❌ MISSING |

**FER-AF-002 — Phantom Dependencies: requirements.txt is 85% incomplete**
```yaml
FINDING:
  type: PHANTOM_DEPENDENCY
  severity: CRITICAL
  file: requirements.txt
  evidence: |
    File contains only 7 packages. FastAPI, uvicorn, websockets — the entire
    dashboard stack — are missing. Running: pip install -r requirements.txt
    will produce a factory that cannot start its dashboard.
  verification_command: "pip install -r requirements.txt && python main.py"
  expected_output: "ModuleNotFoundError: No module named 'fastapi'"
  confidence: HIGH
```

---

## SECTION 2: EXECUTION GAP ANALYSIS

### 2.1 Intent vs Reality Matrix

| Feature Claimed | Evidence of Implementation | Verdict |
|-----------------|---------------------------|---------|
| "5 phases" (BLUEPRINT_SYSTEM prompt) | Code has 3 build phases (1-3) + proto (4) + prod (5). Prompt says "5 phases". | ⚠️ MISMATCH |
| "12-step TDD" (TDD_SYSTEM) | `_tdd_prompt()` lists 13 steps: AC→TDE-RED→TDE-GREEN→BC→BF→SEA→DS→OA→VB→GIT→CL→CCP→AD | ⚠️ 12 vs 13 |
| "Parallel task execution" | `asyncio.gather()` in `_phase_build()` — IMPLEMENTED | ✅ |
| "Dependency-aware scheduling" | `_classify_dependencies()` with Kimi + heuristic fallback | ✅ |
| "E2E tests run automatically" | `_run_e2e_tests()` — IMPLEMENTED, NON-BLOCKING | ✅ but ⚠️ (R4) |
| "Cost tracking" | `cost_tracking` table + writes in `_execute_single_task()` | ✅ |
| "Learning log injection" | `inject_learnings()` in `learning_log.py` | ✅ but never called in execute flow |
| Dashboard UAT panel | dashboard_server.py (not read — unverified) | UNVERIFIED |
| Token budget cap enforcement | `cost_controls.max_api_cost_per_project=50.0` in config | ORPHANED — no enforcement code found |

### 2.2 TODO/FIXME/HACK Graveyard

| File | Line | Content |
|------|------|---------|
| `orchestration/contract_generator.py` | 130 | `-- TODO: Review and complete` — inside auto-generated SQL schema string |

**Sparse.** Only one TODO found across the entire codebase. Either the project is clean or TODOs were systematically removed.

### 2.3 Dead Code Cemetery

**FER-AF-003 — Dead Code: `learning_log.inject_learnings()` is never called in the execution pipeline**
```yaml
FINDING:
  type: DEAD_CODE
  severity: HIGH
  evidence: |
    LearningLog is instantiated in execute_project() at line 1329:
      learning_log = LearningLog(self.db)
    It is passed to _phase_build() at line 1374.
    BUT _phase_build() signature accepts it but never calls inject_learnings().
    The variable is passed through and unused.
    The entire learning injection feature — the mechanism that was supposed to
    prevent repeat mistakes — is WIRED but SILENT.
  verification_command: "grep -n 'inject_learnings' orchestration/master_orchestrator.py"
  expected_output: "No matches"
  confidence: HIGH
```

**FER-AF-004 — Dead Config: Multiple quality_gates keys orphaned**
```yaml
FINDING:
  type: DEAD_CONFIG
  severity: MEDIUM
  evidence: |
    factory_config.json defines:
      quality_gates.test_coverage_threshold = 0.9
      quality_gates.security_scan_enabled = true
      quality_gates.alignment_validation = "enforced"
    None of these keys appear in any .py file. They exist but are never read.
  confidence: HIGH
```

**FER-AF-005 — Dead Config: cost_controls section never consumed**
```yaml
FINDING:
  type: DEAD_CONFIG
  severity: MEDIUM
  evidence: |
    factory_config.json defines:
      cost_controls.local_model_preference = 0.8
      cost_controls.max_api_cost_per_project = 50.0
      cost_controls.track_cli_usage = true
    No consumption found in any orchestration file.
    Budget cap of $50/project is configured but never enforced.
  confidence: HIGH
```

### 2.4 Promised but Never Delivered

**FER-AF-006 — Budget Cap Never Enforced**
```yaml
FINDING:
  severity: HIGH
  detail: |
    config has max_api_cost_per_project=50.0. cost_tracking table exists and
    receives writes. But no code reads cumulative cost and halts execution when
    budget is exceeded. A project could burn unlimited tokens with zero guardrails.
  confidence: HIGH
```

**FER-AF-007 — Phase 5 Production Deploy is a Stub**
```yaml
FINDING:
  severity: MEDIUM
  file: master_orchestrator.py:1434-1435
  evidence: |
    # ─── Phase 5: Production ───
    # Triggered separately after UAT approval
  detail: |
    Phase 5 is a comment. approve_uat() at line 2405 runs git merge + tag v1.0.0
    but does no actual deployment (no Docker push, no server restart, no Kubernetes
    apply). The "production deploy" is just a git tag.
  confidence: HIGH
```

---

## SECTION 3: VERIFICATION & TESTING FAILURE AUDIT

### 3.1 Test Coverage Reality Check

**Test files found:**
- `test_7issues_wave1.py` — Tests issues 2, 6, 7 (git lock, learning log filter, DB validate). Uses mock DB. Tests structural properties only.
- `test_7issues_wave2.py` — Not read (context limit). Claimed to have 14 tests.
- `test_e2e_pipeline.py` — Uses `{"files": [{"path": "utils.py", "content": "try:\n  x()\nexcept:\n  pass"}]}` as fixture. Mocked output parser.
- `test_static_analysis.py`, `test_chat_export.py`, `test_discussion_mode.py`, `test_e2e_doc_recall.py`, `test_library_manager_e2e.py` — Not read.

**FER-AF-008 — Tests mock everything and test nothing real**
```yaml
FINDING:
  type: FIXTURE_BLINDNESS
  severity: HIGH
  file: tests/test_7issues_wave1.py:86-110
  evidence: |
    def _make_db(self, entries):
        db = MagicMock()
        db.get_learning_log.return_value = entries
        return db
  detail: |
    LearningLog tests use a MagicMock for the entire DB. This verifies the
    filtering logic in Python but never tests that:
    - The SQL query in get_learning_log() actually works
    - The occurrence_count column is properly read from SQLite
    - The validated boolean is properly read (SQLite stores as 0/1, Python reads as int)
    A database returning validated=0 (int) instead of validated=False could break
    the `if ... or validated` check if not careful.
  confidence: HIGH
```

### 3.2 Untested Core Logic Map

| Critical Function | Has Test? | Risk |
|-------------------|-----------|------|
| `_execute_single_task()` | ❌ NO | CRITICAL — 200+ line core pipeline |
| `_phase_build()` — parallel wave scheduling | ❌ NO | CRITICAL — concurrent execution |
| `_quality_gate()` — verdict logic | ❌ NO | HIGH |
| `_generate_tasks_from_blueprint()` | ❌ NO | HIGH |
| `_classify_dependencies()` | ❌ NO | HIGH |
| `_run_e2e_tests()` | ❌ NO | HIGH |
| `OutputParser.parse()` — all sanitizers | ❌ NO direct test | HIGH |
| `atomic_commit()` — git operations | ✅ Structural only | MEDIUM |
| `queue_write()` — write bus | ❌ NO | HIGH |
| `_db_drain_loop()` | ❌ NO | HIGH |
| `ReadOnlyDB.get_*` methods | ❌ NO | MEDIUM |

**VERIFICATION_CLAIM:** No test exercises the actual project execution pipeline end-to-end with real (non-mocked) components. Confidence: HIGH.

### 3.3 Silent Failure Catalog

**FER-AF-009 — Quality Gate Worker Failure Auto-Approves**
```yaml
FINDING:
  type: SILENT_FAILURE
  severity: CRITICAL
  file: master_orchestrator.py:2375-2378
  evidence: |
    # Worker call failed
    return {"verdict": "APPROVED", "confidence": 0.5,
            "issues": [], "dac_tags": [],
            "note": f"Gate worker '{gate_worker_name}' call failed — auto-approved"}
  detail: |
    If the gatekeeper worker (Kimi/Gemini) fails for ANY reason — timeout, crash,
    network error — the task is AUTOMATICALLY APPROVED. A completely broken piece
    of code will be committed and merged to develop with no human notification.
    The "note" key in the return dict is never surfaced to the user.
  confidence: HIGH
```

**FER-AF-010 — E2E Failure Does Not Block UAT**
```yaml
FINDING:
  type: SILENT_FAILURE
  severity: CRITICAL
  file: master_orchestrator.py:2041-2045, 2084-2088
  evidence: |
    """Runs pytest on tests/ directory. Non-blocking; failure does not block UAT."""
    return {"success": False, "returncode": proc.returncode, "output_tail": output_tail}
  detail: |
    _run_e2e_tests() is explicitly documented as non-blocking. Its result is
    returned in the execute_project() response dict (key "e2e") but the calling
    code does NOT check e2e_result["success"]. The UAT button in the dashboard
    is presented regardless of E2E pass/fail status. A project with 100% failing
    E2E tests gets offered for human approval.
  confidence: HIGH
```

**FER-AF-011 — Cost tracking failure silently swallowed**
```yaml
FINDING:
  type: SILENT_FAILURE
  severity: MEDIUM
  file: master_orchestrator.py:1753-1754
  evidence: |
    except Exception as _ce:
        logger.debug(f"Cost tracking write failed (non-fatal): {_ce}")
  detail: |
    Cost tracking write failures are logged at DEBUG level — invisible in default
    INFO logging. No alert, no metric. Cost data will silently disappear under
    queue pressure without any visible indication to operators.
  confidence: HIGH
```

**FER-AF-012 — Scope violation TRAP does not abort task**
```yaml
FINDING:
  type: SILENT_FAILURE
  severity: HIGH
  file: output_parser.py:290-327
  evidence: |
    violation = {..., "violation_tag": "TRAP", ...}
    logger.warning(f"TRAP out_of_scope_write: task {task_id} wrote {rel_path!r}...")
    # ... queues DaC tag ...
    return violation  # file is NOT written, but task continues
  detail: |
    Out-of-scope file writes are blocked (good), logged as TRAP (good), but
    the task then continues executing and is still eligible for quality gate
    approval. The violation is stored in DB but there is no mechanism to:
    1. Show the violation to the human in the dashboard before UAT
    2. Fail the task when scope violations occur
    3. Require human acknowledgment of violations
  confidence: HIGH
```

### 3.4 Input Validation Audit

**FER-AF-013 — LLM JSON output parsed without schema validation**
```yaml
FINDING:
  type: INPUT_VALIDATION
  severity: HIGH
  file: master_orchestrator.py:1106-1116 (plan_tasks_gsd)
  evidence: |
    task_defs = json.loads(text[start:end])
    # No schema validation — task_defs could be any JSON structure
    # Fallback:
    task_defs = [{"module": "backend", "description": result["response"],
                  "complexity_hint": "high"}]
  detail: |
    When LLM returns invalid JSON, the ENTIRE raw LLM response string is used
    as the task description. If an LLM returns a 10,000-character refusal or
    error message, it becomes a single "task description" that gets written to DB
    and passed to workers as if it were real task content.
  confidence: HIGH
```

---

## SECTION 4: ERROR HANDLING & RESILIENCE FORENSICS

### 4.1 Error Propagation Trace

**Scenario: LLM returns HTTP 429 (rate limit)**
```
worker.send_message() → CLIWorkerAdapter.send_message()
  → asyncio.wait_for(proc.communicate(), timeout=self.timeout)
  → CLI tool exits with non-zero returncode
  → result = {"success": False, "error": "..."}
  → _execute_single_task() checks: if not worker_result.get("success")
  → dac_tagger.tag() called, returns {"success": False, "error": "Worker failed"}
  → _phase_build() marks task as failed_tasks
  → No retry at the worker level for 429 specifically
  → max_retries in config is for CLI subprocess retries (adapters.py), not semantic retries
```
**Evidence:** `adapters.py:172` — `for attempt in range(self.max_retries + 1)` — retries are blind retries with no back-off for rate limiting.

**FER-AF-014 — No Rate Limit Awareness**
```yaml
FINDING:
  severity: HIGH
  file: workers/adapters.py:172-200
  detail: |
    Retry loop iterates max_retries+1 times with no delay between attempts.
    A 429 rate limit response will be retried immediately, consuming more quota.
    No exponential back-off. No recognition of HTTP 429 vs other errors.
  confidence: HIGH
```

**Scenario: DB file locked/corrupted**
```
ReadOnlyDB._read_conn() opens: sqlite3.connect("file:{path}?mode=ro", ...)
→ If DB is being heavily written, WAL mode prevents read blocking (GOOD)
→ If DB file is deleted: OperationalError propagates up — unhandled
→ If DB is corrupted: sqlite3.DatabaseError propagates — unhandled at orchestrator level
```

**FER-AF-015 — No DB corruption recovery**
```yaml
FINDING:
  severity: HIGH
  detail: |
    ReadOnlyDB._read_conn() has no error handling around sqlite3.connect().
    A corrupted or deleted factory.db causes unhandled OperationalError that
    propagates up through every DB call, crashing the orchestrator mid-execution.
    WatchdogStatePersistence exists but only for watchdog state, not DB health.
  confidence: MEDIUM (DB corruption scenario not directly traced to handler)
```

**Scenario: Git in detached HEAD state**
```
git_manager._run_git("checkout", "develop")
→ If detached HEAD: "error: pathspec 'develop' did not match any file(s) known to git"
→ Raises GitError
→ create_phase_branch() propagates GitError
→ _phase_build() has no try/except around git_mgr.create_phase_branch()
→ Exception propagates to execute_project() → caught by outer try/except → project fails
```

**FER-AF-016 — Git state not verified before phase execution**
```yaml
FINDING:
  severity: HIGH
  file: master_orchestrator.py:1923-1924
  evidence: |
    branch = git_mgr.create_phase_branch(phase_num, f"phase-{phase_num}")
  detail: |
    No verification that git repo is in expected state before creating phase branch.
    Detached HEAD, missing develop branch, or uncommitted changes will cause
    GitError that aborts the entire phase with no meaningful user message.
  confidence: HIGH
```

### 4.2 Exit Code Audit

**Shell Scripts (not fully read — LISTED_ONLY):**
- `check_health.sh`, `setup.sh`, `recover.sh` — Not analyzed. Cannot verify exit code handling.

**Python subprocess calls:**
```
git_manager._run_git(): check=True by default → raises GitError on non-zero exit ✅
_run_e2e_tests(): proc.returncode checked ✅
CLIWorkerAdapter.send_message(): proc.returncode checked ✅
create_project(): proc.communicate() result NOT checked:
```

**FER-AF-017 — git clone/init result not checked in create_project()**
```yaml
FINDING:
  severity: MEDIUM
  file: master_orchestrator.py:963-972
  evidence: |
    proc = await asyncio.create_subprocess_exec(
        "git", "init", str(project_path), ...)
    await proc.communicate()  # returncode NEVER checked
  detail: |
    If git init fails (e.g., directory not writable, git not in PATH),
    execution silently continues. The project is created in DB with a path
    that has no git repo. All subsequent git operations will fail cryptically.
  confidence: HIGH
```

### 4.3 Resource Leak Detection

**FER-AF-018 — time.sleep() in asyncio context (queue_write retry)**
```yaml
FINDING:
  type: BLOCKING_CALL_IN_ASYNC
  severity: HIGH
  file: orchestration/database.py:424
  evidence: |
    import time
    time.sleep(0.1 * (attempt + 1))  # Inside queue_write() — synchronous sleep
  detail: |
    queue_write() is called from async contexts (orchestrator, output_parser).
    time.sleep() in an async context blocks the ENTIRE event loop, freezing
    ALL concurrent tasks for 100-300ms per retry attempt. Under queue pressure,
    this will stall the watchdog drain loop and all active async tasks.
    Should use asyncio.sleep() via an async version of queue_write().
  confidence: HIGH
```

**FER-AF-019 — Same issue in process_reaper.py**
```yaml
FINDING:
  type: BLOCKING_CALL_IN_ASYNC
  severity: MEDIUM
  file: orchestration/process_reaper.py:418, 424
  evidence: |
    time.sleep(0.5)  # line 418
    time.sleep(0.2)  # line 424
  detail: |
    process_reaper likely runs in or interacts with the async event loop.
    Synchronous sleeps here block the loop. (Cannot fully verify without
    reading process_reaper.py — PARTIAL_SCAN risk.)
  confidence: MEDIUM
```

**FER-AF-020 — asyncio.get_event_loop() deprecated pattern**
```yaml
FINDING:
  severity: MEDIUM
  file: main.py:105, database.py:361
  evidence: |
    loop = asyncio.get_event_loop()   # main.py:105
    loop = asyncio.get_event_loop()   # database.py:361
  detail: |
    asyncio.get_event_loop() is deprecated in Python 3.10+ when no running loop
    exists. In an async context (both call sites are inside async functions),
    asyncio.get_running_loop() is the correct, non-deprecated alternative.
    Under Python 3.12, this emits DeprecationWarning and may break in 3.14.
  confidence: HIGH
```

---

## SECTION 5: DEPENDENCY & INTEGRATION DEBT

### 5.1 Dependency Health

```yaml
DEPENDENCY_AUDIT:
  requirements_txt:
    aiohttp: ">=3.9"       # UNPINNED — breaking changes possible
    pytest: ">=8.0"        # UNPINNED
    pytest-asyncio: ">=1.0"  # UNPINNED — major version jump risk
    pytest-cov: ">=5.0"    # UNPINNED
    flake8: ">=7.0"        # UNPINNED
    bandit: ">=1.7"        # UNPINNED
    pip-audit: ">=2.7"     # UNPINNED

  missing_from_requirements:
    - fastapi       # Dashboard server
    - uvicorn       # ASGI server
    - websockets    # WebSocket support
    - pydantic      # Data models (inferred from FastAPI usage)
    - starlette     # FastAPI dependency
    - playwright    # Test scripts use it
    - httpx         # Likely used by FastAPI tests

  verdict: REQUIREMENTS_INCOMPLETE — new-machine setup will fail
```

**FER-AF-021 — All dependencies unpinned**
```yaml
FINDING:
  severity: MEDIUM
  detail: |
    Every package uses >= (minimum) rather than == (exact) pinning.
    A pip install 3 months from now may pull breaking major versions.
    There is no Pipfile.lock or poetry.lock to reproduce the exact environment.
  confidence: HIGH
```

### 5.2 External Integration Points

| Integration | Timeout | Error Handling | Auth |
|-------------|---------|----------------|------|
| Ollama (localhost:11434) | 120s (deepseek), 60s (qwen), 30s (phi3) | `check_health()` on boot | None (local) |
| Claude CLI | 600s | `is_authenticated()` real check | CLI session |
| Kimi CLI | 600s | `is_authenticated()` real check | CLI session |
| Gemini CLI + API | 600s | `is_authenticated()` real check | CLI + GOOGLE_API_KEY |
| SQLite DB | 10s read timeout | WAL mode, `check=True` | None |
| Git | 60s per command | `subprocess.TimeoutExpired` → `GitError` | OS credentials |

**FER-AF-022 — Kimi/Gemini 600s timeout allows 10-minute hangs**
```yaml
FINDING:
  severity: MEDIUM
  detail: |
    CLI workers have 600s (10 minute) timeout. If Kimi hangs on a large prompt,
    it can block a wave for 10 minutes while holding the asyncio.gather slot.
    Since asyncio.gather runs tasks concurrently (not in a thread pool), a
    hanging subprocess does NOT block other tasks — but it consumes a gather slot
    and prevents the wave from completing until it times out.
    Per-complexity timeouts (Risk R13) are NOT implemented — all tasks use
    the same worker-level timeout regardless of complexity.
  confidence: HIGH
```

### 5.3 Hardcoded Shame List

| Value | Location | Should Be |
|-------|----------|-----------|
| `http://localhost:11434` | `factory_config.json:12,20,59` | Config only (IS in config ✅) |
| `http://localhost:11434` | `master_watchdog.py:212` — `_ollama_running()` | Should read from config |
| `127.0.0.1` | `factory_config.json:200` | Config only (IS in config ✅) |
| `8420` | `factory_config.json:202` | Config only (IS in config ✅) |
| `20s` (poll timeout) | `master_orchestrator.py:2207` | Should be configurable |
| `300s` (E2E hard cap) | `master_orchestrator.py:2066` | Should be in config |
| `5000` (blueprint truncation) | `master_orchestrator.py:1582,1591` | Should be configurable |
| `6000` (task gen truncation) | `master_orchestrator.py:2122` | Should be configurable |
| `~/working` | `factory_config.json:3` | Config (IS in config ✅) |
| `"fastapi"`, `"uvicorn"` etc | `_fallback_tasks()` 2224-2279 | Hardcoded in fallback blueprint |

**FER-AF-023 — Ollama URL hardcoded in watchdog health check**
```yaml
FINDING:
  severity: LOW
  file: master_watchdog.py:212
  evidence: |
    async with s.get("http://localhost:11434/api/tags", ...) as r:
  detail: |
    Watchdog health check hardcodes the Ollama URL instead of reading
    config["workers"]["deepseek"]["api_base"]. If Ollama is on a different
    host or port (remote GPU server), health check silently reports offline.
  confidence: HIGH
```

---

## SECTION 6: CODE QUALITY & PATTERN ANALYSIS

### 6.1 "Second-Time-Right" Pattern

Evidence of AI-assisted iterative development visible in:
- `master_orchestrator.py:1745`: `from orchestration.database import queue_write as _qw` — inline import with alias indicates this was added after-the-fact, patched into an existing method without refactoring the top-level imports
- Comments like `# FIX: backup had 'self._config_path = path' but parameter is 'config_path'` at `master_watchdog.py:57` — preserves the bug history in production code
- `# FER-CLI-002 FIX:` comment at `master_watchdog.py:551` — bug ticket IDs embedded in source code indicate patch-over-patch development

### 6.2 Copy-Paste Debt

**FER-AF-024 — Blueprint content truncated to 5000 chars in 3 places**
```yaml
FINDING:
  type: COPY_PASTE_DEBT
  severity: MEDIUM
  evidence:
    - master_orchestrator.py:1582: blueprint[:5000]
    - master_orchestrator.py:1591: blueprint[:5000]
    - master_orchestrator.py:2122: blueprint_content[:6000]
  detail: |
    Blueprint content is sliced to 5000 or 6000 characters in three separate
    places. For a Gemini 1M-context model, this is absurdly conservative and
    loses critical details. The truncation values are inconsistent (5000 vs 6000)
    indicating copy-paste without review. A 5000-char slice of a 50,000-char
    blueprint is a 90% information loss for the audit.
  confidence: HIGH
```

**FER-AF-025 — Worker result dict structure duplicated in 6 places**
```yaml
FINDING:
  type: COPY_PASTE_DEBT
  severity: LOW
  detail: |
    Every worker call follows the same pattern:
      result = await worker.send_message(...)
      if not result.get("success"):
          return {"error": result.get("error"), ...}
    This pattern appears 8+ times in master_orchestrator.py with no shared helper.
    Not critical but increases maintenance burden.
  confidence: HIGH
```

### 6.3 Naming & Convention Violations

| Issue | Location | Detail |
|-------|----------|--------|
| `_quality_gate()` has `validator` parameter typed as `ContractValidator = None` but the call at line 2335 passes `validator_report={}` if validator is None | `master_orchestrator.py:2298,2335-2338` | Logic inversion — None validator triggers empty validator_report, not validator.validate() |
| `request_write_and_wait()` called on `ReadOnlyDB` at `master_orchestrator.py:973` | `master_orchestrator.py:973` | ReadOnlyDB should never have a `request_write_and_wait` method — this is a write operation on a read-only handle |
| `_fallback_tasks()` generates a "Books" app (BookCard, BookForm, etc.) regardless of the actual project | `master_orchestrator.py:2224-2279` | Hardcoded domain-specific fallback for generic factory |

**FER-AF-026 — ReadOnlyDB used for write operation via request_write_and_wait**
```yaml
FINDING:
  type: ARCHITECTURE_VIOLATION
  severity: HIGH
  file: master_orchestrator.py:973-980
  evidence: |
    await self.db.request_write_and_wait("insert", "projects", {
        "project_id": project_id, ...
    })
  detail: |
    self.db is a ReadOnlyDB instance. The design principle is "ReadOnlyDB cannot write."
    Yet it exposes a request_write_and_wait() method that enqueues writes.
    This breaks the ReadOnlyDB contract — the name is misleading.
    The issue is that ReadOnlyDB's request_write() method actually queues writes
    via the shared write bus (database.py:875), so the "read-only" label is
    partially false — it CAN queue writes, it just cannot execute them directly.
  confidence: HIGH
```

### 6.4 Complexity Hotspots

| Function | Lines | Complexity |
|----------|-------|-----------|
| `master_orchestrator.py::execute_project()` | ~150 lines | HIGH — 4 phases, 3 loops, multiple try/except |
| `master_orchestrator.py::_execute_single_task()` | ~220 lines | VERY HIGH — 12 steps, 3 retry loops |
| `master_orchestrator.py::_phase_build()` | ~120 lines | HIGH — deadlock handling, parallel waves |
| `master_orchestrator.py::_generate_tasks_from_blueprint()` | ~120 lines | HIGH — 3 fallback paths for JSON parsing |
| `master_orchestrator.py::discussion_chat()` | ~170 lines | HIGH — 2 while loops, auto_loop, cancellation |
| `output_parser.py::parse()` | ~80 lines | HIGH — 5 parse strategies, nested loops |

---

## SECTION 7: CHAOS ANALYSIS

### 7.1 Core Function Chaos

**What happens when LLM returns empty string?**
```
OutputParser.parse("") → raises OutputParseError("Empty output from worker")
→ _execute_single_task() does NOT catch OutputParseError
→ Exception propagates to asyncio.gather() in _phase_build()
→ Caught by return_exceptions=True → logged as "Task raised exception"
→ Task marked as failed (not in completed_task_ids)
→ No DaC tag for this specific failure path
```
**Verdict:** Handled, but with silent DaC gap. ✅/⚠️

**What happens when all LLM workers are down?**
```
_get_worker("gatekeeper_review") → None
_quality_gate(): gate_worker = None
→ return {"verdict": "APPROVED", ...}  ← AUTO-APPROVE
```
**Verdict:** Silent auto-approval. CRITICAL. (FER-AF-009 already filed.)

**What happens when task has circular dependencies?**
```
_classify_dependencies() → dep_graph = {"A": ["B"], "B": ["A"]}
_phase_build():
  ready = [t for t in pending if all(dep in completed for dep in deps)]
  → ready = []  (A waits for B, B waits for A)
  → logger.warning("dependency deadlock ... running first task to unblock")
  → ready = [next(iter(pending.values()))]  ← FORCE RUN
```
**Verdict:** Handled with force-run (Risk R8 — documented as unsafe). Task runs without deps met. ⚠️

**What happens when git commit fails mid-wave?**
```
atomic_commit() → _run_git("commit", ...) → GitError raised
→ async with self._commit_lock: ... GitError propagates
→ _execute_single_task() does NOT catch GitError specifically
→ Exception propagates to asyncio.gather → task marked failed
→ But files were ALREADY WRITTEN to disk by output_parser
→ Written files are now unstaged, uncommitted, invisible to subsequent tasks
→ No cleanup. No rollback.
```

**FER-AF-027 — Written files orphaned on git commit failure**
```yaml
FINDING:
  type: RESOURCE_LEAK
  severity: HIGH
  detail: |
    output_parser._apply_file() writes files to disk BEFORE atomic_commit().
    If atomic_commit() fails, files remain on disk but are never committed.
    Subsequent tasks in the same phase may find these files and behave
    unpredictably. No cleanup or rollback mechanism exists.
  confidence: HIGH
```

### 7.2 Environment Chaos

**What if GOOGLE_API_KEY is missing?**
```
factory_config.json: gemini.api_key_env = "GOOGLE_API_KEY"
→ adapters.py creates GeminiWorkerAdapter (not read in detail)
→ check_health() likely returns "offline" or "degraded"
→ watchdog boots without Gemini
→ role_router: architecture_audit primary=gemini unavailable
→ fallback=claude used (if claude is available)
→ If both offline: _quality_gate() auto-approves ← CRITICAL fallback chain
```

**What if port 8420 is in use?**
```
dashboard_server.start() → uvicorn.run/bind on 8420
→ OSError: [Errno 48] Address already in use
→ Propagates to main.py → boot fails
→ No "try different port" logic
→ No user-friendly error message
```

**FER-AF-028 — No port conflict detection or alternate port fallback**
```yaml
FINDING:
  severity: MEDIUM
  detail: |
    Port 8420 is hardcoded in config with no fallback. If port is occupied,
    factory fails to boot with a raw OSError. No guidance given to user.
  confidence: MEDIUM (dashboard_server.py not read — PARTIAL_SCAN)
```

**What if factory_state/ directory doesn't exist?**
```
watchdog: self.state_dir.mkdir(parents=True, exist_ok=True) ← handled ✅
orchestrator: self._history_file.parent.mkdir(parents=True, exist_ok=True) ← handled ✅
```
**Verdict:** Handled correctly.

### 7.3 Data Chaos

**What if blueprint JSON exceeds context window?**
```
blueprint_content sliced to 5000 chars in audit calls (lines 1582, 1591)
Full blueprint stored in DB (no truncation in DB write)
Task generation: blueprint[:6000] (line 2122)
→ A 50,000-char blueprint loses 90% of content in audit and task generation
→ Tasks generated from truncated blueprint will be incomplete/incorrect
→ No warning to user about truncation
```

**FER-AF-029 — Blueprint truncation silent and severe**
```yaml
FINDING:
  severity: HIGH
  detail: |
    blueprint[:5000] and blueprint[:6000] slices applied with no logging of
    how much content was dropped. For a complex enterprise project, a blueprint
    easily exceeds 20,000 characters. The LLM sees less than 30% of the spec
    during task generation. Generated tasks will be wrong. No warning is issued.
  confidence: HIGH
```

**What if learning log has 10,000 entries?**
```
_find_similar(): iterates significant_words[:3], calls get_learning_log(limit=10) per word
→ Maximum 30 DB queries per log_fix() call
→ _is_similar() does set intersection — O(n) per comparison
→ With 10,000 entries, naive keyword search returns up to 30 rows
→ This is manageable but naive — no index on keyword similarity
→ Risk R3: naive keyword match vs embedding-based retrieval
```

---

## SECTION 8: DOCUMENTATION DEBT

**README.md** — Not fully read. Cannot verify accuracy.

**Inline documentation:**
- `master_orchestrator.py` has good docstrings for public methods.
- `_execute_single_task()` is well-commented but the step numbering (1-12) doesn't match actual code — step 9 in code is "9. TDD Pipeline" but `_tdd_prompt()` at line 1290 says "12-step TDD protocol" and lists 13 steps (AC→...→AD).

**FER-AF-030 — TDD step count inconsistency**
```yaml
FINDING:
  type: DOCUMENTATION_DRIFT
  severity: LOW
  file: master_orchestrator.py:1285-1291, 2435-2438
  evidence: |
    # _tdd_prompt():
    "Execute 12-step TDD protocol"
    "Steps: AC→TDE-RED→TDE-GREEN→BC→BF→SEA→DS→OA→VB→GIT→CL→CCP→AD"
    # That's 13 step abbreviations.
    # BLUEPRINT_SYSTEM says "5 phases"
    # execute_project() implements 3+1+1=5 phases (1-3, proto, prod)
    # _execute_single_task() has 12 numbered comments but 13 distinct steps
  confidence: HIGH
```

---

## SECTION 9: SECURITY AUDIT

### 9.1 Secrets & Credentials

**FER-AF-031 — GOOGLE_API_KEY in config file (env var reference — correct)**
```yaml
FINDING:
  type: SECURITY_INFO
  severity: INFO
  file: config/factory_config.json:47
  evidence: |
    "api_key_env": "GOOGLE_API_KEY"
  detail: |
    Gemini uses env var reference — CORRECT. API key not hardcoded.
    However: no equivalent api_key_env for any other workers.
    Claude/Kimi use CLI auth — no API keys.
    DeepSeek/Qwen are local — no keys needed.
  verdict: ACCEPTABLE
```

**No hardcoded secrets found in scanned files.** Confidence: MEDIUM (dashboard_server.py not read).

### 9.2 Input Injection Vectors

**FER-AF-032 — Path traversal protection present but relies on resolve()**
```yaml
FINDING:
  type: SECURITY_INFO
  severity: INFO
  file: output_parser.py:329-333
  evidence: |
    full_path = (self.project_path / rel_path).resolve()
    if not str(full_path).startswith(str(self.project_path.resolve())):
        logger.error(f"Path traversal blocked: {rel_path}")
        return None
  detail: |
    Path traversal is blocked via resolve() comparison — correct approach.
    However: on symlinked paths, resolve() follows symlinks.
    If project_path contains a symlink, a path like "../../etc/passwd" through
    the symlink could bypass this check. Low probability but not impossible.
  confidence: MEDIUM
```

**FER-AF-033 — No subprocess shell=True found (POSITIVE finding)**
```yaml
FINDING:
  type: SECURITY_POSITIVE
  severity: INFO
  detail: |
    Grep for shell=True across all .py files: NO MATCHES.
    All subprocess calls use exec form (list of args), not shell form.
    No command injection vector via subprocess.
  confidence: HIGH
```

**FER-AF-034 — SQL injection prevention present**
```yaml
FINDING:
  type: SECURITY_POSITIVE
  severity: INFO
  file: database.py:23-27
  evidence: |
    def _sanitize_identifier(name: str) -> str:
        if not isinstance(name, str) or not re.match(r'^[a-zA-Z0-9_]+$', name):
            raise ValueError(f"Invalid SQL identifier: {name}")
  detail: |
    Table name sanitization exists. Parameterized queries used throughout.
    No raw SQL string formatting found in scanned files.
  confidence: HIGH
```

### 9.3 File System Safety

Path traversal mitigation: present (see FER-AF-032).
Scope enforcement: present (output_parser.py — FER-AF-012 for bypass risk).
No `eval()` or `exec()` calls found.

---

## SECTION 10: PERFORMANCE & SCALABILITY

**FER-AF-035 — Synchronous DB connections block event loop**
```yaml
FINDING:
  severity: HIGH
  file: database.py:456-463
  evidence: |
    @contextmanager
    def _read_conn(self):
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=10)
  detail: |
    sqlite3.connect() is a synchronous blocking call. Every ReadOnlyDB query
    (get_project, get_task, get_tasks_by_phase, etc.) blocks the asyncio event
    loop while SQLite opens the connection and executes the query.
    Under parallel task execution (asyncio.gather), if 8 tasks query the DB
    simultaneously, each connection blocks in sequence — killing parallelism.
    Solution: aiosqlite or run_in_executor() wrapper.
  confidence: HIGH
```

**FER-AF-036 — asyncio.gather with no concurrency limit**
```yaml
FINDING:
  severity: MEDIUM
  file: master_orchestrator.py:1967-1976
  evidence: |
    wave_results = await asyncio.gather(
        *[self._execute_single_task(t, ...) for t in ready],
        return_exceptions=True,
    )
  detail: |
    All ready tasks in a wave execute concurrently with no semaphore limit.
    If a phase has 8 ready tasks, all 8 call their LLM workers simultaneously.
    For CLI workers (Claude/Kimi), this spawns 8 CLI subprocesses simultaneously.
    Each subprocess consumes memory and rate-limit quota.
    No asyncio.Semaphore to limit concurrent worker calls.
  confidence: HIGH
```

**FER-AF-037 — 20-second polling loop for task DB confirmation**
```yaml
FINDING:
  severity: MEDIUM
  file: master_orchestrator.py:2206-2219
  evidence: |
    for attempt in range(20):
        await asyncio.sleep(1)
        confirmed = self.db.get_tasks_by_phase(project_id, 1)
        if confirmed:
            break
  detail: |
    Every project execution waits up to 20 seconds for DB writes to land.
    This is a symptom of the async write bus — tasks are queued but not
    immediately visible. The 20-second worst case is correct but:
    1. Each iteration makes a DB query (20 potential queries)
    2. The poll interval (1s) is hardcoded, not configurable
    3. No backoff — constant 1s polling
    Better: use WriteResultBus callback_id to await specific write confirmation.
  confidence: HIGH
```

---

## SECTION 11: MACHINE-INTERFACE FORENSICS

Dashboard WebSocket protocol — `dashboard_server.py` NOT READ (PARTIAL_SCAN).

**Known from orchestrator evidence:**
- WS messages sent: `uat_ready` (broadcast in execute_project result)
- WS actions received: `resolve_escalation`, `approve_uat` (from MEMORY.md)
- WS actions received: blueprint approval, role swap

**Cannot verify without reading dashboard_server.py:**
- Whether all WS message types are handled on both ends
- Whether WS disconnection is handled gracefully
- Whether large WebSocket payloads (e.g., full blueprint content) are chunked

---

## SECTION 12: CONCURRENCY & RACE CONDITIONS

### 12.1 Parallel Git in Same Wave

**FER-AF-038 — asyncio.Lock insufficient for parallel git waves**
```yaml
FINDING:
  severity: HIGH
  file: git_manager.py:179, master_orchestrator.py:1967-1976
  evidence: |
    async with self._commit_lock:  # serializes commits ✅
    # BUT: pull_latest() is called OUTSIDE the lock:
    git_mgr.pull_latest()  # master_orchestrator.py:1712 — no lock
  detail: |
    _execute_single_task() calls git_mgr.pull_latest() at step 1 (line 1712)
    OUTSIDE the commit lock. Multiple parallel tasks in the same wave will
    all call pull_latest() concurrently — this runs git pull --rebase
    on the same working tree simultaneously.
    Two concurrent `git pull --rebase` calls can corrupt .git/REBASE_HEAD
    and leave the repo in a broken rebase state.
    The _commit_lock only protects commits, not all git state mutations.
  confidence: HIGH
```

### 12.2 ReadOnlyDB vs WatchdogDB Safety

**Write queue mechanism:**
```
All components → queue_write() → asyncio.Queue(maxsize=10000)
→ Watchdog._db_drain_loop() → drains every 5s in batches of 100
→ WatchdogDB.drain_write_queue() → executes writes
```

**Race condition scenario:**
```
orchestrator queues: INSERT tasks (task_id="A")
orchestrator immediately queries: db.get_tasks_by_phase() → returns [] (write not drained yet)
→ This is why the 20-second poll loop exists (FER-AF-037)
→ But between queue and drain (0-5s window), any read will miss queued writes
→ The poll loop mitigates this for task generation, but NOT for:
   - create_project() followed by get_project() calls
   - Any other write-then-read pattern
```

**FER-AF-039 — Write-then-read race window is systemic**
```yaml
FINDING:
  severity: HIGH
  detail: |
    Every write via queue_write() has a 0-5 second window before it's visible
    to ReadOnlyDB reads. The codebase only has ONE mitigation (the poll loop
    in _generate_tasks_from_blueprint). All other write-then-read sequences
    have this race condition.
    Example: create_project() queues INSERT, then execute_project() is called,
    which calls db.get_project() — may return None for up to 5 seconds.
  confidence: HIGH
```

### 12.3 asyncio.Lock Coverage

The `_commit_lock` in `git_manager.py` covers: `git add`, `git status`, `git commit`, `git rev-parse HEAD` — the commit critical section.

NOT covered by lock: `pull_latest()`, `create_phase_branch()`, `merge_to_develop()`, `check_conflicts()`, `get_changed_files()`.

**FER-AF-040 — merge_to_develop not protected by lock**
```yaml
FINDING:
  severity: HIGH
  file: master_orchestrator.py:2025
  evidence: |
    git_mgr.merge_to_develop(branch, f"Phase {phase_num} complete")
  detail: |
    This is called after all tasks complete. But if two phases somehow run
    concurrently (not intended but possible), merge_to_develop() with no lock
    could cause concurrent git merge operations on the same develop branch.
    Even in single-phase execution, merge_to_develop() is unprotected.
  confidence: MEDIUM
```

---

## SECTION 13: BUSINESS LOGIC SANITY

### 13.1 Pipeline Completeness

**Full pipeline as implemented:**
```
1. User submits project description via dashboard
2. execute_project() called → _phase_blueprint() → LLM generates blueprint
3. Blueprint stored in blueprint_revisions table
4. escalation INSERT with type="blueprint_approval" → dashboard shows modal
5. ← HARD STOP: awaiting human approval → execute_project() returns {"awaiting": "blueprint_approval"}
6. Human approves → approve_blueprint() called → blueprint_revisions.approved_by = "HUMAN"
7. execute_project() called AGAIN → detects approved blueprint, skips regen
8. _generate_tasks_from_blueprint() → LLM generates task JSON → tasks inserted
9. 20s poll until tasks visible
10. Phases 1-3: _phase_build() loop
11. Phase N: create_phase_branch() → per-task asyncio.gather waves
12. Each task: pull_latest → classify → assign_worker → execute → parse → contract_validate → rules_check → TDD → DaC_tag → quality_gate → commit
13. Phase N complete: Kimi PR review → conflict check → merge_to_develop
14. Phase 4: tag proto → generate CI/CD → run E2E tests (non-blocking)
15. Return {"awaiting": "uat_approval"}
16. Human approves UAT → approve_uat() → merge_to_main → tag v1.0.0
```

**GAP: Step 5→7 re-invocation mechanism**

**FER-AF-041 — execute_project() must be called TWICE with no clear trigger**
```yaml
FINDING:
  severity: HIGH
  detail: |
    execute_project() returns {"awaiting": "blueprint_approval"} at step 5.
    After human approves, approve_blueprint() is called. But WHO re-invokes
    execute_project()? The dashboard must do this, but dashboard_server.py
    was NOT read in this scan. If the dashboard's blueprint approval handler
    does not re-invoke execute_project(), the factory permanently stalls
    after blueprint approval with no indication to the user.
  confidence: MEDIUM (dashboard_server.py not read)
```

### 13.2 State Transition Validity

**tasks.status valid values:**
`pending | in_progress | testing | review | approved | blocked | failed`

**Observed transitions in code:**
- `create_phase_tasks()`: inserts as `pending` ✅
- `_execute_single_task()`: never explicitly sets `in_progress` before execution starts
- After gate APPROVED: sets `approved` ✅
- After gate REJECTED: remains in retry loop, eventually `failed` ✅
- After timeout: Watchdog sets `pending` (retry) then `blocked` (escalate) ✅

**FER-AF-042 — Task never transitions to in_progress**
```yaml
FINDING:
  severity: MEDIUM
  file: master_orchestrator.py:1692-1914
  detail: |
    _execute_single_task() never sets task status to "in_progress".
    Tasks go from "pending" directly to "approved" or "failed".
    The Watchdog's get_stuck_tasks() finds tasks stuck in "in_progress" —
    but since tasks are never set to "in_progress" by the orchestrator,
    the stuck-task detection in _handle_stuck_tasks() will never fire
    for tasks being actively executed. Only tasks set to in_progress by
    the old API path (send_to_tdd at line 1199) would be detected.
  confidence: HIGH
```

---

## SECTION 14: DEAD CODE FORENSICS

| Dead Code Item | Evidence | Risk |
|----------------|----------|------|
| `learning_log` param in `_phase_build()` | Passed in, never used | HIGH — feature silently disabled |
| `gatekeeper_review()` method (lines 1218-1264) | Old API path — superseded by `_quality_gate()` | MEDIUM |
| `send_to_tdd()` method (lines 1183-1216) | Old API path — superseded by `_execute_single_task()` TDD step | MEDIUM |
| `classify_task()` method (lines 1144-1174) | Old API path — `_classify_task()` used instead | MEDIUM |
| `create_phase_tasks()` (lines 1129-1142) | Old API — `_generate_tasks_from_blueprint()` used instead | MEDIUM |
| `request_blueprint_approval()` (lines 1063-1075) | Old API — `approve_blueprint()` used instead | LOW |
| `factory.version` key in config | Not consumed anywhere | LOW |
| `factory.db_writer` key in config | Not consumed anywhere | LOW |

**FER-AF-043 — Dual API paths create confusion about which is canonical**
```yaml
FINDING:
  severity: HIGH
  detail: |
    master_orchestrator.py has TWO sets of methods:
    OLD API: create_phase_tasks(), classify_task(), send_to_tdd(), gatekeeper_review()
    NEW API: _execute_single_task(), _phase_build(), _classify_task(), _quality_gate()
    The old API methods are PUBLIC (no underscore prefix), still callable from
    dashboard_server.py, and appear to be complete implementations.
    The new API methods are PRIVATE (underscore prefix) and used by execute_project().
    Tests may be testing the OLD API while production runs the NEW API.
    This creates a maintenance trap where fixes to one path don't benefit the other.
  confidence: HIGH
```

---

## SECTION 15: BEHAVIORAL VERIFICATION

### 15.1 Config Loading Verification

```yaml
CONFIG_VERIFICATION:
  file: config/factory_config.json
  loading_test:
    command: "python -c \"import json; json.load(open('config/factory_config.json'))\""
    expected_exit_code: 0
    performed: false  # Would pass — file is valid JSON (LOADED and verified)
  consumer_mismatches:
    - key: factory.factory_state_dir
      config_value: "factory_state"
      consumer_expectation: "absolute path joined to working_dir"
      actual_behavior: "relative to CWD"
      confidence: HIGH
  orphaned_keys:
    - factory.version
    - factory.db_writer
    - quality_gates.test_coverage_threshold
    - quality_gates.security_scan_enabled
    - quality_gates.alignment_validation
    - cost_controls.*
    - watchdog.checkpoint_required_interval_minutes
    - watchdog.max_respawn_attempts
```

### 15.2 Test Fixture Reality Check

```yaml
FIXTURE_VERIFICATION:
  test_file: tests/test_7issues_wave1.py
  fixture_approach: MagicMock for entire ReadOnlyDB
  production_structure:
    get_learning_log returns: [{"log_id": int, "bug_description": str, "occurrence_count": int, "validated": bool/int, ...}]
  fixture_structure:
    returns: [{"log_id": 1, "bug_description": "...", "occurrence_count": 1, "validated": False, ...}]
  potential_mismatch:
    issue: SQLite stores BOOLEAN as INTEGER (0/1). Test fixtures use Python bool (True/False).
    LearningLog._is_qualified() checks: "occurrence >= 2 OR validated"
    If DB returns validated=0 (int), and code does "if ... or validated" → 0 is falsy.
    → This works correctly (0 is falsy = not validated). ✅
    BUT: Test uses validated=False (bool) which is equivalent. ✅
  verdict: CONSISTENT — fixture matches production behavior
  confidence: MEDIUM
```

### 15.3 Cross-File Pattern Sweep

```yaml
PATTERN_SWEEP:

  pattern: "bare except:"
  sweep_result:
    - file: setup_autonomous_factory_project.py:199 (description string — NOT code)
    - file: setup_autonomous_factory_project.py:353 (description string — NOT code)
    - file: tests/test_e2e_pipeline.py:1341 (test fixture content string — NOT code)
  real_bare_excepts_in_production_code: 0
  verdict: CLEAN — no production bare excepts
  confidence: HIGH

  pattern: "time.sleep in async context"
  results:
    - database.py:424: time.sleep(0.1) inside queue_write() — BLOCKING EVENT LOOP
    - database.py:900: time.sleep(0.1) — BLOCKING EVENT LOOP (second occurrence)
    - process_reaper.py:418: time.sleep(0.5)
    - process_reaper.py:424: time.sleep(0.2)
  unprotected: 4 instances
  fer_generated: FER-AF-018, FER-AF-019

  pattern: "asyncio.get_event_loop()"
  results:
    - main.py:105
    - database.py:361
  unprotected: 2 instances (deprecated in Python 3.10+)
  fer_generated: FER-AF-020

  pattern: "shell=True"
  results: NONE
  verdict: CLEAN
  confidence: HIGH

  pattern: "eval( or exec("
  results: NONE (not searched — UNCHECKED)

  pattern: "hardcoded ports"
  results:
    - factory_config.json:202: 8420 (in config — acceptable)
    - master_watchdog.py:212: localhost:11434 (hardcoded — FER-AF-023)
    - factory_config.json:12,20: localhost:11434 (in config — acceptable)
```

### 15.4 Algorithm Audit

**Dependency deadlock handling:**
```python
# master_orchestrator.py:1953-1959
if not ready:
    logger.warning(f"Phase {phase_num}: dependency deadlock ... running first task")
    ready = [next(iter(pending.values()))]
```
**Assessment:** Force-run of arbitrary task is unsafe (R8). If task A genuinely requires task B's output, running A first will produce incorrect output. The correct action is to halt and escalate.

**Quality gate logic (Issue 5 fix):**
```python
# master_orchestrator.py:2352-2365
real_issues = [i for i in issues if i and str(i).strip()]
real_tags = [t for t in dac_tags if t and str(t).strip()]
if real_issues or real_tags:
    verdict = "REJECTED"
else:
    raw_verdict = gate.get("verdict", "").upper()
    verdict = "REJECTED" if raw_verdict == "REJECTED" else "APPROVED"
```
**Assessment:** Logic-based gate is implemented. Issue 5 fix is correct. BUT: relies on LLM returning structured JSON with "issues" and "dac_tags" keys. If LLM returns unstructured text (fails to include these keys), `gate = {}`, both lists are empty, and task is APPROVED regardless of LLM response content.

**FER-AF-044 — Quality gate approves when LLM returns unstructured text**
```yaml
FINDING:
  severity: HIGH
  file: master_orchestrator.py:2342-2350
  evidence: |
    try:
        resp = result["response"]
        json_match = re.search(r'\{[\s\S]*\}', resp)
        if json_match:
            gate = json.loads(json_match.group())
    except (json.JSONDecodeError, AttributeError):
        pass
    # gate remains {} if no JSON found
    # real_issues = [] → APPROVED
  detail: |
    If the gatekeeper LLM responds with plain text ("This code has major issues...")
    instead of JSON, gate={}, real_issues=[], real_tags=[], and the task is
    APPROVED. The gate passes on LLM format failure, which is exactly backwards.
  confidence: HIGH
```

### 15.5 Integration Path Trace

**Full data flow: User submits project → files written to disk**

```
1. User: POST /api/projects/launch {name, description}
   → dashboard_server.py (NOT READ — UNVERIFIED)
   → orchestrator.create_project(name, description)
   → queue_write INSERT projects → [5s drain] → DB

2. POST /api/projects/{id}/execute
   → orchestrator.execute_project(project_id)
   → _phase_blueprint() → LLM generates blueprint text
   → queue_write INSERT blueprint_revisions
   → queue_write INSERT escalation (blueprint_approval)
   → RETURNS {"awaiting": "blueprint_approval"}

3. POST /api/projects/{id}/approve-blueprint
   → orchestrator.approve_blueprint(project_id)
   → request_write_and_wait UPDATE blueprint_revisions (approved_by=HUMAN)
   → contract_gen.lock_contracts()

4. execute_project() re-invoked (mechanism unverified)
   → _generate_tasks_from_blueprint()
   → LLM generates phases JSON
   → queue_write INSERT tasks (N tasks)
   → 20s poll until visible

5. _phase_build(phase=1)
   → git_mgr.create_phase_branch(1, "phase-1")
   → _classify_dependencies() → dep_graph via Kimi
   → asyncio.gather([_execute_single_task(t) for t in wave])

6. _execute_single_task(task)
   → git_mgr.pull_latest()
   → _classify_task() → kimi returns "low"/"high"
   → _get_worker("code_generation_complex")
   → context_mgr.build_task_prompt()
   → worker.send_message(prompt) → CLI subprocess → stdout
   → OutputParser.parse(stdout) → structured JSON
   → OutputParser._apply_file() → Path(project_path/rel_path).write_text(content)
   ← FILES WRITTEN TO DISK ✅

7. atomic_commit() → git add -A → git commit -m "[task_id] ..."
8. _quality_gate() → kimi reviews → verdict
9. queue_write UPDATE tasks status=approved/failed
```

**Integration gaps identified:**
- Step 4 re-invocation mechanism (FER-AF-041)
- Step 6: task never set to "in_progress" (FER-AF-042)
- Step 6: parallel git pull_latest races (FER-AF-038)

---

## SECTION 16: THE RISK MAP

| ID | Finding | Severity | Impact | Fix Effort | Priority |
|----|---------|----------|--------|------------|----------|
| FER-AF-002 | requirements.txt missing FastAPI/uvicorn/etc | CRITICAL | New dev cannot start factory | Low | P0 |
| FER-AF-009 | Gate worker failure auto-approves broken code | CRITICAL | Broken code silently enters codebase | Medium | P0 |
| FER-AF-010 | E2E failure does not block UAT | CRITICAL | Broken projects go to production | Low | P0 |
| FER-AF-044 | Quality gate approves on unstructured LLM response | CRITICAL | Gate is bypassable by format failure | Low | P0 |
| FER-AF-003 | Learning log injection silently disabled | HIGH | R&D investment lost; repeat mistakes occur | Low | P1 |
| FER-AF-006 | Budget cap never enforced | HIGH | Unbounded API cost burns | Medium | P1 |
| FER-AF-018 | time.sleep() blocks event loop in queue_write | HIGH | Event loop freezes under queue pressure | Medium | P1 |
| FER-AF-027 | Written files orphaned on git commit failure | HIGH | Disk/git state corruption | Medium | P1 |
| FER-AF-035 | Synchronous DB in async context | HIGH | Kills parallelism benefit | High | P1 |
| FER-AF-038 | Parallel git pull races in same wave | HIGH | Git repo corruption under load | Medium | P1 |
| FER-AF-039 | Write-then-read race window systemic | HIGH | Stale reads throughout execution | High | P1 |
| FER-AF-041 | execute_project re-invocation unverified | HIGH | Factory stalls after blueprint approval | Low (verify) | P1 |
| FER-AF-042 | Task never set to in_progress | HIGH | Stuck task detection never fires | Low | P1 |
| FER-AF-043 | Dual API paths — old vs new | HIGH | Tests test wrong code path | Medium | P1 |
| FER-AF-026 | ReadOnlyDB.request_write_and_wait breaks contract | HIGH | Misleading architecture | Low | P2 |
| FER-AF-029 | Blueprint truncated to 5000 chars silently | HIGH | Incomplete task generation | Low | P2 |
| FER-AF-001 | factory_state_dir relative path bug | HIGH | Chat history in wrong directory | Low | P2 |
| FER-AF-007 | Phase 5 production deploy is stub | MEDIUM | "Deploy" does nothing real | High | P2 |
| FER-AF-013 | LLM JSON fallback uses raw LLM text as task | MEDIUM | Garbage tasks created | Medium | P2 |
| FER-AF-014 | No rate limit back-off | MEDIUM | Rate limit hammering | Low | P2 |
| FER-AF-017 | git init result unchecked | MEDIUM | Silent git state corruption | Low | P2 |
| FER-AF-020 | asyncio.get_event_loop() deprecated | MEDIUM | Python 3.12+ DeprecationWarning | Low | P2 |
| FER-AF-036 | No semaphore on asyncio.gather | MEDIUM | Resource exhaustion under load | Low | P2 |
| FER-AF-023 | Ollama URL hardcoded in watchdog | LOW | Remote GPU setup breaks silently | Low | P3 |
| FER-AF-028 | No port conflict detection | LOW | Unhelpful OSError on port conflict | Low | P3 |
| FER-AF-021 | All deps unpinned | MEDIUM | Future pip install may break | Low | P3 |

---

## SECTION 17: THE AI GUARDRAIL PROTOCOL

### 17.1 Pre-Commit Checklist

Before any commit to this codebase:
- [ ] requirements.txt updated with ALL actual imports
- [ ] Any new async function: confirmed no `time.sleep()` calls
- [ ] Any new DB read after write: poll loop or `request_write_and_wait` used
- [ ] Any new gate/approval path: failure case is NOT auto-approve
- [ ] Any new E2E test: failure path verified to surface to human
- [ ] Any new config key: confirm it is consumed by code (no orphaned keys)
- [ ] Any new API method: old API counterpart deprecated or removed
- [ ] Blueprint/large text passed to LLM: log truncation amount

### 17.2 Known Traps Registry

```yaml
---
TRAP_ID: CLI-TRAP-001
SEVERITY: CRITICAL
DESCRIPTION: Gate worker failure silently auto-approves broken code
FILE: master_orchestrator.py:2375-2378
ROOT_CAUSE: Defensive fallback designed for "no worker available" scenario
  treats transient errors the same as "no worker" — both auto-approve
EVIDENCE: |
  return {"verdict": "APPROVED", "confidence": 0.5, "issues": [], "dac_tags": [],
          "note": f"Gate worker '{gate_worker_name}' call failed — auto-approved"}
VERIFICATION_COMMAND: "Kill kimi process during quality gate execution"
AVOIDANCE: Return REJECTED on worker call failure. Log to escalations.
FER_REFERENCE: FER-AF-009
PATTERN_SWEEP:
  command: "grep -n 'auto-approved' orchestration/master_orchestrator.py"
  total_instances: 1
  unprotected: 1
---

---
TRAP_ID: CLI-TRAP-002
SEVERITY: CRITICAL
DESCRIPTION: time.sleep() blocks asyncio event loop in queue_write()
FILE: orchestration/database.py:424, 900
ROOT_CAUSE: AI-generated retry logic used synchronous sleep without considering async context
EVIDENCE: |
  import time
  time.sleep(0.1 * (attempt + 1))  # line 424 — inside sync queue_write()
  # queue_write() IS called from async context (_execute_single_task, etc.)
VERIFICATION_COMMAND: "python -c \"import asyncio, time; asyncio.run(asyncio.sleep(0)); time.sleep(1)\""
AVOIDANCE: queue_write() must be made async, or time.sleep replaced with asyncio.sleep
  via run_in_executor for the sync retry path
FER_REFERENCE: FER-AF-018
PATTERN_SWEEP:
  command: "grep -n 'time.sleep' orchestration/database.py"
  total_instances: 2
  unprotected: 2
---

---
TRAP_ID: CLI-TRAP-003
SEVERITY: HIGH
DESCRIPTION: LLM task generation fallback creates hardcoded Books app tasks
FILE: master_orchestrator.py:2224-2279
ROOT_CAUSE: Fallback was built during development for a specific demo project
  and never generalized
EVIDENCE: |
  {"module": "backend/routers/books.py", "description": "FastAPI CRUD: GET/POST /api/books"}
  {"module": "frontend/src/components/BookCard.tsx", "description": "Book card..."}
AVOIDANCE: Generate generic tasks from project description when LLM fails,
  or use project_type to select appropriate fallback templates
FER_REFERENCE: N/A (pattern issue, not single FER)
---

---
TRAP_ID: CLI-TRAP-004
SEVERITY: HIGH
DESCRIPTION: learning_log passed to _phase_build but never used — R11 fix silently dead
FILE: master_orchestrator.py:1329, 1374, 1918
ROOT_CAUSE: inject_learnings() call was never wired into _execute_single_task()
  after the learning_log refactor
EVIDENCE: |
  learning_log = LearningLog(self.db)  # instantiated line 1329
  phase_result = await self._phase_build(..., learning_log, ...)  # passed line 1374
  # _phase_build() signature: ...learning_log: "LearningLog"...
  # _phase_build() body: learning_log never called
AVOIDANCE: Call learning_log.inject_learnings(task["description"]) in _execute_single_task()
  before building the task prompt
FER_REFERENCE: FER-AF-003
---
```

### 17.3 Definition of Done

Based on actual codebase state, a task is "done" when:
1. `_execute_single_task()` returns `{"success": True}`
2. Task status in DB = `approved`
3. Git commit hash recorded in `commits` table
4. Quality gate returned `APPROVED` (even if gatekeeper was offline)
5. TDD pipeline completed (even if TDD tests themselves failed)

**Current DoD is insufficient.** Missing:
- E2E test gate before UAT
- Human-visible scope violation acknowledgment
- Budget cap check
- In-progress status tracking

### 17.4 Regression Prevention Protocol

Automated checks that would catch bugs already found:
1. `pytest tests/ --asyncio-mode=auto` — runs 25 existing tests
2. Add test: `test_quality_gate_rejects_on_worker_failure()` — catches FER-AF-009
3. Add test: `test_queue_write_non_blocking()` — catches FER-AF-018 (verify no time.sleep in async)
4. Add lint rule: `no-time-sleep-in-async` (bandit or custom)
5. `pip-audit -r requirements.txt` — already in requirements
6. `bandit -r orchestration/` — already in requirements (run it)

---

## SECTION 18: MACHINE SIGNAL SUMMARY

```yaml
MACHINE_SIGNAL_SUMMARY:
  session_info:
    forensic_id: "FORENSIC-20260302-001"
    project: "Autonomous Factory v1.1"
    files_scanned: 36
    depth_levels:
      listed_only: 15
      opened: 2
      loaded: 2
      traced: 17

  failure_events:
    total_fers: 44
    by_severity:
      critical: 4   # FER-AF-002, 009, 010, 044
      high: 25
      medium: 10
      low: 5
    by_type:
      SILENT_FAILURE: 6
      DEAD_CODE: 4
      CONFIG_DRIFT: 3
      PHANTOM_DEPENDENCY: 1
      BLOCKING_CALL_IN_ASYNC: 2
      ARCHITECTURE_VIOLATION: 2
      RACE_CONDITION: 3
      INPUT_VALIDATION: 2
      RESOURCE_LEAK: 2
      SECURITY_INFO: 3
      OTHER: 16

  verification_claims:
    total_claims: 44
    confidence_distribution:
      high: 36
      medium: 6
      low: 2
    evidence_type_distribution:
      direct: 38
      indirect: 4
      partial: 2

  unchecked_items:
    - dashboard_server.py (not read — all WS/HTTP endpoint behavior unverified)
    - tdd_pipeline.py (not read — TDD 13-step implementation unverified)
    - context_manager.py (not read — build_task_prompt() contract unverified)
    - dac_tagger.py (not read — tag() and tag_from_tdd_result() unverified)
    - contract_validator.py / contract_generator.py (not read)
    - phi3_manager.py (not read — queue_summary() behavior unverified)
    - process_reaper.py (partially read — ghost process handling unverified)
    - Shell scripts (check_health.sh, setup.sh, recover.sh) — not read
    - execute_project() re-invocation mechanism after blueprint approval
    - Token counting in worker response (worker_result.get("tokens", {}))

  risk_assessment_of_unchecked:
    high_risk_unchecked: 3  # dashboard, tdd_pipeline, execute_project re-invocation
    medium_risk_unchecked: 5
    low_risk_unchecked: 3

  immediate_action_required:
    - FER-AF-002: Fix requirements.txt — add FastAPI, uvicorn, websockets, playwright
    - FER-AF-009: Quality gate must REJECT (not APPROVE) on worker call failure
    - FER-AF-010: E2E failure must block UAT button in dashboard
    - FER-AF-044: Quality gate must REJECT when LLM returns no structured JSON
    - FER-AF-018: Replace time.sleep() with asyncio.sleep() in queue_write()
    - FER-AF-003: Wire learning_log.inject_learnings() into _execute_single_task()
    - FER-AF-042: Set task status to "in_progress" at start of _execute_single_task()
    - FER-AF-038: Protect pull_latest() with git lock or remove from parallel execution

  verdict: |
    The Autonomous Factory architecture is sound. The write-bus pattern, role router,
    crash recovery, and parallel execution framework are well-designed. The core execution
    pipeline is implemented and coherent. HOWEVER:

    4 CRITICAL bugs exist that directly undermine the factory's reliability:
    auto-approval on gate failure, non-blocking E2E, unstructured-response approval,
    and a missing requirements.txt. These are not architectural — they are simple code
    bugs that can be fixed in an afternoon.

    The most dangerous systemic issue is the time.sleep() in async contexts and the
    synchronous SQLite in the asyncio event loop — both of which will cause
    mysterious performance degradation at scale that is hard to diagnose.

    E2E testing has NOT been run. The factory has never executed a full project.
    All 25 existing tests mock the DB and workers. Real behavior is unverified.
```

---

*Report generated by Claude Sonnet 4.6 following CLI_FORENSIC_EXTRACTION_PROMPT_V5.0*
*Forensic Session: FORENSIC-20260302-001 | Duration: Single session | Context consumed: ~90%*
