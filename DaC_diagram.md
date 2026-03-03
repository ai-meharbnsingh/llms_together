# Autonomous Factory — Diagram as Code (DaC)

## Reconstruction Guide

This document + 3 companion files = 100% software reconstruction:

| File | Contains | Purpose |
|------|----------|---------|
| **DaC_diagram.md** (this file) | Architecture, flows, schema, module graph, file map | WHAT the system is |
| **DaC_protocols.md** | Project-type protocols as decision trees | HOW projects are governed |
| **DaC_behavior.md** | Prompt templates, rules, tag mappings, output schemas | HOW components behave |
| **factory_config.json** | Worker configs, role assignments, thresholds, settings | TUNABLE parameters |

---

## 0. File Map — Where Everything Lives

```mermaid
graph TD
    subgraph ROOT["autonomous_factory/"]
        MAIN_F["main.py\n─────────────\nEntry point\nBoot sequence"]
        DAC_F["DaC_diagram.md\n─────────────\nThis file — architecture"]
        DAC_P["DaC_protocols.md\n─────────────\nProtocol decision trees"]
        DAC_B["DaC_behavior.md\n─────────────\nPrompt catalog + rules"]

        subgraph ORCH_DIR["orchestration/ (20 modules)"]
            F_MW["master_watchdog.py — PID 1, sole DB writer"]
            F_MO["master_orchestrator.py — COO, chat routing, execution"]
            F_DB["database.py — 20 tables, ReadOnlyDB/WatchdogDB"]
            F_TDD["tdd_pipeline.py — 13-step + 5-step fast-track"]
            F_CM["context_manager.py — prompt assembly"]
            F_CG["contract_generator.py — api/db/types contracts"]
            F_CV["contract_validator.py — alignment validation"]
            F_OP["output_parser.py — JSON extraction + file writes"]
            F_RE["rules_engine.py — R001-R009 enforcement"]
            F_GM["git_manager.py — branching + atomic commits"]
            F_WM["workspace_manager.py — worktree isolation"]
            F_DT["dac_tagger.py — 6 tag types + training export"]
            F_LL["learning_log.py — cross-project learning"]
            F_SA["static_analysis.py — flake8/bandit/pip-audit"]
            F_CI["cicd_generator.py — GitHub Actions + Docker"]
            F_OB["orchestrator_brain.py — LLM reasoning"]
            F_PM["phi3_manager.py — DoC scribes + summarization"]
            F_PR["process_reaper.py — ghost prevention"]
            F_WS["watchdog_state.py — crash recovery"]
            F_RR["role_router.py — 12 roles + hot-swap"]
        end

        subgraph WORK_DIR["workers/"]
            F_WA["adapters.py — CLI + Ollama adapters"]
            F_FIG["figma_mcp.py — design token client"]
        end

        subgraph DASH_DIR["dashboard/"]
            F_DS["dashboard_server.py — 37 REST + WebSocket"]
        end

        subgraph PROTO_DIR["protocols/ (6 files)"]
            F_WEB["web.md — web project rules"]
            F_MOB["mobile.md — mobile project rules"]
            F_IOT["iot.md — IoT project rules"]
            F_PLM["plm.md — PLM project rules"]
            F_BP["blueprint_protocol.md — blueprint generation"]
            F_BA["blueprint_audit.md — audit scoring"]
        end

        subgraph CFG_DIR["config/"]
            F_CFG["factory_config.json — all settings"]
        end

        subgraph RULES_DIR["rules/"]
            F_PR_RULES["project_rules.json — per-project overrides"]
        end

        subgraph TEST_DIR["tests/ (26 test files)"]
            F_TESTS["426 unit/integration tests\n5 Playwright E2E tests"]
        end
    end
```

---

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

    subgraph DB_LAYER["Database Layer (SQLite WAL — 20 Tables)"]
        WDB["WatchdogDB\n─────────────\nWrite-Capable\nSingle Instance\nOwned by Watchdog\nSCHEMA_VERSION = 3"]
        RDB["ReadOnlyDB\n─────────────\nDistributed Copies\nOrchestrator · Dashboard\nWorkers"]
        BUS["Message Bus\n─────────────\nasyncio.Queue\nWriteResultBus\nBatched every 5s"]
    end

    subgraph ORCH_LAYER["Orchestration Layer"]
        ORCH["MasterOrchestrator\n─────────────\nCOO — In-Memory\nChat Routing (4 modes)\nProject Execution\nSession Management\nWarm/Cold Memory\nDiscussion Panel"]
        PHI3["Phi3Manager\n─────────────\nDoC Scribes\nphi3-orchestrator\nRolling Context\nSummarization\nCold Storage Flush"]
        RR["RoleRouter\n─────────────\nRole ↔ Worker Map\nHot-Swap\nFallback Support\nLocal Test Mode Toggle"]
    end

    subgraph EXECUTION["Autonomous Execution Engine"]
        TDD["TDDPipeline\n─────────────\n13-Step Full Pipeline\nAC→RED→GREEN→BC→BF\n→SEA→DS→OA→VB→GIT\n→CL→CCP→AD\n─────────────\n5-Step Fast-Track\nAC→GREEN→OA→GIT→AD\n(CSS/UI/config only)"]
        CM["ContextManager\n─────────────\nProtocol Injection\nContract Loading\nProject Context\nTask Context"]
        CG["ContractGenerator\n─────────────\napi_contract.json\ndb_schema.sql\ntypes.json"]
        CV["ContractValidator\n─────────────\nCompleteness Check\nAlignment Validation\nContract Locking"]
        OP["OutputParser\n─────────────\nJSON Extraction\nFile Writer\nDecision Router"]
        RE["RulesEngine\n─────────────\nDefault Rules (R001-R009)\nProject Rules\nPrompt Injection\nViolation Detection"]
        GM["GitManager\n─────────────\nmain/develop/phase/{N}\nAtomic Commits\nPR Creation\nConflict Detection"]
        WM["WorkspaceManager\n─────────────\nGit Worktree Isolation\nDomain Routing\nPer-Domain Locks\nSync/Merge/Commit\n.autonomy/ worktrees"]
    end

    subgraph INTELLIGENCE["Intelligence Layer"]
        DT["DaCTagger\n─────────────\nTRAP/SER/DOM\nHRO/HAL/ENV\nAuto-Tagging\nTraining Export"]
        LL["LearningLog\n─────────────\nCross-Project Learning\nDe-duplication\nOccurrence Counting\nPrompt Injection"]
        SA["StaticAnalyzer\n─────────────\nOWASP Scanning\nCode Analysis\nSecurity Findings"]
        CICD["CICDGenerator\n─────────────\nCI/CD Pipeline\nGitHub Actions\nAutomated Builds"]
        BRAIN["OrchestratorBrain\n─────────────\nLLM Reasoning Layer\nGate Rejection Analysis\nDeadlock Resolution\nWorker Routing\nEscalation Composition\nResolution Interpretation"]
    end

    subgraph WORKERS["Worker Layer"]
        CLI_W["CLIWorkerAdapter\n─────────────\nSubprocess Spawn\nPipe I/O\nPGID Kill Groups\nCLAUDECODE Strip"]
        OLLAMA_W["OllamaWorkerAdapter\n─────────────\nHTTP POST\nlocalhost:11434\nStream Response\naiohttp Session"]
        FIGMA["FigmaMCPClient\n─────────────\nDesign Token Fetch\nLayout/Component Specs\nGraceful Fallback\nlocalhost:3845"]
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
        DS["DashboardServer\n─────────────\naiohttp Web Server\n37 REST Endpoints\nWebSocket Hub\nBroadcast Loop (2s)\nSession Management API\nChat Search/Archive API"]
        UI["Web UI\n─────────────\nhttp://127.0.0.1:8420\nChat Tabs + Sessions\nRole Management\nProject Controls\nLive Status\nDiscussion Panel"]
    end

    subgraph CONFIG["Configuration & Protocols"]
        CFG["factory_config.json\n─────────────\nWorker Configs\nRole Assignments\nWatchdog Settings\nDashboard Settings\nCost Controls\nLocal Test Mode\nWorkspace Settings"]
        PROTO["protocols/\n─────────────\nweb.md\nmobile.md\niot.md\nplm.md\nblueprint_protocol.md\nblueprint_audit.md"]
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
    ORCH --> WM
    WM -->|"worktree ops"| GM
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
    ORCH -.->|"design tokens"| FIGMA

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
    ORCH -->|"reasoning via"| BRAIN
    BRAIN -->|"routes via"| RR
    BRAIN -->|"reads"| RDB
    BRAIN -->|"logs to"| LL
```

---

## 2. TDD Pipeline (13-Step Wave + 5-Step Fast-Track)

```mermaid
stateDiagram-v2
    [*] --> CLASSIFY: Task assigned

    CLASSIFY: CLASSIFY TASK\nFast-track patterns:\ncss/style/color/font/theme\nicon/logo/config/env/readme\ncomment/rename/asset/typo

    state classify_check <<choice>>
    CLASSIFY --> classify_check

    classify_check --> AC_FULL: Full pipeline
    classify_check --> AC_FAST: Fast-track\n(CSS/UI/config)

    state FULL_PIPELINE {
        AC_FULL: Step 1 — AC\nAcceptance Criteria\n(AC_Team)
        RED: Step 2 — RED\nWrite Failing Tests\n(TDE_RED)
        GREEN_F: Step 3 — GREEN\nMinimal Implementation\n(TDE_GREEN)
        BC: Step 4 — BC\nBug Capture Scan\n(Bug_Capture)
        BF: Step 5 — BF\nBug Fix\n(Bug_Fix)
        SEA: Step 6 — SEA\nSilent Error Analysis\n(Silent_Error)
        DS: Step 7 — DS\nOWASP Security Scan\n(StaticAnalyzer)
        OA_F: Step 8 — OA\nOutput Alignment\n(Output_Align)
        VB: Step 9 — VB\nVersion Bump
        GIT_F: Step 10 — GIT\nAtomic Commit\n(GitManager)
        CL: Step 11 — CL\nCleanup Artifacts
        CCP: Step 12 — CCP\nCheckpoint State
        AD_F: Step 13 — AD\nDashboard Update

        AC_FULL --> RED
        RED --> GREEN_F
        GREEN_F --> BC
        BC --> BF: bugs found
        BC --> SEA: no bugs
        BF --> SEA
        SEA --> DS
        DS --> OA_F
        OA_F --> VB
        VB --> GIT_F
        GIT_F --> CL
        CL --> CCP
        CCP --> AD_F

        BC --> ESCALATE: critical issue
        BF --> ESCALATE: unfixable
        SEA --> ESCALATE: severe drift
        DS --> ESCALATE: security violation
        ESCALATE: ESCALATION\n(Human Review)
    }

    state FAST_TRACK {
        AC_FAST: Step 1 — AC\nAcceptance Criteria
        GREEN_FT: Step 2 — GREEN\nMinimal Implementation
        OA_FT: Step 3 — OA\nOutput Alignment
        GIT_FT: Step 4 — GIT\nAtomic Commit
        AD_FT: Step 5 — AD\nDashboard Update

        AC_FAST --> GREEN_FT
        GREEN_FT --> OA_FT
        OA_FT --> GIT_FT
        GIT_FT --> AD_FT
    }

    AD_F --> [*]: Wave complete
    AD_FT --> [*]: Wave complete (fast)
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
    participant P3 as Phi3 Scribe
    participant DB as Database

    U->>DS: WebSocket action=chat_message
    DS->>MO: handle_message(user_msg, session_id)
    MO->>MO: _build_conversation_context()
    note over MO: DoC prefix if < 5 msgs (1/4 budget)<br/>Recent history (3/4 budget)<br/>Skip doc_recovery entries
    MO->>DB: _append_history("user", msg, session_id)
    MO->>MO: _route_intent(msg) → detect mode

    alt orchestrator mode
        MO->>RR: get_worker("tdd_analysis")
    else direct mode
        MO->>RR: get_worker(worker_name)
    else project mode
        MO->>MO: prepend project context
        MO->>RR: get_worker(worker_name)
    else discussion mode
        loop each participant (cancellable)
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
    note over MO: If > 200 msgs → flush overflow<br/>to chat_archive (cold storage)
    MO->>P3: queue_summary(persist_full=True)
    note over P3: Async: summarize → chat_summaries<br/>Update DoC → context_summaries
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
    LOCK --> WT_SETUP[WorkspaceManager\nsetup_worktrees\n.autonomy/{domain}]
    WT_SETUP --> BUILD

    BUILD --> TASK_LOOP[FOR EACH task in phase]
    TASK_LOOP --> WT_ROUTE[WorkspaceManager\nresolve_worktree\nmodule prefix match]
    WT_ROUTE --> WT_SYNC[sync_from_develop\nmerge develop into worktree]
    WT_SYNC --> SINGLE[_execute_single_task\nin worktree path]

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

    GATE_RESULT -->|No| BRAIN_ANALYZE[OrchestratorBrain\nanalyze_rejection]
    BRAIN_ANALYZE -->|targeted_retry| RETRY_GUIDED[Retry with\nBrain Guidance]
    BRAIN_ANALYZE -->|switch_worker| SWITCH_W[Brain suggests\nalternate worker]
    BRAIN_ANALYZE -->|escalate_to_human| COMPOSE_ESC[brain.compose_escalation\nsummary + root_cause + 3 options]
    RETRY_GUIDED --> QG
    SWITCH_W --> QG
    COMPOSE_ESC --> ESCALATE_TASK[Escalation\nstatus=pending\nWAIT for human]
    ESCALATE_TASK --> BRAIN_INTERP[brain.interpret_resolution\naction + learning + prompt_modifier]
    BRAIN_INTERP -->|retry_task| TASK_LOOP
    BRAIN_INTERP -->|skip_task| NEXT_TASK
    BRAIN_INTERP -->|log_learning| LEARN_LOG[LearningLog\ninjected into future tasks]
    GATE_RESULT -->|Yes| WT_COMMIT[WorkspaceManager\ncommit_in_worktree\ndomain branch]
    WT_COMMIT --> WT_MERGE[merge_to_develop\nworktree → develop\npreserve phase branch]
    WT_MERGE --> NEXT_TASK{More tasks?}
    NEXT_TASK -->|Yes| TASK_LOOP
    NEXT_TASK -->|No| WT_CLEAN[WorkspaceManager\ncleanup_worktrees\nremove .autonomy/]
    WT_CLEAN --> E2E[Run E2E Tests\nPlaywright Full Walkthrough]

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
        R12["orchestrator_reasoning"]
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
    R12 -->|primary| DS
    R12 -->|fallback| QW
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

    subgraph BRAIN_LOOP["OrchestratorBrain Feedback"]
        ESC_RES["Human Resolves\nEscalation"]
        BRAIN_INT["brain.interpret_resolution()\naction + learning + prompt_modifier"]
    end

    ESC_RES --> BRAIN_INT
    BRAIN_INT -->|"log_fix()"| LEARN
    BRAIN_INT -->|"prompt_modifier"| CM_INJ
```

---

## 8. Dashboard WebSocket & REST API

```mermaid
graph TB
    subgraph CLIENT["Browser Client"]
        WS_OUT["Outbound Actions\n─────────────\nchat_message\ndirect_chat\nproject_chat\ndiscussion_chat\nswap_role\nlaunch_project\nselect_project\nnew_session\nswitch_session\nrename_session\nclose_session\nresolve_escalation\ndiscussion_cancel\nchat_stop"]
        WS_IN["Inbound Events\n─────────────\nstatus_update (2s)\nchat_message\nproject_launched\nproject_progress\nproject_completed\nrole_swapped\nsession_changed\ntask_assigned\nescalation_raised\nchat_stopped\nerror"]
    end

    subgraph SERVER["DashboardServer (37 REST Endpoints)"]
        WS_HANDLER["_websocket(req)\nMain WS Handler\nAction Dispatcher"]
        BROADCAST["_broadcast()\nBroadcast Loop\nevery 2 seconds"]
        subgraph REST_API["REST API Groups"]
            R_STATUS["System\n─────────────\nGET /api/status\nGET /api/tasks\nGET /api/activity\nGET /api/escalations\nPOST /api/escalation/{id}/resolve"]
            R_WORKERS["Workers & Roles\n─────────────\nGET /api/roles\nPOST /api/roles/swap\nGET /api/workers/available\nGET /api/workers/status"]
            R_CHAT["Chat\n─────────────\nPOST /api/chat\nPOST /api/chat/direct\nPOST /api/chat/project\nPOST /api/chat/discussion\nPOST /api/chat/stop\nGET /api/chat/history\nGET /api/chat/search\nGET /api/chat/archive\nGET /api/chat/download"]
            R_SESSION["Sessions\n─────────────\nGET /api/chat/sessions\nPOST /api/chat/sessions/new\nPOST /api/chat/sessions/switch\nPOST /api/chat/sessions/rename\nPOST /api/chat/sessions/close"]
            R_PROJECT["Projects\n─────────────\nGET /api/projects\nPOST /api/projects/select\nPOST /api/projects/create\nPOST /api/projects/launch\nGET /api/projects/{id}/progress\nGET /api/projects/{id}/blueprint\nPOST /api/projects/{id}/approve-blueprint\nPOST /api/projects/{id}/approve-uat\nDELETE /api/projects/{id}"]
            R_CONFIG["Config & Training\n─────────────\nGET /api/config/mode\nPOST /api/config/mode\nPOST /api/training-data/{id}/validate"]
        end
    end

    WS_OUT -->|"WebSocket"| WS_HANDLER
    WS_HANDLER --> BROADCAST
    BROADCAST -->|"WebSocket"| WS_IN
    R_STATUS & R_WORKERS & R_CHAT & R_SESSION & R_PROJECT & R_CONFIG -->|"HTTP"| CLIENT
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
        OB2["orchestrator_brain.py"]
        WM2["workspace_manager.py"]
    end

    subgraph WORKERS_MOD["workers/"]
        WA2["adapters.py\nWorkerAdapter\nCLIWorkerAdapter\nOllamaWorkerAdapter"]
        FIG2["figma_mcp.py\nFigmaMCPClient"]
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
        BA["blueprint_audit.md"]
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
    MO --> OB2
    MO --> WM2
    WM2 --> GM2
    OB2 --> RR2
    OB2 --> DB2
    OB2 --> LL2

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
    CM2 --> BA
    CG2 --> WA2
    CV2 --> WA2
    MO -.-> FIG2
    DS2 --> MO
```

---

## 11. Database Schema (20 Tables — SCHEMA_VERSION = 3)

```mermaid
erDiagram
    projects ||--o{ tasks : "has"
    projects ||--o{ blueprint_revisions : "versioned by"
    projects ||--o{ phase_completions : "tracks"
    projects ||--o{ training_data : "produces"
    projects ||--o{ decision_logs : "logs"
    tasks ||--o{ checkpoints : "snapshots"
    tasks ||--o{ quality_gates : "validated by"
    tasks ||--o{ commits : "tracked by"
    tasks ||--o{ dac_tags : "tagged by"
    dashboard_state ||--o{ context_summaries : "FK instance_name"

    projects {
        text project_id PK
        text name
        text description
        text status
    }
    tasks {
        text task_id PK
        text project_id FK
        int phase
        text module
        text description
        text status
        text dependencies
    }
    checkpoints {
        int checkpoint_id PK
        text task_id FK
        text step_id
        text state_json
    }
    quality_gates {
        int gate_id PK
        text task_id FK
        text gate_type
        text result
        text details
    }
    commits {
        int commit_id PK
        text task_id FK
        text commit_hash
        text branch
    }
    blueprint_revisions {
        int revision_id PK
        text project_id FK
        int version
        text content
    }
    training_data {
        int training_id PK
        text project_id FK
        text tag_type
        text input_text
        text output_text
    }
    phase_completions {
        int completion_id PK
        text project_id FK
        int phase
        text result
    }
    decision_logs {
        int decision_id PK
        text project_id FK
        text decision_type
        text description
    }
    dashboard_state {
        text instance_name PK
        text status
        text current_task_id FK
        real context_usage_percent
    }
    context_summaries {
        int summary_id PK
        text instance_name FK
        text original_chat_ids
        text summary_text
        int token_count
    }
    chat_summaries {
        text chat_id PK
        text session_id
        text instance_name
        text parent_worker
        text user_query
        text llm_response_summary
        text full_llm_response
        text keywords
        text decisions_made
    }
    chat_archive {
        int archive_id PK
        text session_id
        text role
        text content
        text mode
        text worker
        text project_id
        text original_timestamp
    }
    dac_tags {
        int tag_id PK
        text task_id FK
        text tag_type
        text event
        text context
        text status
    }
    learning_log {
        int log_id PK
        text bug_description
        text root_cause
        text prevention_strategy
        text keywords
        int occurrence_count
        int validated
    }
    worker_health {
        int health_id PK
        text worker_name
        text status
    }
    escalations {
        int escalation_id PK
        text type
        text description
        text status
    }
    cost_tracking {
        int cost_id PK
        text task_id
        text project_id
        text worker
        text operation
        int prompt_tokens
        int completion_tokens
        int total_tokens
        real estimated_cost_usd
        int elapsed_ms
    }
```

---

## 12. Warm/Cold Memory & Session System

```mermaid
flowchart TD
    subgraph WARM["Warm Memory (used every response)"]
        HIST["chat_history.json\n─────────────\nMax 200 messages\nPer mode+worker scope\nIn-memory + file-backed"]
        DOC["Document of Context (DoC)\n─────────────\nPhi3-generated summary\nDecisions · Requirements\nState · Key Context\nAction Items · History"]
    end

    subgraph COLD["Cold Storage (searchable on demand)"]
        ARCHIVE["chat_archive\n─────────────\nAll overflow messages\nsession_id · role · content\nmode · worker · project_id\noriginal_timestamp"]
        SUMMARIES["chat_summaries\n─────────────\nPhi3 summaries per chat\nkeywords · decisions\nfull_llm_response\n(when persist_full=True)"]
    end

    subgraph SESSIONS["Session Management"]
        NEW["new_chat_session()\n─────────────\nFlush warm → cold\nCreate UUID session\nClear history"]
        SWITCH["switch_chat_session()\n─────────────\nFlush current → cold\nLoad target from archive\nRestore history"]
        RENAME["rename_chat_session()"]
        CLOSE["close_chat_session()\n─────────────\nArchive + remove"]
    end

    subgraph SEARCH["Search APIs"]
        KW_SEARCH["GET /api/chat/search\n─────────────\nKeyword search in\nchat_summaries\nFilter: worker, limit"]
        ARCH_SEARCH["GET /api/chat/archive\n─────────────\nKeyword search in\nchat_archive\nFilter: worker, mode\nPagination: offset, limit"]
    end

    subgraph CONTEXT_BUILD["Context Builder (_build_conversation_context)"]
        BUDGET["Token Budget Split\n─────────────\n< 5 relevant msgs:\n  DoC = 1/4 budget\n  History = 3/4 budget\n≥ 5 relevant msgs:\n  No DoC prefix\n  History = full budget"]
    end

    %% Overflow path
    HIST -->|"> 200 msgs"| ARCHIVE
    HIST -->|"every chat pair"| SUMMARIES

    %% Recovery path
    DOC -->|"loaded on boot"| CONTEXT_BUILD
    HIST --> CONTEXT_BUILD
    ARCHIVE -->|"on session switch"| HIST

    %% Session lifecycle
    NEW --> ARCHIVE
    SWITCH --> ARCHIVE
    SWITCH --> HIST
    CLOSE --> ARCHIVE

    %% Search
    SUMMARIES --> KW_SEARCH
    ARCHIVE --> ARCH_SEARCH

    %% DoC update
    SUMMARIES -->|"Phi3 _update_doc()"| DOC

    CONTEXT_BUILD -->|"injected into prompt"| WORKER_PROMPT["Worker Prompt"]
```

---

## 13. Local Test Mode (E2E Without Token Burn)

```mermaid
flowchart LR
    subgraph PRODUCTION["Production Mode"]
        P_ROLES["12 Roles\n─────────────\ncode_gen_simple → DeepSeek\ncode_gen_complex → Claude\ntdd_testing → Claude\ntdd_analysis → Kimi\ngatekeeper_review → Gemini\narchitecture_audit → Claude\ntask_planning → Kimi\nblueprint_gen → Claude\nsummarization → Phi3\nfrontend_design → Gemini\nproject_class → Phi3\norchestrator_reason → DeepSeek"]
    end

    subgraph LOCAL["Local Test Mode"]
        L_ROLES["All Roles → Local\n─────────────\nEvery role remapped to\nDeepSeek or Qwen\n─────────────\nNo CLI subprocess spawns\nNo Claude/Kimi/Gemini tokens\nFast E2E test execution"]
    end

    TOGGLE["POST /api/config/mode\n─────────────\nRoleRouter.set_local_mode()\nSaves production config\nApplies local overrides"]

    P_ROLES -->|"enable"| TOGGLE
    TOGGLE -->|"local_test=true"| L_ROLES
    L_ROLES -->|"disable"| TOGGLE
    TOGGLE -->|"local_test=false"| P_ROLES
```
