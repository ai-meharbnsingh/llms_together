# Autonomous Factory — Behavior Diagrams as Code

> Companion to `DaC_diagram.md` and `DaC_protocols.md`. Contains every internal behavior:
> prompt templates, enforcement rules, DaC tag mappings, output schemas, quality gate logic,
> and orchestrator brain decision trees. An AI can reconstruct exact component behavior from this file.

---

## 1. DaC Rules Engine (R001–R009 + Type-Specific)

### 1.1 Core Rules (All Projects)

```mermaid
flowchart TD
    subgraph CORE_RULES["9 Core Rules — ALL Project Types"]
        R001["R001 — API (automated)\n─────────────\nNo worker may modify\napi_contract.json directly\nRequest changes via Orchestrator\nViolation: SER"]
        R002["R002 — GIT (automated)\n─────────────\nAll commits must reference\ntask_id in message\nViolation: TRAP"]
        R003["R003 — DEPENDENCY (kimi)\n─────────────\nIf dependency unclear\ndo NOT assume\nEscalate to Kimi\nViolation: TRAP"]
        R004["R004 — CONTRACT (validator+kimi)\n─────────────\nAll API responses must\nmatch types.json schemas\nViolation: SER"]
        R005["R005 — OUTPUT (automated)\n─────────────\nAll code output must be\nstructured JSON:\n{files[], decisions[], notes[]}\nViolation: TRAP"]
        R006["R006 — DECISION (kimi)\n─────────────\nMinor: log to decision_logs\nMajor (schema/API/security):\nescalate to human\nViolation: DOM"]
        R007["R007 — SECURITY (kimi)\n─────────────\nNo hardcoded secrets\nOWASP Top 10 compliance\nViolation: SER"]
        R008["R008 — SYNC (automated)\n─────────────\nGit pull latest before\nstarting any task\nViolation: ENV"]
        R009["R009 — ACCESS (automated)\n─────────────\nWorkers have full filesystem\naccess to project folder\nNo human permission for ops\nViolation: ENV"]
    end
```

### 1.2 IoT-Specific Rules

```mermaid
flowchart TD
    subgraph IOT_RULES["IoT Rules (R100–R102)"]
        R100["R100 — SAFETY (automated)\n─────────────\nDefault Simulation tier\nLive mode requires per-task\nhuman confirmation\nViolation: SER"]
        R101["R101 — MQTT (kimi)\n─────────────\nTLS mandatory for production\nPer-device auth required\nViolation: SER"]
        R102["R102 — FIRMWARE (kimi)\n─────────────\nAll firmware must include\nwatchdog timer + OTA support\nViolation: DOM"]
    end
```

### 1.3 PLM-Specific Rules

```mermaid
flowchart TD
    subgraph PLM_RULES["PLM Rules (R200–R201)"]
        R200["R200 — PRECISION (kimi)\n─────────────\nDecimal for financial calcs\nNumpy for engineering\nNever raw float for money\nViolation: SER"]
        R201["R201 — BOM (validator+kimi)\n─────────────\nAll BOM entries must have:\npart_number, quantity,\nunit, supplier\nViolation: TRAP"]
    end
```

### 1.4 Mobile-Specific Rules

```mermaid
flowchart TD
    subgraph MOBILE_RULES["Mobile Rules (R300–R301)"]
        R300["R300 — OFFLINE (kimi)\n─────────────\nAll critical features\nmust work offline\nQueue mutations for sync\nViolation: DOM"]
        R301["R301 — SECURITY (kimi)\n─────────────\nUse Keychain/Keystore\nNever AsyncStorage for tokens\nViolation: SER"]
    end
```

### 1.5 Enforcement Classification

```mermaid
flowchart LR
    subgraph ENFORCEMENT["Enforcement Modes"]
        AUTO["automated\n─────────────\nChecked by code\nBlocks immediately\nR001, R002, R005,\nR008, R009, R100"]
        KIMI["kimi\n─────────────\nReviewed by Kimi\nquality gate\nR003, R006, R007,\nR101, R102, R200,\nR300, R301"]
        BOTH["validator+kimi\n─────────────\nAuto-check first\nthen Kimi review\nR004, R201"]
    end

    AUTO --> INSTANT["Instant rejection\non violation"]
    KIMI --> GATE["Caught during\nquality gate"]
    BOTH --> DOUBLE["Double verification"]
```

### 1.6 Automated Rule Checking Logic

```mermaid
flowchart TD
    OUTPUT["Worker Output"] --> CHECK{"check_automated_rules()"}

    CHECK --> R001_C{"R001: Output contains\napi_contract.json\nin files[]?"}
    R001_C -->|"Yes"| V001["VIOLATION: SER\nDirect contract modification"]
    R001_C -->|"No"| R002_C

    R002_C{"R002: Commit message\ncontains task_id?"}
    R002_C -->|"No"| V002["VIOLATION: TRAP\nMissing task reference"]
    R002_C -->|"Yes"| R005_C

    R005_C{"R005: Output is\nvalid JSON with\nfiles[] key?"}
    R005_C -->|"No"| V005["VIOLATION: TRAP\nMalformed output"]
    R005_C -->|"Yes"| PASS["All automated\nrules pass"]

    V001 & V002 & V005 --> TAG["DaC tag created\n+ learning_log entry"]
```

---

## 2. DaC Tag System (EVENT_TAG_MAP)

### 2.1 Tag Types

```mermaid
flowchart TD
    subgraph TAG_TYPES["6 DaC Tag Categories"]
        TRAP["TRAP\n─────────────\nRule violations\nMalformed output\nScope gaps\nMissing tests"]
        SER["SER\n─────────────\nSecurity issues\nContract violations\nMerge conflicts"]
        DOM["DOM\n─────────────\nBug patterns\nLogic errors\nDomain mistakes"]
        HRO["HRO\n─────────────\nHuman intervention\nDouble rejections\nEscalations"]
        HAL["HAL\n─────────────\nSilent errors\nTimeouts\nHallucinations\nConcurrency issues"]
        ENV["ENV\n─────────────\nEnvironment issues\nConfig errors\nDependency problems"]
    end
```

### 2.2 Complete Event → Tag Mapping

```mermaid
flowchart LR
    subgraph EVENTS["Pipeline Events"]
        E1["bug_capture"]
        E2["silent_error"]
        E3["security_scan"]
        E4["contract_violation"]
        E5["merge_conflict"]
        E6["malformed_output"]
        E7["missing_test"]
        E8["gap_detected"]
        E9["double_rejection"]
        E10["human_escalation"]
        E11["task_timeout"]
        E12["worker_crash"]
        E13["hallucination"]
        E14["dependency_unclear"]
        E15["config_error"]
        E16["env_mismatch"]
    end

    E1 -->|"DOM"| DOM_T["DOM"]
    E2 -->|"HAL"| HAL_T["HAL"]
    E3 -->|"SER"| SER_T["SER"]
    E4 -->|"SER"| SER_T
    E5 -->|"SER"| SER_T
    E6 -->|"TRAP"| TRAP_T["TRAP"]
    E7 -->|"TRAP"| TRAP_T
    E8 -->|"TRAP"| TRAP_T
    E9 -->|"HRO"| HRO_T["HRO"]
    E10 -->|"HRO"| HRO_T
    E11 -->|"HAL"| HAL_T
    E12 -->|"HAL"| HAL_T
    E13 -->|"HAL"| HAL_T
    E14 -->|"TRAP"| TRAP_T
    E15 -->|"ENV"| ENV_T["ENV"]
    E16 -->|"ENV"| ENV_T
```

### 2.3 Training Solution Hints (Auto-Generated Per Tag)

```mermaid
flowchart TD
    subgraph SOLUTIONS["Auto-Generated Training Solutions"]
        S_TRAP["TRAP → 'Ensure worker output\nis valid JSON and writes only\nwithin its module scope'"]
        S_SER["SER → 'Resolve security/contract/\nconflict issue before proceeding'"]
        S_DOM["DOM → 'Review domain logic\nand fix the identified bug'"]
        S_HRO["HRO → 'Escalate to human\nreviewer for decision'"]
        S_HAL["HAL → 'Add error handling,\ntimeout guards, and\noutput validation'"]
        S_ENV["ENV → 'Fix environment\nconfiguration or\ndependency mismatch'"]
    end

    subgraph PIPELINE["Tag → Training Data Pipeline"]
        TAG_CREATE["DaC tag created"] --> TRAIN["training_data row\n(validated=False)"]
        TAG_CREATE --> LEARN["learning_log entry\n(occurrence_count=1)"]
        TRAIN --> HUMAN_V["Human validates"]
        HUMAN_V --> INJECT["Injected into\nfuture prompts"]
    end
```

### 2.4 Tag Escalation: Double Rejection → HRO

```mermaid
sequenceDiagram
    participant W as Worker
    participant G as Quality Gate (Kimi)
    participant T as DaCTagger
    participant H as Human Dashboard

    W->>G: Submit code (attempt 1)
    G-->>W: REJECTED (issues found)
    T->>T: tag(event="bug_capture", type=DOM)

    W->>G: Submit code (attempt 2)
    G-->>W: REJECTED again
    T->>T: tag(event="double_rejection", type=HRO)
    T->>H: Escalation raised
    note over H: Human must decide:<br/>retry / switch_worker / skip
```

---

## 3. Output Parser Schema

### 3.1 Expected Worker Output Format

```mermaid
classDiagram
    class WorkerOutput {
        +File[] files
        +Decision[] decisions
        +string[] notes
        +string[] tests_needed
    }
    class File {
        +string path (relative)
        +string content (full file)
        +string action (create|update|delete)
    }
    class Decision {
        +string type (minor|major)
        +string description
    }
    WorkerOutput "1" --> "*" File
    WorkerOutput "1" --> "*" Decision
```

### 3.2 Output Parsing Flow

```mermaid
flowchart TD
    RAW["Raw LLM Response"] --> TRY_JSON{"Direct\nJSON.parse?"}

    TRY_JSON -->|"Success"| VALIDATE
    TRY_JSON -->|"Fail"| TRY_FENCE{"Extract from\nmarkdown fence\n```json...```?"}

    TRY_FENCE -->|"Success"| VALIDATE
    TRY_FENCE -->|"Fail"| TRY_TRIPLE{"Sanitize\ntriple-quotes\n(Python style)?"}

    TRY_TRIPLE -->|"Success"| VALIDATE
    TRY_TRIPLE -->|"Fail"| TRY_BACKTICK{"Sanitize\nJS template\nliterals?"}

    TRY_BACKTICK -->|"Success"| VALIDATE
    TRY_BACKTICK -->|"Fail"| TRY_BRACE{"Brace-counting\nextraction?"}

    TRY_BRACE -->|"Success"| VALIDATE
    TRY_BRACE -->|"Fail"| HAL_TAG["DaC tag: HAL\n(parse failure)"]

    VALIDATE["_validate_structure()"] --> HAS_FILES{"Has files[] key?"}
    HAS_FILES -->|"Yes"| SCOPE_CHECK["Scope enforcement"]
    HAS_FILES -->|"No"| TRAP_TAG["DaC tag: TRAP\n(malformed structure)"]

    SCOPE_CHECK --> ALLOWED{"File within\nmodule scope?"}
    ALLOWED -->|"Yes"| WRITE["Write file to disk"]
    ALLOWED -->|"No"| CROSS{"In CROSS_CUTTING_FILES?"}
    CROSS -->|"Yes"| WRITE
    CROSS -->|"No"| REJECT_FILE["Reject file\nDaC tag: TRAP\n(scope violation)"]
```

### 3.3 Cross-Cutting Files (Any Task May Touch)

```mermaid
flowchart LR
    subgraph CROSS_CUT["CROSS_CUTTING_FILES Set"]
        CC1["requirements.txt"]
        CC2["package.json"]
        CC3["package-lock.json"]
        CC4["pyproject.toml"]
        CC5["setup.py / setup.cfg"]
        CC6["Dockerfile"]
        CC7["docker-compose.yml"]
        CC8[".env.example"]
        CC9["README.md"]
        CC10[".gitignore"]
        CC11["tsconfig.json"]
        CC12["vite.config.ts"]
        CC13["tailwind.config.js"]
    end
```

### 3.4 Scope Enforcement Logic

```mermaid
flowchart TD
    FILE_PATH["file.path from output"] --> GET_PREFIX["_get_allowed_prefix(task_module)"]
    GET_PREFIX --> EXAMPLES["Examples:\n  backend/database.py → backend/\n  backend/routers/ → backend/routers/\n  frontend/ → frontend/"]

    FILE_PATH --> CHECK{"file.path starts\nwith allowed_prefix?"}
    CHECK -->|"Yes"| ALLOW["ALLOW write"]
    CHECK -->|"No"| IS_CROSS{"file.name in\nCROSS_CUTTING_FILES?"}
    IS_CROSS -->|"Yes"| ALLOW
    IS_CROSS -->|"No"| TRAVERSAL{"Contains '..' or\nabsolute path?"}
    TRAVERSAL -->|"Yes"| BLOCK_SEC["BLOCK: Path traversal\nDaC tag: SER"]
    TRAVERSAL -->|"No"| BLOCK_SCOPE["BLOCK: Out of scope\nDaC tag: TRAP"]
```

### 3.5 Decision Routing

```mermaid
flowchart LR
    DECISION["Decision from output"] --> TYPE{"decision.type?"}
    TYPE -->|"minor"| LOG["Log to decision_logs\ntable in DB"]
    TYPE -->|"major"| ESCALATE["Escalate to human\nvia dashboard"]
```

---

## 4. Context Manager — Prompt Assembly

### 4.1 Task Prompt Structure (build_task_prompt)

```mermaid
flowchart TD
    subgraph PROMPT["Worker Task Prompt — 8 Sections"]
        S1["§1 TASK HEADER\n─────────────\ntask_id, module,\nphase, complexity\n+ description"]
        S2["§2 PROTOCOL\n─────────────\nLoaded from protocols/*.md\nFallback: web.md"]
        S3["§3 DaC RULES\n─────────────\nFrom project_rules.json\nFormatted as:\n- [R001] (API): rule text"]
        S3B["§3b KNOWN FAILURE PATTERNS\n─────────────\nHardcoded top recurring bugs:\nTRAP: scope violations\nSER: CORS, JWT, CSRF, secrets\nHRO: async/concurrency\nDOM: FK, nullable, enum\nHAL: localhost, retry, paths"]
        S4["§4 CONTRACTS\n─────────────\napi_contract.json\ndb_schema.sql\ntypes.json\nMarked IMMUTABLE\nDeviation = TRAP rejection"]
        S5A["§5a PHASE FILES\n─────────────\nFiles from earlier tasks\nin same phase (max 30KB)\nPrevents duplicate definitions"]
        S5B["§5b RELEVANT FILES\n─────────────\nExisting code files\n(max 10 files, 50KB each)"]
        S6["§6 PAST LEARNINGS\n─────────────\nFrom learning_log\nFiltered: occurrence ≥ 2\nOR validated=True\nWithin last 90 days"]
        S7["§7 OUTPUT FORMAT\n─────────────\nRequired JSON structure:\n{files[], decisions[],\nnotes[], tests_needed[]}"]
        S8["§8 FILE SCOPE\n─────────────\nAllowed directory prefix\nCross-cutting exceptions\nViolation = TRAP"]
    end

    S1 --> S2 --> S3 --> S3B --> S4 --> S5A --> S5B --> S6 --> S7 --> S8
```

### 4.2 Protocol Loading Fallback Chain

```mermaid
flowchart TD
    REQUEST["load_protocol(project_type)"] --> C1{"project_dir/protocols/\n{type}.md exists?"}
    C1 -->|"Yes"| LOAD1["Load project-local"]
    C1 -->|"No"| C2{"factory_dir/protocols/\n{type}.md exists?"}
    C2 -->|"Yes"| LOAD2["Load built-in"]
    C2 -->|"No"| C3{"project_dir/protocols/\nweb.md exists?"}
    C3 -->|"Yes"| LOAD3["Load web.md fallback"]
    C3 -->|"No"| C4{"factory_dir/protocols/\nweb.md exists?"}
    C4 -->|"Yes"| LOAD4["Load web.md fallback"]
    C4 -->|"No"| EMPTY["Return empty string"]
```

### 4.3 Learning Log Quality Filter

```mermaid
flowchart TD
    ENTRY["learning_log entry"] --> OCC{"occurrence_count ≥ 2\nOR validated = True?"}
    OCC -->|"No"| SKIP["Skip (not injected)"]
    OCC -->|"Yes"| AGE{"created_at within\nlast 90 days?"}
    AGE -->|"No"| SKIP
    AGE -->|"Yes"| INJECT["Inject into prompt\nas 'Past Learnings'"]
```

### 4.4 Contract Enforcement Block (Exact Text)

The contracts section injected into every worker prompt includes these exact enforcement rules:

```mermaid
flowchart TD
    subgraph MUST["YOU MUST"]
        M1["Implement EXACTLY the endpoints\nlisted in api_contract.json"]
        M2["Use EXACTLY the table names,\ncolumn names, types from db_schema.sql"]
        M3["Use EXACTLY the TypeScript types\nfrom types.json"]
        M4["Match function signatures,\nreturn types, error codes precisely"]
    end

    subgraph MUST_NOT["YOU MUST NOT"]
        N1["Invent new endpoints, tables,\ncolumns, or types not in contracts"]
        N2["Rename or restructure anything\ndefined in the contracts"]
        N3["Add extra fields to API responses\nthat break the contract schema"]
        N4["Use a different auth mechanism\nthan specified"]
    end
```

---

## 5. Quality Gate Prompt (build_gate_prompt)

### 5.1 Gate Prompt Structure

```mermaid
flowchart TD
    subgraph GATE_PROMPT["Kimi Quality Gate Prompt"]
        G1["§1 TASK\n─────────────\ntask_id + description"]
        G2["§2 CODE OUTPUT\n─────────────\nAll files with content\n(produced by worker)"]
        G3["§3 CONTRACTS\n─────────────\napi_contract.json\ndb_schema.sql\ntypes.json"]
        G4["§4 AUTO-VALIDATOR REPORT\n─────────────\nJSON from contract_validator\n(completeness + alignment)"]
        G5["§5 DECISIONS MADE\n─────────────\nminor/major decisions\nfrom worker output"]
        G6["§6 REVIEW INSTRUCTIONS\n─────────────\n5 cross-reference checks\n+ verdict format"]
    end

    G1 --> G2 --> G3 --> G4 --> G5 --> G6
```

### 5.2 Cross-Reference Checks (Mandatory Before Verdict)

```mermaid
flowchart TD
    subgraph XREF["5 Cross-Reference Checks"]
        X1["1. Python imports\n─────────────\nEvery from X import Y\nmust be stdlib, third-party,\nOR in Code Output files\nMissing local module → REJECT"]
        X2["2. TypeScript imports\n─────────────\nEvery import { Y } from './X'\nmust resolve to file in\nCode Output\nMissing file → REJECT"]
        X3["3. Function/class calls\n─────────────\nEvery called function/class\nmust be defined, imported,\nor built-in\nUndefined references → REJECT"]
        X4["4. Type usage\n─────────────\nTypes must match types.json\nMismatches → list as issues"]
        X5["5. Cross-file consistency\n─────────────\nIf file A imports from B\n(both in Code Output)\nexported names must match\nMismatch → REJECT"]
    end
```

### 5.3 Gate Verdict Schema

```mermaid
classDiagram
    class GateVerdict {
        +string verdict ("APPROVED"|"REJECTED")
        +float confidence (0.0-1.0)
        +string[] issues
        +string[] dac_tags (per issue)
        +string[] suggestions
    }
    note for GateVerdict "APPROVE only if:\n- confidence > 0.9\n- no critical issues\n- all 5 cross-ref checks pass"
```

---

## 6. Orchestrator Brain Decision Trees

### 6.1 Brain Architecture

```mermaid
flowchart TD
    subgraph BRAIN["OrchestratorBrain"]
        THINK["_think(prompt, system)\n─────────────\nPrimary: DeepSeek\nFallback: Qwen\nReturns: parsed JSON dict\nor None on failure"]
        PARSE["_parse_json(text)\n─────────────\n1. Direct JSON.parse\n2. Markdown fence extraction\n3. Brace-counting"]
    end

    THINK --> PARSE

    subgraph METHODS["5 Decision Methods"]
        M1["analyze_rejection()"]
        M2["compose_escalation()"]
        M3["resolve_deadlock()"]
        M4["suggest_worker()"]
        M5["interpret_resolution()"]
    end

    THINK --> M1 & M2 & M3 & M4 & M5

    subgraph FALLBACKS["Every Method Has\nDeterministic Fallback"]
        F_NOTE["If LLM call fails →\nheuristic logic runs\nBrain NEVER blocks pipeline"]
    end

    M1 & M2 & M3 & M4 & M5 --> FALLBACKS
```

### 6.2 analyze_rejection() — Gate Rejection Analysis

```mermaid
flowchart TD
    REJECTION["Quality gate\nrejected task"] --> BRAIN_CALL["Brain analyzes:\n- task description\n- module\n- rejection count\n- gate issues\n- past attempts"]

    BRAIN_CALL --> STRATEGY{"strategy?"}

    STRATEGY -->|"targeted_retry"| RETRY["Prepend retry_guidance\nto worker prompt\nand re-execute same worker"]
    STRATEGY -->|"switch_worker"| SWITCH["Change to different\nworker and retry"]
    STRATEGY -->|"escalate_to_human"| ESCALATE["Show human_summary\non dashboard"]

    subgraph FALLBACK_LOGIC["Deterministic Fallback"]
        FB_CHECK{"rejection_count ≥ 2?"}
        FB_CHECK -->|"Yes"| FB_ESC["escalate_to_human"]
        FB_CHECK -->|"No"| FB_RETRY["targeted_retry\nwith issue list"]
    end

    BRAIN_CALL -.->|"LLM fails"| FALLBACK_LOGIC
```

**Prompt template:**
```
System: You are the reasoning core of an autonomous software factory.
        Analyze gate rejections and recommend the best recovery strategy.
        Always respond with valid JSON.

User:   You are the Orchestrator Brain analyzing a quality-gate rejection.
        TASK: {description}
        MODULE: {module}
        REJECTION #{count}
        ISSUES: {json issues}
        {past attempt history}

        Return JSON: {diagnosis, strategy, retry_guidance, human_summary}
```

**Response schema:**
```mermaid
classDiagram
    class RejectionAnalysis {
        +string diagnosis
        +string strategy ("targeted_retry"|"switch_worker"|"escalate_to_human")
        +string retry_guidance
        +string human_summary
    }
```

### 6.3 compose_escalation() — Rich Escalation

```mermaid
flowchart TD
    REPEATED["Repeated task\nfailures"] --> COMPOSE["Brain composes:\n- all gate issues\n- DaC tags\n- task context"]

    COMPOSE --> OUTPUT["Escalation output:\nsummary + root_cause\n+ 3 options"]

    subgraph OPTIONS["3 Resolution Options"]
        O1["retry_with_different_worker\n'Try deepseek instead of qwen'"]
        O2["simplify_task\n'Break into smaller subtasks'"]
        O3["skip\n'Mark as skipped, move on'"]
    end

    OUTPUT --> OPTIONS
```

**Prompt template:**
```
System: Compose clear, actionable escalations for human operators.

User:   TASK: {description}
        MODULE: {module}
        ALL GATE ISSUES: {json issues}
        DaC TAGS: {json tags}

        Return JSON: {summary, root_cause, options[{label, action, detail}]}
```

### 6.4 resolve_deadlock() — Dependency Deadlock

```mermaid
flowchart TD
    DEADLOCK["All pending tasks\nhave unsatisfied deps"] --> ANALYZE["Brain analyzes\ndependency graph"]

    ANALYZE --> PICK["Picks best task\nto break deadlock"]

    subgraph FALLBACK_DL["Deterministic Fallback"]
        FB_DL["Pick task with\nfewest unsatisfied\ndependencies"]
    end

    ANALYZE -.->|"LLM fails"| FALLBACK_DL
```

**Response schema:**
```mermaid
classDiagram
    class DeadlockResolution {
        +string analysis
        +string resolution ("run_task"|"reorder"|"escalate")
        +string task_to_run
        +string reason
    }
```

### 6.5 suggest_worker() — Smart Worker Routing

```mermaid
flowchart TD
    RETRY["Need to retry\nwith different worker"] --> BRAIN_SW["Brain considers:\n- task complexity\n- previous worker\n- failure history"]

    BRAIN_SW --> PICK_W{"Recommended worker?"}
    PICK_W --> DS["deepseek (16B)\n─────────────\nStrong at complex code"]
    PICK_W --> QW["qwen (7B)\n─────────────\nFast, good at simple code"]

    subgraph FALLBACK_SW["Deterministic Fallback"]
        FB_SW["Swap to other worker:\nif qwen failed → deepseek\nif deepseek failed → qwen"]
    end

    BRAIN_SW -.->|"LLM fails"| FALLBACK_SW
```

### 6.6 interpret_resolution() — Human Decision Translation

```mermaid
flowchart TD
    HUMAN_DEC["Human resolves\nescalation"] --> INTERPRET["Brain interprets\nhuman decision text"]

    INTERPRET --> ACTION{"action?"}
    ACTION -->|"retry_task"| A_RETRY["Re-execute task"]
    ACTION -->|"skip_task"| A_SKIP["Skip and continue"]
    ACTION -->|"modify_prompt"| A_MOD["Prepend prompt_modifier\nto future similar tasks"]
    ACTION -->|"log_learning"| A_LOG["Record learning\nin learning_log"]

    subgraph FALLBACK_IR["Deterministic Fallback"]
        FB_IR["Keyword match:\n'retry/redo/try again' → retry_task\n'skip/ignore/move on' → skip_task\nelse → log_learning"]
    end

    INTERPRET -.->|"LLM fails"| FALLBACK_IR
```

**Response schema:**
```mermaid
classDiagram
    class ResolutionAction {
        +string action ("retry_task"|"skip_task"|"modify_prompt"|"log_learning")
        +string learning
        +string prompt_modifier
    }
```

---

## 7. TDD Pipeline Step Prompts

### 7.1 Testing Ground Truth Rule (Injected Into All Code Steps)

```mermaid
flowchart TD
    subgraph GROUND_TRUTH["TESTING_GROUND_TRUTH_RULE — Injected Into Every Worker"]
        GT1["If tests fail →\nfix SOURCE CODE"]
        GT2["NEVER modify or\nweaken tests"]
        GT3["No assumptions\nNo temporary fixes\nNo test modifications"]
        GT4["No skipping tests\nNo xfail marking\nNo loosening assertions"]
        GT5["Tests define the contract\nImplementation must\nsatisfy the contract"]
    end
```

### 7.2 TDD Step → Worker + DaC Tag Routing

```mermaid
flowchart TD
    subgraph STEP_ROUTING["Step → Worker → Tag Mapping"]
        AC_S["AC (Acceptance Criteria)\n→ Claude (tdd_testing)"]
        RED_S["RED (Write Tests)\n→ Claude (tdd_testing)\n→ pytest must FAIL"]
        GREEN_S["GREEN (Implementation)\n→ Claude (tdd_testing)\n→ pytest must PASS"]
        BC_S["BC (Bug Capture)\n→ flake8 + LLM\n→ Tag: DOM if bugs found"]
        BF_S["BF (Bug Fix)\n→ Claude or DeepSeek\n→ Re-run pytest"]
        SEA_S["SEA (Silent Error)\n→ bandit subset + LLM\n→ Tag: HAL if concurrency issues"]
        DS_S["DS (Security Scan)\n→ bandit + pip-audit + LLM\n→ Tag: SER if violations"]
        OA_S["OA (Output Alignment)\n→ Kimi (tdd_analysis)\n→ Tag: TRAP if gaps"]
        VB_S["VB (Version Bump)\n→ Orchestrator internal"]
        GIT_S["GIT (Atomic Commit)\n→ Delegated to orchestrator"]
        CL_S["CL (Cleanup)\n→ Remove temp artifacts"]
        CCP_S["CCP (Checkpoint)\n→ Save state to DB"]
        AD_S["AD (Dashboard Update)\n→ Broadcast status"]
    end
```

### 7.3 Fast-Track Detection

```mermaid
flowchart TD
    TASK_DESC["Task description"] --> SCAN["Scan for\nfast-track patterns"]

    subgraph PATTERNS["FAST_TRACK_PATTERNS"]
        P1["css, style, color, font, theme"]
        P2["icon, logo, image"]
        P3["copy, text change, label,\nplaceholder, tooltip, typo"]
        P4["config, env, readme,\ncomment, rename, asset"]
    end

    SCAN --> MATCH{"Any pattern\nfound?"}
    MATCH -->|"Yes"| FAST["5-Step Fast-Track\nAC → GREEN → OA → GIT → AD"]
    MATCH -->|"No"| FULL["13-Step Full Pipeline"]
```

---

## 8. Phi3 Summarization & DoC System

### 8.1 Phi3 Summarization Flow

```mermaid
sequenceDiagram
    participant Chat as Chat/Execution Event
    participant Q as Phi3 Queue (max 50)
    participant P3 as Phi3:mini (Ollama)
    participant DB as Database

    Chat->>Q: queue_summary(user_query, llm_response)

    loop Async processing
        Q->>P3: POST /api/generate<br/>temperature: 0.1, num_predict: 256
        P3-->>Q: {summary, decisions[], keywords[]}

        Q->>DB: INSERT chat_summaries<br/>(chat_id, session_id, instance_name,<br/>parent_worker, user_query,<br/>llm_response_summary, keywords,<br/>decisions_made)

        alt persist_full = True
            Q->>DB: UPDATE chat_summaries<br/>SET full_llm_response = ...
            Q->>P3: _update_doc() (DoC merge)
            P3-->>DB: UPDATE context_summaries<br/>(merged DoC, < 3000 words)
        end
    end
```

### 8.2 Summarization Prompt Template

```
Summarize concisely:
USER: {user_query[:500]}
AI: {llm_response[:1000]}

JSON: {"summary":..., "decisions":[...], "keywords":[...]}
```

### 8.3 Document of Context (DoC) Template

```mermaid
flowchart TD
    subgraph DOC_TEMPLATE["DoC Structure (< 3000 words)"]
        DOC1["## DECISIONS MADE\n- [Decision]: [Rationale] (Chat: id)"]
        DOC2["## REQUIREMENTS CAPTURED\n- [Requirement description]"]
        DOC3["## CURRENT STATE\n- Project: [status]\n- Phase: [current phase]\n- Active Tasks: [summary]"]
        DOC4["## KEY CONTEXT\n- [Important context affecting\nfuture decisions]"]
        DOC5["## ACTION ITEMS\n- [ ] Pending action\n- [x] Completed action"]
        DOC6["## SESSION HISTORY\n- One-line summaries\nNewest first, max 20 entries"]
    end
```

### 8.4 DoC Update Prompt Template

```
System: You are a context document maintainer.

Prompt: Update the Document of Context (DoC) by merging new
        information into existing sections. Do NOT rewrite from scratch.

        CURRENT DoC: {current_doc}

        NEW CHAT EXCHANGE:
        USER: {user_query}
        AI SUMMARY: {summary}
        DECISIONS: {decisions}
        KEYWORDS: {keywords}
        CHAT ID: {chat_id}

        RULES:
        - Merge new info into existing sections
        - Remove superseded decisions and completed actions
        - Keep total under 3000 words
        - Output ONLY the updated DoC
```

### 8.5 Execution Summary (queue_execution_summary)

```mermaid
flowchart LR
    EXEC_EVENT["Autonomous\nexecution event"] --> FORMAT["Format:\nuser_query = '[EXEC] task={id}\nphase={N} step={step}\nworker={name}: {preview}'\n\nllm_response = response[:500]"]
    FORMAT --> QUEUE["Queue with:\nsession_id = 'exec_{project_id}'\npersist_full = True\n+ project_id, task_id, phase"]
    QUEUE --> FEEDS["Feeds:\n- DoC (crash recovery)\n- chat_summaries (audit)\n- training_data context"]
```

---

## 9. Known Failure Patterns (Hardcoded in Prompts)

### 9.1 TRAP — Scope Violations (Most Common)

```mermaid
flowchart TD
    subgraph TRAP_PATTERNS["TRAP Failure Patterns"]
        TP1["Writing files outside\nassigned module directory"]
        TP2["Redefining classes/functions\nalready exported by\nanother module"]
        TP3["Shadowing imports:\nimporting a name that\nconflicts with types.json"]
    end
```

### 9.2 SER — Security/Auth Failures

```mermaid
flowchart TD
    subgraph SER_PATTERNS["SER Failure Patterns"]
        SP1["Missing CORS headers or\nhardcoding Allow-Origin: *"]
        SP2["JWT tokens in localStorage\ninstead of httpOnly cookies"]
        SP3["CSRF protection absent\non POST/PUT/DELETE"]
        SP4["Passwords as plaintext or\nweak hash (MD5/SHA1)\ninstead of bcrypt/argon2"]
        SP5["API keys/secrets\nhardcoded in source"]
    end
```

### 9.3 HRO — Async/Concurrency Errors

```mermaid
flowchart TD
    subgraph HRO_PATTERNS["HRO Failure Patterns"]
        HP1["Blocking I/O inside async\nwithout await or to_thread()"]
        HP2["Race conditions from\nshared mutable state"]
        HP3["Missing await on coroutines\n(silently creates objects)"]
    end
```

### 9.4 DOM — Data/Model Integrity

```mermaid
flowchart TD
    subgraph DOM_PATTERNS["DOM Failure Patterns"]
        DP1["Foreign key references to\ntables not yet created"]
        DP2["Nullable columns without\nNone-checks in business logic"]
        DP3["Enum values in code\ndon't match db_schema.sql"]
    end
```

### 9.5 HAL — External Service/Environment

```mermaid
flowchart TD
    subgraph HAL_PATTERNS["HAL Failure Patterns"]
        HP_1["Hardcoded localhost URLs\ninstead of env vars"]
        HP_2["Missing retry/timeout on\nexternal HTTP calls"]
        HP_3["Assuming file paths exist\nwithout Path.exists() check"]
    end
```

---

## 10. Brain System Prompts (Exact Templates)

### 10.1 System Prompts Per Method

```mermaid
flowchart TD
    subgraph SYSTEM_PROMPTS["Brain System Prompts (role prefix for all)"]
        SP_BASE["Base: 'You are the reasoning core\nof an autonomous software factory.'"]

        SP_REJ["analyze_rejection:\n+ 'Analyze gate rejections and\nrecommend the best recovery strategy.\nAlways respond with valid JSON.'"]

        SP_ESC["compose_escalation:\n+ 'Compose clear, actionable\nescalations for human operators.\nAlways respond with valid JSON.'"]

        SP_DL["resolve_deadlock:\n+ 'Resolve dependency deadlocks\nby identifying the best task\nto run first.\nAlways respond with valid JSON.'"]

        SP_WK["suggest_worker:\n+ 'Recommend the best worker\nfor a retry based on\nfailure history.\nAlways respond with valid JSON.'"]

        SP_IR["interpret_resolution:\n+ 'Interpret human decisions and\ntranslate them into actionable\nsystem instructions.\nAlways respond with valid JSON.'"]
    end
```

### 10.2 Worker Available for Brain

```mermaid
flowchart LR
    BRAIN_ROLE["orchestrator_reasoning role"] --> PRIMARY["DeepSeek (16B)\nPrimary"]
    PRIMARY -.->|"fails"| FALLBACK_W["Qwen (7B)\nFallback"]
    FALLBACK_W -.->|"fails"| HEURISTIC["Deterministic\nheuristic logic"]
```
