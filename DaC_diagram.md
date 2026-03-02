# Autonomous Factory — Diagram as Code (DaC)

## 1. System Architecture Overview

```mermaid
graph TB
    subgraph ENTRY["Entry Point"]
        MAIN["main.py\n─────────────\nsetup_logging()\n_load_config()"]
    end

    subgraph INFRA["Infrastructure Layer"]
        WD["MasterWatchdog\n─────────────\nPID 1 / Sole DB Writer\nProcess Monitor\nTask Allocator\n_db_drain_loop()\n_monitoring_loop()"]
        PR["ProcessReaper\n─────────────\nGhost Process Prevention\nHeartbeat Tracking\nPGID Kill Groups"]
        WSP["WatchdogStatePersistence\n─────────────\nCrash Recovery\nwatchdog_state.json\nHeartbeat File"]
    end

    subgraph DB_LAYER["Database Layer (SQLite WAL)"]
        WDB["WatchdogDB\n─────────────\nWrite-Capable\nSingle Instance\nOwned by Watchdog"]
        RDB["ReadOnlyDB\n─────────────\nDistributed Copies\nOrchestrator · Dashboard\nWorkers"]
        BUS["Message Bus\n─────────────\nasyncio.Queue\nWriteResultBus\nBatched every 5s"]
    end

    subgraph ORCH_LAYER["Orchestration Layer"]
        ORCH["MasterOrchestrator\n─────────────\nCOO — In-Memory\nChat Routing\nProject Execution\nSession Management"]
        PHI3["Phi3Manager\n─────────────\nDoC Scribes\nphi3-orchestrator\nRolling Context\nSummarization"]
        RR["RoleRouter\n─────────────\nRole ↔ Worker Map\nHot-Swap\nFallback Support\nLocal Mode Toggle"]
    end

    subgraph EXECUTION["Autonomous Execution Engine"]
        TDD["TDDPipeline\n─────────────\n13-Step Pipeline\nAC→RED→GREEN→BC→BF\n→SEA→DS→OA→VB→GIT\n→CL→CCP→AD"]
        CM["ContextManager\n─────────────\nProtocol Injection\nContract Loading\nProject Context\nTask Context"]
        CG["ContractGenerator\n─────────────\napi_contract.json\ndb_schema.sql\ntypes.json"]
        CV["ContractValidator\n─────────────\nCompleteness Check\nAlignment Validation\nContract Locking"]
        OP["OutputParser\n─────────────\nJSON Extraction\nFile Writer\nDecision Router"]
        RE["RulesEngine\n─────────────\nDefault Rules (R001-R009)\nProject Rules\nPrompt Injection\nViolation Detection"]
        GM["GitManager\n─────────────\nmain/develop/phase/{N}\nAtomic Commits\nPR Creation\nConflict Detection"]
    end

    subgraph INTELLIGENCE["Intelligence Layer"]
        DT["DaCTagger\n─────────────\nTRAP/SER/DOM\nHRO/HAL/ENV\nAuto-Tagging\nTraining Export"]
        LL["LearningLog\n─────────────\nCross-Project Learning\nDe-duplication\nOccurrence Counting\nPrompt Injection"]
        SA["StaticAnalyzer\n─────────────\nOWASP Scanning\nCode Analysis\nSecurity Findings"]
        CICD["CICDGenerator\n─────────────\nCI/CD Pipeline\nGitHub Actions\nAutomated Builds"]
    end

    subgraph WORKERS["Worker Layer"]
        CLI_W["CLIWorkerAdapter\n─────────────\nSubprocess Spawn\nPipe I/O\nPGID Kill Groups\nCLAUDECODE Strip"]
        OLLAMA_W["OllamaWorkerAdapter\n─────────────\nHTTP POST\nlocalhost:11434\nStream Response\naiohttp Session"]
        subgraph CLI_WORKERS["CLI Workers"]
            CLAUDE["Claude\nclaude CLI"]
            KIMI["Kimi\nkimi CLI"]
            GEMINI["Gemini\ngemini CLI\ndual_auth"]
        end
        subgraph LOCAL_WORKERS["Local Ollama Workers"]
            DEEPSEEK["DeepSeek\ndeepseek-coder-v2:16b"]
            QWEN["Qwen\nqwen2.5-coder:7b"]
            PHI3_W["Phi3\nphi3:mini\n×6 instances"]
        end
    end

    subgraph DASHBOARD["Dashboard Layer"]
        DS["DashboardServer\n─────────────\naiohttp Web Server\nREST API (40+ routes)\nWebSocket Hub\nBroadcast Loop (2s)"]
        UI["Web UI\n─────────────\nhttp://127.0.0.1:8420\nChat Tabs\nRole Management\nProject Controls\nLive Status"]
    end

    subgraph CONFIG["Configuration & Protocols"]
        CFG["factory_config.json\n─────────────\nWorker Configs\nRole Assignments\nWatchdog Settings\nDashboard Settings"]
        PROTO["protocols/\n─────────────\nweb.md\nmobile.md\niot.md\nplm.md\nblueprint_protocol.md"]
    end

    %% Startup Chain
    MAIN -->|"1. boot()"| WD
    MAIN -->|"2. start_all()"| PHI3
    MAIN -->|"3. init"| ORCH
    MAIN -->|"4. start()"| DS

    %% Infrastructure wiring
    WD --> PR
    WD --> WSP
    WD -->|"owns"| WDB
    WD -->|"drains"| BUS
    WDB -->|"read-only copy"| RDB
    RDB -->|"async write requests"| BUS
    BUS -->|"batch execute"| WDB

    %% Orchestration wiring
    ORCH -->|"routes via"| RR
    ORCH -->|"reads"| RDB
    ORCH --> TDD
    ORCH --> CM
    ORCH --> CG
    ORCH --> CV
    ORCH --> OP
    ORCH --> RE
    ORCH --> GM
    ORCH --> DT
    ORCH --> LL
    PHI3 -->|"DoC updates"| ORCH

    %% Role Router → Workers
    RR -->|"get_worker(role)"| CLI_W
    RR -->|"get_worker(role)"| OLLAMA_W
    CLI_W --> CLAUDE
    CLI_W --> KIMI
    CLI_W --> GEMINI
    OLLAMA_W --> DEEPSEEK
    OLLAMA_W --> QWEN
    OLLAMA_W --> PHI3_W

    %% Dashboard connections
    DS -->|"set_orchestrator()"| ORCH
    DS --> RDB
    DS --> RR
    DS -->|"serves"| UI

    %% Config loading
    CFG -->|"loaded by"| MAIN
    PROTO -->|"injected by"| CM

    %% Intelligence connections
    TDD --> DT
    TDD --> LL
    TDD --> SA
    ORCH --> CICD
```

---

## 2. TDD Pipeline (13-Step Wave)

```mermaid
stateDiagram-v2
    [*] --> AC: Task assigned

    AC: Step 1 — AC\nAcceptance Criteria\n(AC_Team)
    RED: Step 2 — RED\nWrite Failing Tests\n(TDE_RED)
    GREEN: Step 3 — GREEN\nMinimal Implementation\n(TDE_GREEN)
    BC: Step 4 — BC\nBug Capture Scan\n(Bug_Capture)
    BF: Step 5 — BF\nBug Fix\n(Bug_Fix)
    SEA: Step 6 — SEA\nSilent Error Analysis\n(Silent_Error)
    DS: Step 7 — DS\nOWASP Security Scan\n(StaticAnalyzer)
    OA: Step 8 — OA\nOutput Alignment\n(Output_Align)
    VB: Step 9 — VB\nVersion Bump
    GIT: Step 10 — GIT\nAtomic Commit\n(GitManager)
    CL: Step 11 — CL\nCleanup Artifacts
    CCP: Step 12 — CCP\nCheckpoint State
    AD: Step 13 — AD\nDashboard Update

    AC --> RED
    RED --> GREEN
    GREEN --> BC
    BC --> BF: bugs found
    BC --> SEA: no bugs
    BF --> SEA
    SEA --> DS
    DS --> OA
    OA --> VB
    VB --> GIT
    GIT --> CL
    CL --> CCP
    CCP --> AD
    AD --> [*]: Wave complete

    BC --> ESCALATE: critical issue
    BF --> ESCALATE: unfixable
    SEA --> ESCALATE: severe drift
    DS --> ESCALATE: security violation
    ESCALATE: ESCALATION\n(Human Review)
    ESCALATE --> [*]: blocked
```

---

## 3. Chat Request Flow

```mermaid
sequenceDiagram
    participant U as User (Browser)
    participant DS as DashboardServer
    participant MO as MasterOrchestrator
    participant RR as RoleRouter
    participant WA as WorkerAdapter
    participant LLM as LLM Worker
    participant DB as Database

    U->>DS: WebSocket action=chat_message
    DS->>MO: handle_message(user_msg, session_id)
    MO->>DB: _append_history("user", msg)
    MO->>MO: _route_intent(msg) → detect mode

    alt orchestrator mode
        MO->>RR: get_worker("tdd_analysis")
    else direct mode
        MO->>RR: get_worker(worker_name)
    else project mode
        MO->>MO: prepend project context
        MO->>RR: get_worker(worker_name)
    else discussion mode
        loop each participant
            MO->>RR: get_worker(participant)
        end
    end

    RR->>WA: route to adapter

    alt CLI Worker (Claude/Kimi/Gemini)
        WA->>LLM: subprocess spawn + pipe I/O
        LLM-->>WA: stdout response
    else Ollama Worker (DeepSeek/Qwen/Phi3)
        WA->>LLM: HTTP POST /api/generate
        LLM-->>WA: streamed response
    end

    WA-->>MO: {success, response, worker}
    MO->>DB: _append_history("assistant", response, metadata)
    MO->>MO: _save_chat_history()
    MO-->>DS: response dict
    DS->>U: broadcast chat_message event
```

---

## 4. Project Execution Flow

```mermaid
flowchart TD
    START([User: Launch Project]) --> DS_WS[DashboardServer\naction=launch_project]
    DS_WS --> EXEC[MasterOrchestrator\nexecute_project]

    EXEC --> PHASE_CHECK{Phase == 0?}

    PHASE_CHECK -->|Yes| BLUEPRINT[_phase_blueprint]
    BLUEPRINT --> B1[Route to blueprint_generation worker]
    B1 --> B2[ContextManager.load_protocol\nproject_type]
    B2 --> B3[Generate Blueprint v1]
    B3 --> B4[ContractGenerator\napi_contract + db_schema + types]
    B4 --> B5[ContractValidator\ncompleteness + alignment]
    B5 --> B6[Dual Audit via gatekeeper_review]
    B6 --> B7{Approved?}
    B7 -->|No| ESCALATE_BP[Escalate to Human]
    B7 -->|Yes| LOCK[Lock Contracts\nIMMUTABLE]

    PHASE_CHECK -->|No| BUILD[_phase_build]
    LOCK --> BUILD

    BUILD --> TASK_LOOP[FOR EACH task in phase]
    TASK_LOOP --> SINGLE[_execute_single_task]

    SINGLE --> CONTEXT[Build Prompt Context\nprotocol + contracts\nrules + learnings]
    CONTEXT --> TDD_RUN[TDDPipeline.execute_task\n13-step pipeline]
    TDD_RUN --> QG[_quality_gate\nPARALLEL validation]

    QG --> BC_G[BC — Bug Capture]
    QG --> BF_G[BF — Bug Fix]
    QG --> SEA_G[SEA — Silent Error]
    QG --> DBC_G[DBC — DB Concurrency]
    QG --> SCA_G[SCA — Schema Alignment]
    QG --> MOCK_G[MOCK — Edge Cases]
    QG --> SEC_G[SEC — Security OWASP]
    QG --> VMSA_G[VMSA — Pixel Diff UI]

    BC_G & BF_G & SEA_G & DBC_G & SCA_G & MOCK_G & SEC_G & VMSA_G --> GATE_RESULT{All Gates Pass?}

    GATE_RESULT -->|No| ESCALATE_TASK[Escalate / Retry]
    GATE_RESULT -->|Yes| COMMIT[GitManager\nAtomic Commit]
    COMMIT --> NEXT_TASK{More tasks?}
    NEXT_TASK -->|Yes| TASK_LOOP
    NEXT_TASK -->|No| E2E[Run E2E Tests\nPlaywright Full Walkthrough]

    E2E --> E2E_RESULT{E2E Passed?}
    E2E_RESULT -->|No| UAT_BLOCKED["awaiting: uat_blocked_e2e\nlogger.warning — UAT locked"]
    E2E_RESULT -->|Yes| SCREENSHOTS[Capture Screenshots\nscreenshots/wave-N/]

    UAT_BLOCKED --> FIX_E2E[Fix failing tests\nnew wave required]
    FIX_E2E --> E2E

    SCREENSHOTS --> REVIEW{User Review}
    REVIEW -->|Changes needed| NEXT_WAVE[New Wave / Phase]
    REVIEW -->|Approved| UAT_APPROVAL["awaiting: uat_approval\napprove_uat() unblocked"]
    UAT_APPROVAL --> VISUAL_BASE[Visual Baseline\nfor VMSA]
    VISUAL_BASE --> DONE([Phase Complete])
```

---

## 5. Database Write Flow (Message Bus)

```mermaid
sequenceDiagram
    participant C as Caller\n(Orchestrator / Dashboard / Worker)
    participant Q as Write Queue\n(asyncio.Queue)
    participant BUS as WriteResultBus
    participant WD as MasterWatchdog\n_db_drain_loop()
    participant DB as SQLite DB\n(WatchdogDB)

    C->>BUS: create_waiter(callback_id)
    C->>Q: push DBWriteRequest\n{op, table, params, callback_id}
    C->>C: await Future(callback_id, timeout=30s)

    loop Every 5 seconds
        WD->>Q: dequeue all pending writes
        note over WD: drain_write_queue() — async
        WD->>WD: asyncio.to_thread(_drain_batch_sync, writes)
        note over WD: ← sync SQLite work runs in thread pool
        WD->>DB: BEGIN TRANSACTION
        loop Each write request
            WD->>DB: _execute_write(conn, write)
            DB-->>WD: result
        end
        WD->>DB: COMMIT
        WD-->>WD: thread returns (results list)
        note over WD: back in async context — thread-safe Future resolution
        WD->>BUS: resolve(callback_id, result)
    end

    BUS-->>C: Future resolves → result returned
```

---

## 6. Worker Role Mapping

```mermaid
graph LR
    subgraph ROLES["Abstract Roles"]
        R1["code_generation_simple"]
        R2["code_generation_complex"]
        R3["tdd_testing"]
        R4["tdd_analysis"]
        R5["gatekeeper_review"]
        R6["architecture_audit"]
        R7["task_planning_gsd"]
        R8["blueprint_generation"]
        R9["summarization"]
        R10["frontend_design"]
        R11["project_classification"]
    end

    subgraph WORKERS["Concrete Workers"]
        CL["Claude\ncli_login"]
        KM["Kimi\ncli_login"]
        GM["Gemini\ndual_auth"]
        DS["DeepSeek\nlocal_ollama"]
        QW["Qwen\nlocal_ollama"]
        PH["Phi3\nlocal_ollama × 6"]
    end

    R1 -->|primary| DS
    R1 -->|fallback| QW
    R2 -->|primary| CL
    R2 -->|fallback| GM
    R3 -->|primary| CL
    R3 -->|fallback| GM
    R4 -->|primary| KM
    R4 -->|fallback| CL
    R5 -->|primary| GM
    R5 -->|fallback| CL
    R6 -->|primary| CL
    R6 -->|fallback| KM
    R7 -->|primary| KM
    R7 -->|fallback| CL
    R8 -->|primary| CL
    R8 -->|fallback| GM
    R9 -->|primary| PH
    R9 -.->|hot-swap| DS
    R10 -->|primary| GM
    R10 -->|fallback| CL
    R11 -->|primary| PH
    R11 -->|fallback| KM
```

---

## 7. DaC Tagging & Learning Flow

```mermaid
flowchart LR
    subgraph EVENTS["Runtime Events"]
        E1["Bug Found\n(Bug_Capture)"]
        E2["Silent Error\n(SEA)"]
        E3["Security Issue\n(OWASP Scan)"]
        E4["Contract Violation\n(Schema_Guard)"]
        E5["Merge Conflict\n(GitManager)"]
        E6["Malformed Output\n(OutputParser)"]
        E7["Task Timeout\n(Watchdog)"]
        E8["Hallucination\n(SEA)"]
        E9["Config Error\n(RulesEngine)"]
    end

    subgraph TAGS["DaC Tag Types"]
        DOM["DOM\nDomain Mistakes\nBug Patterns"]
        HAL["HAL\nSilent Errors\nHallucinations\nTimeouts"]
        SER["SER\nSecurity\nContracts\nMerge Conflicts"]
        TRAP["TRAP\nRule Violations\nMalformed Output"]
        HRO["HRO\nHuman Review\nRequired"]
        ENV["ENV\nEnvironment\nConfig Issues"]
    end

    subgraph STORAGE["Storage"]
        DACDB["dac_tags\n(DB Table)"]
        TRAIN["training_data\n(DB Table)"]
        LEARN["learning_log\n(DB Table)"]
    end

    subgraph INJECT["Prompt Injection"]
        LL_INJ["LearningLog\n.inject_learnings()\ncount ≥ 2 OR validated"]
        CM_INJ["ContextManager\nbuild_task_context()"]
    end

    E1 --> DOM
    E2 --> HAL
    E3 --> SER
    E4 --> SER
    E5 --> SER
    E6 --> TRAP
    E7 --> HAL
    E8 --> HAL
    E9 --> ENV

    DOM & HAL & SER & TRAP & HRO & ENV --> DACDB
    DACDB -->|"flush_to_training_data()"| TRAIN
    TRAIN -->|"log_fix() + de-duplicate"| LEARN
    LEARN --> LL_INJ
    LL_INJ --> CM_INJ
    CM_INJ -->|"injected into next worker prompt"| WORKERS2["Worker Prompt\n(prevents repeat mistakes)"]
```

---

## 8. Dashboard WebSocket API

```mermaid
graph TB
    subgraph CLIENT["Browser Client"]
        WS_OUT["Outbound Actions\n─────────────\nchat_message\ndirect_chat\nproject_chat\ndiscussion_chat\nswap_role\nlaunch_project\nselect_project\nnew_session\nswitch_session\nrename_session\nclose_session\nresolve_escalation\ndiscussion_cancel\nchat_stop"]
        WS_IN["Inbound Events\n─────────────\nstatus_update (2s)\nchat_message\nproject_launched\nproject_progress\nproject_completed\nrole_swapped\nsession_changed\ntask_assigned\nescalation_raised\nchat_stopped\nerror"]
    end

    subgraph SERVER["DashboardServer"]
        WS_HANDLER["_websocket(req)\nMain WS Handler\nAction Dispatcher"]
        BROADCAST["_broadcast()\nBroadcast Loop\nevery 2 seconds"]
        REST["REST API\n─────────────\n/api/status\n/api/tasks\n/api/roles\n/api/workers\n/api/chat/*\n/api/projects/*\n/api/config/*"]
    end

    WS_OUT -->|"WebSocket"| WS_HANDLER
    WS_HANDLER --> BROADCAST
    BROADCAST -->|"WebSocket"| WS_IN
    REST -->|"HTTP"| CLIENT
```

---

## 9. Process Lifecycle & Crash Recovery

```mermaid
stateDiagram-v2
    [*] --> BOOT: main.py starts

    BOOT: BOOT\nsetup_logging()\n_load_config()

    BOOT --> WATCHDOG_INIT: MasterWatchdog.boot()
    WATCHDOG_INIT: WATCHDOG INIT\n_init_workers()\n_init_roles()\nDB init\nProcessReaper init

    WATCHDOG_INIT --> PHI3_START: Phi3Manager.start_all()
    PHI3_START: PHI3 START\nSpawn phi3-orchestrator\nLoad DoC from DB

    PHI3_START --> ORCH_INIT: MasterOrchestrator init
    ORCH_INIT: ORCHESTRATOR INIT\nLoad chat_history.json\nLoad chat_sessions.json\nLoad _doc_context

    ORCH_INIT --> DASH_START: DashboardServer.start()
    DASH_START: DASHBOARD START\nRegister routes\nStart broadcast loop\nListen :8420

    DASH_START --> RUNNING: System Running

    state RUNNING {
        [*] --> IDLE
        IDLE --> PROCESSING: user message / task
        PROCESSING --> CHECKPOINTING: after each TDD step
        CHECKPOINTING --> PROCESSING: checkpoint saved
        PROCESSING --> IDLE: task complete
        PROCESSING --> CRASHED: unhandled exception
    }

    RUNNING --> MONITORING: every 30s
    MONITORING: WATCHDOG MONITORING\nHealth checks\nHeartbeat validation\nStuck task detection

    MONITORING --> RESPAWN: worker dead
    RESPAWN --> RUNNING: worker restarted

    MONITORING --> RUNNING: all healthy

    RUNNING --> CRASHED: process dies
    CRASHED: CRASHED\nWatchdogStatePersistence\nrecovers from:\nwatchdog_state.json\nlast checkpoint

    CRASHED --> RECOVER: restart
    RECOVER --> ORCH_INIT: resume from checkpoint

    RUNNING --> SHUTDOWN: graceful stop
    SHUTDOWN: SHUTDOWN\nDrain write queue\nKill process groups\nClose DB connections
    SHUTDOWN --> [*]
```

---

## 10. Complete Module Dependency Graph

```mermaid
graph TD
    MAIN["main.py"]

    subgraph ORCH_MOD["orchestration/"]
        MW["master_watchdog.py"]
        MO["master_orchestrator.py"]
        RR2["role_router.py"]
        DB2["database.py"]
        PM["phi3_manager.py"]
        PR2["process_reaper.py"]
        WS2["watchdog_state.py"]
        TDD2["tdd_pipeline.py"]
        CM2["context_manager.py"]
        CG2["contract_generator.py"]
        CV2["contract_validator.py"]
        OP2["output_parser.py"]
        RE2["rules_engine.py"]
        GM2["git_manager.py"]
        DT2["dac_tagger.py"]
        LL2["learning_log.py"]
        SA2["static_analysis.py"]
        CICD2["cicd_generator.py"]
    end

    subgraph WORKERS_MOD["workers/"]
        WA2["adapters.py\nWorkerAdapter\nCLIWorkerAdapter\nOllamaWorkerAdapter"]
        FIG["figma_mcp.py"]
    end

    subgraph DASH_MOD["dashboard/"]
        DS2["dashboard_server.py"]
    end

    subgraph PROTO_MOD["protocols/"]
        WEB["web.md"]
        MOB["mobile.md"]
        IOT["iot.md"]
        PLM["plm.md"]
        BP["blueprint_protocol.md"]
    end

    MAIN --> MW
    MAIN --> PM
    MAIN --> MO
    MAIN --> DS2

    MW --> DB2
    MW --> WA2
    MW --> RR2
    MW --> PR2
    MW --> WS2

    MO --> DB2
    MO --> RR2
    MO --> TDD2
    MO --> CM2
    MO --> CG2
    MO --> CV2
    MO --> OP2
    MO --> RE2
    MO --> GM2
    MO --> DT2
    MO --> LL2
    MO --> CICD2

    TDD2 --> DT2
    TDD2 --> LL2
    TDD2 --> SA2
    TDD2 --> OP2
    TDD2 --> GM2

    PM --> WA2
    RR2 --> WA2
    DS2 --> DB2
    DS2 --> RR2
    CM2 --> WEB
    CM2 --> MOB
    CM2 --> IOT
    CM2 --> PLM
    CM2 --> BP
    CG2 --> WA2
    CV2 --> WA2
```
