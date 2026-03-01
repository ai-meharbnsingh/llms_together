import { useState } from "react";

const C = {
  bg: "#0a0e17", panel: "#111827", border: "#1e293b", text: "#e2e8f0", dim: "#64748b",
  cyan: "#06b6d4", blue: "#3b82f6", green: "#22c55e", yellow: "#eab308",
  red: "#ef4444", orange: "#f97316", purple: "#a855f7", lime: "#84cc16",
  teal: "#14b8a6", pink: "#ec4899", indigo: "#6366f1", sky: "#0ea5e9",
};

const TABS = [
  { id: "watchdog", label: "WATCHDOG BOOT", icon: "🐕", color: C.cyan, desc: "Python PID 1 — what it DOES on startup" },
  { id: "gatekeeper", label: "KIMI GATEKEEPER", icon: "🏛️", color: C.indigo, desc: "Quality gate — approve/reject/tag/escalate" },
  { id: "orchestrator", label: "ORCHESTRATOR", icon: "🎯", color: C.blue, desc: "COO — project setup, blueprints, GSD task planning" },
  { id: "claude_tdd", label: "CLAUDE TDD", icon: "🧪", color: C.green, desc: "12-step TDD protocol — AC through checkpoint" },
  { id: "gemini", label: "GEMINI AUDITOR", icon: "🔍", color: C.yellow, desc: "Architecture & blueprint dual audit" },
  { id: "deepseek", label: "DEEPSEEK", icon: "🧠", color: C.orange, desc: "Complex code gen — algorithms, DB, heavy logic" },
  { id: "qwen", label: "QWEN", icon: "⚡", color: C.lime, desc: "Simple code gen — CRUD, config, boilerplate" },
  { id: "phi3", label: "PHI3 SCRIBE", icon: "📝", color: C.pink, desc: "Paired summarizer — 200-word summaries per chat" },
  { id: "project_router", label: "PROJECT ROUTER", icon: "🗂️", color: C.teal, desc: "Classify project type → inject domain rules" },
  { id: "role_router", label: "ROLE ROUTER", icon: "🔀", color: C.purple, desc: "Map roles → workers, hot-swap from dashboard" },
  { id: "reaper", label: "PROCESS REAPER", icon: "💀", color: C.red, desc: "Ghost/zombie/orphan prevention & cleanup" },
  { id: "dashboard", label: "DASHBOARD", icon: "📊", color: C.sky, desc: "Web UI — real-time status, role swap, escalations" },
];

// ═══ REUSABLE COMPONENTS ═══

const R = ({ n, t, who = "watchdog", tag = "" }) => {
  const whoC = { watchdog: { bg: C.cyan + "20", color: C.cyan, label: "WATCHDOG" }, llm: { bg: C.purple + "20", color: C.purple, label: "LLM" } };
  const w = whoC[who] || whoC.watchdog;
  return (
    <div style={{ display: "flex", gap: 6, padding: "5px 0", borderBottom: `1px solid ${C.border}15`, alignItems: "flex-start" }}>
      <span style={{ color: C.dim, fontSize: 9, minWidth: 20, fontFamily: "monospace" }}>{n}.</span>
      <span style={{ fontSize: 7, padding: "1px 5px", borderRadius: 2, minWidth: 52, textAlign: "center", background: w.bg, color: w.color, fontFamily: "monospace", fontWeight: "bold" }}>{w.label}</span>
      <span style={{ fontSize: 10, color: C.text, fontFamily: "monospace", flex: 1, lineHeight: 1.5 }}>{t}</span>
      {tag && <span style={{ fontSize: 7, color: C.dim, background: C.border, padding: "1px 5px", borderRadius: 2, fontFamily: "monospace", whiteSpace: "nowrap" }}>{tag}</span>}
    </div>
  );
};

const S = ({ title, icon, color, children }) => (
  <div style={{ margin: "10px 0", border: `1px solid ${color}25`, borderRadius: 6, overflow: "hidden" }}>
    <div style={{ background: color + "12", padding: "6px 10px", borderBottom: `1px solid ${color}25` }}>
      <span style={{ color, fontSize: 11, fontFamily: "monospace", fontWeight: "bold" }}>{icon} {title}</span>
    </div>
    <div style={{ padding: "4px 10px" }}>{children}</div>
  </div>
);

// ═══ PAGE 1: WATCHDOG BOOT SEQUENCE (26 rules) ═══

const WatchdogBoot = () => (
  <div>
    <S title="PHASE 1 — INFRA BOOT (python3 main.py)" icon="🚀" color={C.cyan}>
      <R n={1} t="Python process starts as PID 1 — the SOLE writer to SQLite" who="watchdog" tag="TRAP" />
      <R n={2} t="SQLite DB initialized — 14 tables created if not exist, WAL mode ON" who="watchdog" tag="TRAP" />
      <R n={3} t="Check crash recovery — reads watchdog_state.json. If previous crash → RECOVERY MODE" who="watchdog" tag="HRO" />
      <R n={4} t="Startup sweep — reads process_registry.json, kills ALL leftover ghost PIDs from last run" who="watchdog" tag="HRO" />
      <R n={5} t="Prerequisites check — Ollama running? Git available? CLI tools in PATH?" who="watchdog" tag="ENV" />
      <R n={6} t="Workers init — create adapters (CLI for Claude/Kimi/Gemini, Ollama for DeepSeek/Qwen/Phi3), health check each" who="watchdog" tag="HAL" />
      <R n={7} t="Dashboard starts — aiohttp server on :8420, WebSocket ready" who="watchdog" tag="ENV" />
      <R n={8} t="Role Router loads — reads factory_config.json, maps 10 roles → workers" who="watchdog" tag="SER" />
      <R n={9} t="Starts 3 async loops: Drain (5s batch write), Monitor (30s health), State Persist (30s save)" who="watchdog" tag="HRO" />
    </S>

    <S title="PHASE 2 — SPAWN ORCHESTRATOR + ITS PHI3 SCRIBE" icon="🔧" color={C.teal}>
      <R n={10} t="Spawns Orchestrator process — pushes Orchestrator rules/config to it" who="watchdog" tag="SER" />
      <R n={11} t="Spawns 1× Phi3 instance PAIRED with Orchestrator — this is its dedicated scribe (not all 6 yet)" who="watchdog" tag="SER" />
      <R n={12} t="Pushes Phi3 summarization rules to this instance: format, word limit, fields required" who="watchdog" tag="DOM" />
      <R n={13} t={'Creates local PID structure: { orchestrator_pid, phi3_scribe_pid, status: "active", paired: true }'} who="watchdog" tag="HRO" />
      <R n={14} t="Registers both PIDs in process_registry.json for ghost prevention" who="watchdog" tag="HRO" />
      <R n={15} t="Orchestrator is now ALIVE — listening for human input via Dashboard" who="watchdog" tag="SER" />
    </S>

    <S title="PHASE 3 — EVERY INTERACTION = CHAT_ID (human AND LLM replies)" icon="💬" color={C.yellow}>
      <R n={16} t='Human says anything (even "hi") → Watchdog assigns chat_id_001 to this message' who="watchdog" tag="TRAP" />
      <R n={17} t="Orchestrator processes, generates response → Watchdog assigns chat_id_002 to the LLM reply" who="watchdog" tag="TRAP" />
      <R n={18} t="BOTH directions get their own chat_id — human input AND LLM output are separate tracked records" who="watchdog" tag="TRAP" />
      <R n={19} t="Phi3 scribe (paired instance) receives: human msg + LLM response as a conversation pair" who="watchdog" tag="TRAP" />
      <R n={20} t="Phi3 generates summary (200 words ± 20)" who="llm" tag="DOM" />
      <R n={21} t={'Watchdog pushes EACH to DB (chat_summaries): { chat_id, who: "human"|"orchestrator"|"worker_name", summary, actual_chat, session_id, project_id, timestamp }'} who="watchdog" tag="TRAP" />
      <R n={22} t='Status: chat marked "active" during interaction, "closed" when done' who="watchdog" tag="HRO" />
    </S>

    <S title="PID TRACKING STRUCTURE (Watchdog maintains)" icon="📋" color={C.orange}>
      <R n={23} t={'process_registry.json: { watchdog_pid, orchestrator: { pid, status, spawned_at }, phi3_orch_scribe: { pid, parent_pid: orchestrator_pid, status, paired_with: "orchestrator" } }'} who="watchdog" tag="HRO" />
      <R n={24} t="If Orchestrator dies → Watchdog kills its paired Phi3 scribe → respawns both → new PIDs registered" who="watchdog" tag="HRO" />
      <R n={25} t="If Phi3 scribe dies → Watchdog respawns just the scribe → re-pairs with existing Orchestrator PID" who="watchdog" tag="HRO" />
      <R n={26} t='Status values: "active" (running), "closed" (graceful shutdown), "crashed" (unexpected death)' who="watchdog" tag="HRO" />
    </S>
  </div>
);

// ═══ PAGE 2: KIMI GATEKEEPER (28 rules) ═══

const Gatekeeper = () => (
  <div>
    <S title="WHAT WATCHDOG DOES BEFORE CALLING KIMI" icon="🐕" color={C.cyan}>
      <R n={1} t="Receives code output from DeepSeek/Qwen (via message bus)" who="watchdog" tag="TRAP" />
      <R n={2} t="Saves code files to project directory on disk" who="watchdog" tag="ENV" />
      <R n={3} t="Runs test suite (pytest/jest) — captures test report (pass/fail/coverage)" who="watchdog" tag="SER" />
      <R n={4} t="Runs linter (pylint/eslint) — captures lint report" who="watchdog" tag="SER" />
      <R n={5} t="Runs security scan (grep secrets, basic OWASP checks) — captures scan report" who="watchdog" tag="SER" />
      <R n={6} t="Packages and sends to Kimi: code files + Task.md + test report + lint report + scan report" who="watchdog" tag="TRAP" />
    </S>

    <S title="WHAT KIMI GATEKEEPER THINKS (receives package, outputs verdict)" icon="🏛️" color={C.indigo}>
      <R n={7} t="Reads original Task.md AC points — this is the ONLY source of truth" who="llm" tag="DOM" />
      <R n={8} t="Reads Watchdog's test report — checks: do tests cover all AC points? All passing?" who="llm" tag="DOM" />
      <R n={9} t="Reads Watchdog's lint report — checks: code quality, imports valid, no dead code" who="llm" tag="SER" />
      <R n={10} t="Reads Watchdog's scan report — checks: no hardcoded secrets, no TODOs/FIXMEs" who="llm" tag="SER" />
      <R n={11} t="Reads actual code — checks: matches blueprint architecture, follows project context rules" who="llm" tag="DOM" />
      <R n={12} t="Scores 0-100% across 4 categories: Correctness, Completeness, Security, Quality" who="llm" tag="DOM" />
    </S>

    <S title="KIMI'S OUTPUT — JSON VERDICT" icon="📋" color={C.teal}>
      <R n={13} t={'Output format: {score, verdict: "APPROVE"|"REJECT", categories: {correctness, completeness, security, quality}, failures: [], dac_tag}'} who="llm" tag="TRAP" />
      <R n={14} t="If score ≥ 90% → verdict: APPROVE" who="llm" tag="DOM" />
      <R n={15} t="If score < 90% → verdict: REJECT + must include: which AC failed, why, and DaC tag" who="llm" tag="DOM" />
      <R n={16} t="DaC tag is MANDATORY on every REJECT — classifies failure for routing + metrics" who="llm" tag="TRAP" />
    </S>

    <S title="WHAT WATCHDOG DOES AFTER KIMI'S VERDICT" icon="🐕" color={C.cyan}>
      <R n={17} t="Reads Kimi's JSON verdict from response" who="watchdog" tag="TRAP" />
      <R n={18} t="Writes verdict to quality_gates table via DB (direct write — Watchdog is sole writer)" who="watchdog" tag="TRAP" />
      <R n={19} t="If APPROVE → Watchdog creates Git PR, runs git add/commit/push" who="watchdog" tag="ENV" />
      <R n={20} t="If REJECT → Watchdog reads DaC tag, Orchestrator decides which TDD step to retry" who="watchdog" tag="HRO" />
      <R n={21} t="If 2× consecutive REJECT on same task → Watchdog writes to escalations table → Dashboard popup" who="watchdog" tag="HRO" />
      <R n={22} t="Updates dashboard_state with latest gate result" who="watchdog" tag="TRAP" />
    </S>

    <S title="DaC FAILURE TAGS — Kimi MUST pick one on REJECT" icon="🏷️" color={C.red}>
      <R n={23} t="TRAP — data flow broken: API returns wrong shape, DB write missing, log gap" who="llm" tag="TRAP" />
      <R n={24} t="SER — service mismatch: wrong endpoint, contract violation, boundary crossed" who="llm" tag="SER" />
      <R n={25} t="DOM — business logic: wrong formula, missing validation, wrong state transition" who="llm" tag="DOM" />
      <R n={26} t="HRO — reliability: no error handling, no retry, no timeout, crash risk" who="llm" tag="HRO" />
      <R n={27} t="HAL — infrastructure: wrong runtime assumption, missing dep, wrong port" who="llm" tag="HAL" />
      <R n={28} t="ENV — deployment: missing env var, wrong path, config error, permission issue" who="llm" tag="ENV" />
    </S>
  </div>
);

// ═══ PAGE 3: ORCHESTRATOR COO (32 rules) ═══

const Orchestrator = () => (
  <div>
    <S title="PHASE 0 — PROJECT CREATION (human says 'build X')" icon="📁" color={C.blue}>
      <R n={1} t="Human submits project goal via Dashboard chat — Watchdog assigns chat_id, routes to Orchestrator" who="watchdog" tag="TRAP" />
      <R n={2} t="Orchestrator receives goal string — calls Project Router to auto-classify type (web/mobile/iot/plm)" who="llm" tag="DOM" />
      <R n={3} t="Watchdog creates project directory: ~/working/<project_name>/ with subdirs (backend, frontend, tests, config, etc.)" who="watchdog" tag="ENV" />
      <R n={4} t="Watchdog runs git init in project directory (or git clone if repo URL provided)" who="watchdog" tag="ENV" />
      <R n={5} t="Watchdog writes project record to DB: { project_id, name, description, status: 'active', project_path }" who="watchdog" tag="TRAP" />
      <R n={6} t="Orchestrator now has current_project set — all subsequent operations scoped to this project" who="llm" tag="SER" />
    </S>

    <S title="PHASE 0 — BLUEPRINT GENERATION" icon="📐" color={C.teal}>
      <R n={7} t="Orchestrator asks Role Router for 'blueprint_generation' worker → gets Claude (primary) or Gemini (fallback)" who="llm" tag="SER" />
      <R n={8} t="Orchestrator builds blueprint prompt: project name + description + requirements + Project Router context rules" who="llm" tag="DOM" />
      <R n={9} t="Watchdog spawns Claude subprocess, registers PID with Reaper, sends prompt" who="watchdog" tag="HRO" />
      <R n={10} t="Claude generates blueprint: architecture, DB schema, APIs, frontend, 5 phases, tasks + AC, security, testing" who="llm" tag="DOM" />
      <R n={11} t="Watchdog saves blueprint to DB: blueprint_revisions table { project_id, version: 1, content }" who="watchdog" tag="TRAP" />
    </S>

    <S title="PHASE 0 — DUAL AUDIT (Kimi + Gemini in parallel)" icon="🔍" color={C.yellow}>
      <R n={12} t="Orchestrator asks Role Router for 'gatekeeper_review' → Kimi, and 'architecture_audit' → Gemini" who="llm" tag="SER" />
      <R n={13} t="Watchdog spawns BOTH audit subprocesses in parallel (asyncio.gather)" who="watchdog" tag="HRO" />
      <R n={14} t="Kimi audits COMPLETENESS: every task has AC? dependencies explicit? no ambiguous requirements?" who="llm" tag="DOM" />
      <R n={15} t="Gemini audits ARCHITECTURE: API design? DB normalized? indexes? security? scalability?" who="llm" tag="DOM" />
      <R n={16} t="Watchdog collects both results — if either found issues, Orchestrator revises blueprint and re-audits" who="watchdog" tag="HRO" />
      <R n={17} t="If both pass → Watchdog presents blueprint to Human via Dashboard for approval" who="watchdog" tag="TRAP" />
      <R n={18} t="Human approves → Watchdog writes: blueprint_approved_by='HUMAN', current_phase=1" who="watchdog" tag="TRAP" />
    </S>

    <S title="PHASE 1-3 — GSD TASK PLANNING" icon="📋" color={C.green}>
      <R n={19} t="Orchestrator asks Role Router for 'task_planning_gsd' → Claude (primary) or Kimi (fallback)" who="llm" tag="SER" />
      <R n={20} t="Orchestrator sends phase requirements to GSD planner: 'Break this phase into concrete tasks'" who="llm" tag="DOM" />
      <R n={21} t="GSD planner returns JSON array: [{module, description, complexity_hint, acceptance_criteria, dependencies}]" who="llm" tag="DOM" />
      <R n={22} t="Watchdog creates task records in DB: task_id format = task_<project>_<phase>_<NNN>, status='pending'" who="watchdog" tag="TRAP" />
      <R n={23} t="Orchestrator asks Kimi to classify each task complexity (LOW/HIGH) — writes Task.md file per task" who="llm" tag="DOM" />
      <R n={24} t="Watchdog saves Task.md to factory_state/tasks/<task_id>.md with AC, context rules, constraints" who="watchdog" tag="ENV" />
    </S>

    <S title="PHASE 1-3 — TASK DISPATCH & FLOW" icon="🔀" color={C.orange}>
      <R n={25} t="Watchdog reads task complexity — LOW → assigns to 'code_generation_simple' role, HIGH → 'code_generation_complex' role" who="watchdog" tag="SER" />
      <R n={26} t="Role Router resolves: simple → Qwen (fallback DeepSeek), complex → DeepSeek (fallback Qwen)" who="watchdog" tag="SER" />
      <R n={27} t="Watchdog updates task: status='in_progress', assigned_to=<worker>, assigned_at=now()" who="watchdog" tag="TRAP" />
      <R n={28} t="After code gen completes → Orchestrator dispatches to 'tdd_testing' role (Claude)" who="llm" tag="SER" />
      <R n={29} t="After TDD completes → Orchestrator dispatches to 'gatekeeper_review' role (Kimi)" who="llm" tag="SER" />
      <R n={30} t="Kimi APPROVE → task status='approved', Git PR created" who="watchdog" tag="TRAP" />
      <R n={31} t="Kimi REJECT 2× → Watchdog creates escalation, task status='blocked', Dashboard shows popup" who="watchdog" tag="HRO" />
      <R n={32} t="Human resolves escalation → Watchdog unblocks task, re-enters TDD pipeline" who="watchdog" tag="HRO" />
    </S>
  </div>
);

// ═══ PAGE 4: CLAUDE TDD (34 rules) ═══

const ClaudeTDD = () => (
  <div>
    <S title="WHAT WATCHDOG DOES BEFORE TDD STARTS" icon="🐕" color={C.cyan}>
      <R n={1} t="Receives code output from DeepSeek/Qwen worker + Task.md with AC points" who="watchdog" tag="TRAP" />
      <R n={2} t="Asks Role Router for 'tdd_testing' → Claude (primary) or Gemini (fallback)" who="watchdog" tag="SER" />
      <R n={3} t="Spawns Claude subprocess, registers PID with Reaper (timeout = 180s)" who="watchdog" tag="HRO" />
      <R n={4} t="Spawns paired Phi3 scribe instance for Claude's TDD session" who="watchdog" tag="SER" />
      <R n={5} t="Sends prompt: Task.md + code files + project context rules + TDD system prompt" who="watchdog" tag="TRAP" />
      <R n={6} t="Updates task: status='testing', assigned_to='claude', current_step='AC'" who="watchdog" tag="TRAP" />
    </S>

    <S title="STEP 1-3: AC → RED → GREEN (Claude thinks)" icon="🔴🟢" color={C.green}>
      <R n={7} t="STEP 1 — AC: Claude reads Task.md, extracts acceptance criteria, defines pass/fail for each" who="llm" tag="DOM" />
      <R n={8} t="STEP 2 — TDE-RED: Claude writes failing tests for every AC point (Vitest/pytest)" who="llm" tag="DOM" />
      <R n={9} t="Watchdog runs test suite — verifies ALL tests FAIL (Red phase confirmed)" who="watchdog" tag="SER" />
      <R n={10} t="STEP 3 — TDE-GREEN: Claude writes MINIMUM code to make all tests pass" who="llm" tag="DOM" />
      <R n={11} t="Watchdog runs test suite — verifies ALL tests PASS (Green phase confirmed)" who="watchdog" tag="SER" />
      <R n={12} t="If tests still fail → Claude retries GREEN with specific failure context" who="llm" tag="HRO" />
    </S>

    <S title="STEP 4-5: BC → BF (Bug Check & Fix)" icon="🐛" color={C.red}>
      <R n={13} t="STEP 4 — BC: Claude performs static analysis — looks for bugs, logic errors, edge cases" who="llm" tag="DOM" />
      <R n={14} t="Watchdog runs linter (pylint/eslint) — captures report, sends to Claude" who="watchdog" tag="SER" />
      <R n={15} t="If bugs found → Claude creates structured bug ticket: { id, severity, category, repro_steps }" who="llm" tag="DOM" />
      <R n={16} t="STEP 5 — BF: Claude proposes 3 fix options [FixA, FixB, Defer] for each bug" who="llm" tag="DOM" />
      <R n={17} t="If severity=HIGH → Watchdog escalates to Human via Dashboard before fixing" who="watchdog" tag="HRO" />
      <R n={18} t="Fix applied → Watchdog re-runs test suite to verify fix didn't break anything" who="watchdog" tag="SER" />
    </S>

    <S title="STEP 6-8: SEA → DS → OA (Quality Gates)" icon="🛡️" color={C.yellow}>
      <R n={19} t="STEP 6 — SEA: Claude checks for silent errors — race conditions, async drift, memory leaks, unhandled promises" who="llm" tag="HRO" />
      <R n={20} t="For IoT projects: Claude checks hardware timing, sensor bounds validation, MQTT reconnect logic" who="llm" tag="DOM" />
      <R n={21} t="STEP 7 — DS: Claude performs dependency & security audit — OWASP top 10 checks, grep for secrets" who="llm" tag="SER" />
      <R n={22} t="Watchdog runs 'npm audit' / 'pip audit' — captures vulnerability report" who="watchdog" tag="SER" />
      <R n={23} t="STEP 8 — OA: 3-tier Output Alignment check" who="llm" tag="TRAP" />
      <R n={24} t="OA Tier 1 — Type Sync: Pydantic models ↔ TypeScript types ↔ Zod guards all match" who="llm" tag="TRAP" />
      <R n={25} t="OA Tier 2 — Schema Introspection: migration DDL matches ORM models, FK integrity" who="llm" tag="TRAP" />
      <R n={26} t="OA Tier 3 — Data Pipe: end-to-end data flow verified (DB → API → Frontend)" who="llm" tag="TRAP" />
      <R n={27} t="If schema mismatch found → Watchdog escalates (architectural decision required)" who="watchdog" tag="HRO" />
    </S>

    <S title="STEP 9-13: VB → GIT → CL → CCP → AD (Commit & Checkpoint)" icon="💾" color={C.teal}>
      <R n={28} t="STEP 9 — VB: Claude bumps version in pyproject.toml / package.json" who="llm" tag="ENV" />
      <R n={29} t="STEP 10 — GIT: Watchdog stages files, creates atomic commit (wave-specific files only)" who="watchdog" tag="ENV" />
      <R n={30} t="STEP 11 — CL: Watchdog removes temp files, closes connections, resets test env" who="watchdog" tag="ENV" />
      <R n={31} t="STEP 12 — CCP: Watchdog saves checkpoint to DB: { task_id, worker, step, state_data, files_modified, tests_status }" who="watchdog" tag="TRAP" />
      <R n={32} t="STEP 13 — AD: Watchdog updates dashboard_state: task complete, context usage, tasks_completed_today++" who="watchdog" tag="TRAP" />
      <R n={33} t="Phi3 scribe summarizes entire TDD session (all 13 steps) → saved to chat_summaries" who="watchdog" tag="TRAP" />
      <R n={34} t="Task status updated to 'review' → ready for Kimi Gatekeeper" who="watchdog" tag="TRAP" />
    </S>
  </div>
);

// ═══ PAGE 5: GEMINI AUDITOR (24 rules) ═══

const GeminiAuditor = () => (
  <div>
    <S title="WHEN GEMINI IS CALLED (two contexts)" icon="📌" color={C.yellow}>
      <R n={1} t="Context A — BLUEPRINT AUDIT: called during Phase 0 after Claude generates blueprint" who="watchdog" tag="SER" />
      <R n={2} t="Context B — ARCHITECTURE REVIEW: called ad-hoc by Orchestrator when structural changes needed" who="watchdog" tag="SER" />
      <R n={3} t="Watchdog asks Role Router for 'architecture_audit' → Gemini (primary) or Claude (fallback)" who="watchdog" tag="SER" />
      <R n={4} t="Watchdog spawns Gemini subprocess (CLI or API based on dual_auth config), registers PID" who="watchdog" tag="HRO" />
      <R n={5} t="Spawns paired Phi3 scribe for this Gemini session" who="watchdog" tag="SER" />
    </S>

    <S title="WHAT GEMINI THINKS — BLUEPRINT AUDIT" icon="📐" color={C.teal}>
      <R n={6} t="Reads full blueprint content — architecture section, DB schema, API endpoints, frontend tree" who="llm" tag="DOM" />
      <R n={7} t="Checks API design: RESTful conventions, consistent response format, proper HTTP status codes, versioned endpoints" who="llm" tag="SER" />
      <R n={8} t="Checks DB schema: normalized tables, indexes cover query patterns, FK constraints defined, no data duplication" who="llm" tag="DOM" />
      <R n={9} t="Checks security architecture: auth strategy (JWT/OAuth), encryption at rest/transit, CORS config, rate limiting plan" who="llm" tag="SER" />
      <R n={10} t="Checks scalability: connection pooling, caching strategy, async patterns, DB query complexity" who="llm" tag="HAL" />
      <R n={11} t="Checks phase breakdown: 5 phases logical? dependencies correct? no circular deps between tasks?" who="llm" tag="DOM" />
      <R n={12} t="Checks frontend architecture: component tree makes sense? state management defined? routing clear?" who="llm" tag="DOM" />
    </S>

    <S title="GEMINI'S OUTPUT FORMAT" icon="📋" color={C.orange}>
      <R n={13} t={'Output JSON: { status: "PASS"|"ISSUES_FOUND", findings: [{ severity, category, description, recommendation }], score }'} who="llm" tag="TRAP" />
      <R n={14} t="Severity levels: HIGH (blocks progress), MEDIUM (should fix), LOW (nice to have)" who="llm" tag="DOM" />
      <R n={15} t="Categories: api_design, db_schema, security, scalability, architecture, testing_strategy" who="llm" tag="SER" />
      <R n={16} t="Each finding MUST include a concrete recommendation — not just 'fix this'" who="llm" tag="DOM" />
      <R n={17} t="If status=PASS → Watchdog proceeds to Human approval" who="watchdog" tag="TRAP" />
      <R n={18} t="If status=ISSUES_FOUND with HIGH severity → Orchestrator revises blueprint, re-audits" who="watchdog" tag="HRO" />
    </S>

    <S title="WHAT WATCHDOG DOES AFTER GEMINI'S AUDIT" icon="🐕" color={C.cyan}>
      <R n={19} t="Parses Gemini's JSON response — extracts findings and severity counts" who="watchdog" tag="TRAP" />
      <R n={20} t="Writes audit result to quality_gates table: { task_id, gate_type: 'architecture', passed, findings, executed_by: 'gemini' }" who="watchdog" tag="TRAP" />
      <R n={21} t="Logs each HIGH finding to decision_logs table with reasoning for traceability" who="watchdog" tag="TRAP" />
      <R n={22} t="If audit runs in parallel with Kimi (blueprint dual audit) → Watchdog waits for BOTH before proceeding" who="watchdog" tag="HRO" />
      <R n={23} t="Phi3 scribe summarizes audit session → chat_summaries DB" who="watchdog" tag="TRAP" />
      <R n={24} t="Dashboard refreshes to show audit status in activity feed" who="watchdog" tag="TRAP" />
    </S>
  </div>
);

// ═══ PAGE 6: DEEPSEEK WORKER (22 rules) ═══

const DeepSeekWorker = () => (
  <div>
    <S title="WHEN DEEPSEEK IS CALLED" icon="📌" color={C.orange}>
      <R n={1} t="Watchdog assigns task with complexity=HIGH to 'code_generation_complex' role → DeepSeek (primary)" who="watchdog" tag="SER" />
      <R n={2} t="If DeepSeek offline/crashed → Role Router falls back to Qwen" who="watchdog" tag="HRO" />
      <R n={3} t="Watchdog sends request to local Ollama API (http://localhost:11434/api/generate)" who="watchdog" tag="HAL" />
      <R n={4} t="Model: deepseek-coder-v2:16b | Max context: 65,536 tokens | Timeout: 120s | Retries: 2" who="watchdog" tag="HAL" />
      <R n={5} t="Spawns paired Phi3 scribe for this DeepSeek session" who="watchdog" tag="SER" />
    </S>

    <S title="WHAT WATCHDOG SENDS TO DEEPSEEK" icon="📦" color={C.blue}>
      <R n={6} t="Task.md file content — includes description, acceptance criteria, dependencies" who="watchdog" tag="TRAP" />
      <R n={7} t="Project context rules injected by Project Router — domain-specific constraints (e.g., 'all monetary values as integers')" who="watchdog" tag="DOM" />
      <R n={8} t="Blueprint excerpt — relevant architecture section for this task's module" who="watchdog" tag="DOM" />
      <R n={9} t="Existing code context — related files from project dir (if any exist from prior tasks)" who="watchdog" tag="TRAP" />
      <R n={10} t="System prompt: 'You are a senior backend engineer. Output production-ready code with types, error handling, tests.'" who="watchdog" tag="DOM" />
    </S>

    <S title="WHAT DEEPSEEK THINKS (complex code generation)" icon="🧠" color={C.orange}>
      <R n={11} t="Reads AC points — plans implementation approach for complex logic (algorithms, state machines, DB schemas)" who="llm" tag="DOM" />
      <R n={12} t="Generates backend code: API routes, DB models, migrations, business logic, validation" who="llm" tag="DOM" />
      <R n={13} t="Generates corresponding test stubs (DeepSeek writes skeleton tests, Claude TDD makes them rigorous)" who="llm" tag="DOM" />
      <R n={14} t="Follows project context rules — e.g., multi-tenant queries include tenant_id, monetary values as integers" who="llm" tag="DOM" />
      <R n={15} t="Output: complete file contents with path headers (e.g., '# FILE: backend/api/routes.py')" who="llm" tag="TRAP" />
    </S>

    <S title="WHAT WATCHDOG DOES AFTER DEEPSEEK RESPONDS" icon="🐕" color={C.cyan}>
      <R n={16} t="Parses response — extracts file contents by path headers" who="watchdog" tag="TRAP" />
      <R n={17} t="Writes each file to project directory on disk" who="watchdog" tag="ENV" />
      <R n={18} t="Records token usage: { prompt_tokens, completion_tokens } for cost tracking" who="watchdog" tag="TRAP" />
      <R n={19} t="Updates dashboard_state: DeepSeek status='idle', context usage updated" who="watchdog" tag="TRAP" />
      <R n={20} t="Phi3 scribe summarizes: what was built, key decisions, files created" who="watchdog" tag="TRAP" />
      <R n={21} t="Saves checkpoint: { task_id, worker: 'deepseek', step: 'CODE_GEN_COMPLETE', files_modified }" who="watchdog" tag="TRAP" />
      <R n={22} t="Hands off to Orchestrator → next step is Claude TDD" who="watchdog" tag="SER" />
    </S>
  </div>
);

// ═══ PAGE 7: QWEN WORKER (20 rules) ═══

const QwenWorker = () => (
  <div>
    <S title="WHEN QWEN IS CALLED" icon="📌" color={C.lime}>
      <R n={1} t="Watchdog assigns task with complexity=LOW to 'code_generation_simple' role → Qwen (primary)" who="watchdog" tag="SER" />
      <R n={2} t="If Qwen offline/crashed → Role Router falls back to DeepSeek" who="watchdog" tag="HRO" />
      <R n={3} t="Watchdog sends request to local Ollama API (http://localhost:11434/api/generate)" who="watchdog" tag="HAL" />
      <R n={4} t="Model: qwen2.5-coder:7b | Max context: 32,768 tokens | Timeout: 60s | Retries: 2" who="watchdog" tag="HAL" />
      <R n={5} t="Spawns paired Phi3 scribe for this Qwen session" who="watchdog" tag="SER" />
    </S>

    <S title="WHAT WATCHDOG SENDS TO QWEN" icon="📦" color={C.blue}>
      <R n={6} t="Task.md file content — simplified tasks: CRUD endpoints, config files, boilerplate, simple validation" who="watchdog" tag="TRAP" />
      <R n={7} t="Project context rules (same injection as DeepSeek — domain rules always applied)" who="watchdog" tag="DOM" />
      <R n={8} t="Existing code context — related files so Qwen matches conventions" who="watchdog" tag="TRAP" />
      <R n={9} t="System prompt: 'You are a fast, focused developer. Write clean, simple code. Follow existing patterns exactly.'" who="watchdog" tag="DOM" />
    </S>

    <S title="WHAT QWEN THINKS (simple code generation)" icon="⚡" color={C.lime}>
      <R n={10} t="Reads AC points — plans straightforward implementation (CRUD, forms, config, data models)" who="llm" tag="DOM" />
      <R n={11} t="Generates code: REST endpoints, DB CRUD operations, form components, config files, setup scripts" who="llm" tag="DOM" />
      <R n={12} t="Follows existing code patterns — matches naming, structure, imports from prior task outputs" who="llm" tag="SER" />
      <R n={13} t="Output: file contents with path headers, same format as DeepSeek" who="llm" tag="TRAP" />
    </S>

    <S title="WHAT WATCHDOG DOES AFTER QWEN RESPONDS" icon="🐕" color={C.cyan}>
      <R n={14} t="Parses response — extracts file contents by path headers" who="watchdog" tag="TRAP" />
      <R n={15} t="Writes files to project directory on disk" who="watchdog" tag="ENV" />
      <R n={16} t="Records token usage for cost tracking (Qwen is cheaper — local model)" who="watchdog" tag="TRAP" />
      <R n={17} t="Updates dashboard_state: Qwen status='idle'" who="watchdog" tag="TRAP" />
      <R n={18} t="Phi3 scribe summarizes session" who="watchdog" tag="TRAP" />
      <R n={19} t="Saves checkpoint: { task_id, worker: 'qwen', step: 'CODE_GEN_COMPLETE' }" who="watchdog" tag="TRAP" />
      <R n={20} t="Hands off to Orchestrator → next step is Claude TDD" who="watchdog" tag="SER" />
    </S>
  </div>
);

// ═══ PAGE 8: PHI3 SCRIBE (24 rules) ═══

const Phi3Scribe = () => (
  <div>
    <S title="ARCHITECTURE — ONE PHI3 PER WORKER (1:1 pairing)" icon="🔗" color={C.pink}>
      <R n={1} t="Phi3Manager creates one Phi3Instance per active worker: phi3-orchestrator, phi3-deepseek, phi3-qwen, phi3-claude, phi3-kimi, phi3-gemini" who="watchdog" tag="SER" />
      <R n={2} t="Each instance has its own asyncio.Queue (maxsize=50) — independent processing" who="watchdog" tag="HAL" />
      <R n={3} t="Model: phi3:mini | Max context: 4,096 tokens | Timeout: 30s | Retries: 1" who="watchdog" tag="HAL" />
      <R n={4} t="All instances share same Ollama endpoint but process independently (non-blocking async)" who="watchdog" tag="HAL" />
      <R n={5} t="Each instance identified by name: 'phi3-<parent_worker>' in all DB records" who="watchdog" tag="TRAP" />
    </S>

    <S title="WHAT WATCHDOG SENDS TO PHI3" icon="📦" color={C.blue}>
      <R n={6} t="After every LLM interaction: Watchdog sends { user_query (≤500 chars), llm_response (≤1000 chars) } pair" who="watchdog" tag="TRAP" />
      <R n={7} t="Includes metadata: session_id, project_id, phase, task_id — for DB attribution" who="watchdog" tag="TRAP" />
      <R n={8} t="Watchdog generates unique chat_id for each summary request: 'chat_<uuid12>'" who="watchdog" tag="TRAP" />
      <R n={9} t="If queue is FULL (50 items) → drops OLDEST item, adds newest (no blocking)" who="watchdog" tag="HRO" />
    </S>

    <S title="WHAT PHI3 THINKS (summarization)" icon="📝" color={C.pink}>
      <R n={10} t="Receives conversation pair: USER said X, AI responded Y" who="llm" tag="DOM" />
      <R n={11} t="Generates concise summary (target: 200 words ± 20)" who="llm" tag="DOM" />
      <R n={12} t="Extracts decisions made in this exchange (if any)" who="llm" tag="DOM" />
      <R n={13} t="Extracts keywords for searchability (up to 20 keywords, stopwords removed)" who="llm" tag="DOM" />
      <R n={14} t={'Output JSON: { summary: "...", decisions: [...], keywords: [...] }'} who="llm" tag="TRAP" />
      <R n={15} t="If Phi3 output isn't valid JSON → fallback: first 200 chars as summary, empty arrays" who="llm" tag="HRO" />
    </S>

    <S title="WHAT WATCHDOG DOES AFTER PHI3 RESPONDS" icon="🐕" color={C.cyan}>
      <R n={16} t="Phi3 instance writes to DB via message bus (not direct — same rule as everyone)" who="watchdog" tag="TRAP" />
      <R n={17} t={'Writes to chat_summaries: { chat_id, session_id, instance_name, parent_worker, user_query, summary, keywords, decisions, project_id, task_id }'} who="watchdog" tag="TRAP" />
      <R n={18} t="Keywords stored as JSON array — used for context recovery search" who="watchdog" tag="TRAP" />
      <R n={19} t="Decisions stored as JSON array — used for Global Learning Log queries" who="watchdog" tag="TRAP" />
    </S>

    <S title="FAILURE & LIFECYCLE" icon="⚠️" color={C.red}>
      <R n={20} t="If Phi3 instance crashes → warning logged, parent worker CONTINUES (Phi3 is non-critical)" who="watchdog" tag="HRO" />
      <R n={21} t="Watchdog respawns crashed Phi3 instance on next monitoring cycle (30s)" who="watchdog" tag="HRO" />
      <R n={22} t="If parent worker dies → Watchdog kills paired Phi3 instance (orphan prevention)" who="watchdog" tag="HRO" />
      <R n={23} t="On factory shutdown: Phi3 instances stopped FIRST (before workers) — ordered teardown" who="watchdog" tag="HRO" />
      <R n={24} t="Context Manager uses Phi3 for compression: when worker context >70%, Phi3 summarizes full history → compressed state" who="watchdog" tag="HRO" />
    </S>
  </div>
);

// ═══ PAGE 9: PROJECT ROUTER (26 rules) ═══

const ProjectRouter = () => (
  <div>
    <S title="WHEN PROJECT ROUTER IS CALLED" icon="📌" color={C.teal}>
      <R n={1} t="Called by Orchestrator during Phase 0 — right after human submits project goal" who="watchdog" tag="SER" />
      <R n={2} t="Input: raw project goal string (e.g., 'Build an e-commerce marketplace for handmade goods')" who="watchdog" tag="TRAP" />
      <R n={3} t="Output: ProjectContext object with { type, sub_type, context_rules, tech_stack, tdd_overrides }" who="watchdog" tag="TRAP" />
    </S>

    <S title="CLASSIFICATION LOGIC (deterministic Python — no LLM)" icon="🗂️" color={C.teal}>
      <R n={4} t="Step 1: Check for explicit tag in goal string: [WEB:ecommerce], [IOT:sensor_system], etc." who="watchdog" tag="DOM" />
      <R n={5} t="Step 2: If no tag → auto-classify via keyword matching against 15 keyword groups" who="watchdog" tag="DOM" />
      <R n={6} t="Keywords scored by frequency — highest match wins (e.g., 'mqtt' + 'sensor' + 'esp32' → iot/sensor_system)" who="watchdog" tag="DOM" />
      <R n={7} t="If zero keywords match → default to web/website (most common project type)" who="watchdog" tag="DOM" />
      <R n={8} t="4 top-level types: web, mobile, iot, plm" who="watchdog" tag="DOM" />
      <R n={9} t="14 sub-types total: web(5) + mobile(3) + iot(3) + plm(4) — but the sub-type just means different set of domain rules" who="watchdog" tag="DOM" />
    </S>

    <S title="DOMAIN RULES — INJECTED INTO EVERY PROMPT (examples)" icon="📜" color={C.orange}>
      <R n={10} t="web/ecommerce: 'All monetary values stored as integers (paise/cents) — never float'" who="watchdog" tag="DOM" />
      <R n={11} t="web/saas: 'Multi-tenant architecture — every DB query must include tenant_id'" who="watchdog" tag="DOM" />
      <R n={12} t="iot/sensor_system: 'All sensor readings must have: timestamp (UTC), device_id, value, unit, quality_flag'" who="watchdog" tag="DOM" />
      <R n={13} t="plm/bom_management: 'BOM hierarchy: Assembly → Sub-assembly → Component → Raw material (tree structure)'" who="watchdog" tag="DOM" />
      <R n={14} t="plm/tolerance_calculator: 'ALL calculations must use Decimal type — NEVER float for precision'" who="watchdog" tag="DOM" />
      <R n={15} t="Each sub-type has 7-12 domain rules — these are NON-NEGOTIABLE constraints on all generated code" who="watchdog" tag="DOM" />
    </S>

    <S title="TDD OVERRIDES & TECH STACK" icon="🧪" color={C.green}>
      <R n={16} t="Each sub-type defines extra TDD gates beyond standard 12-step (e.g., 'payment_idempotency_test' for ecommerce)" who="watchdog" tag="SER" />
      <R n={17} t="Security focus overrides: ecommerce gets PCI-DSS, saas gets tenant isolation, iot gets MQTT auth" who="watchdog" tag="SER" />
      <R n={18} t="Performance targets: ecommerce 'page_load < 2s', api 'p95 < 100ms', iot 'data_loss < 0.1%'" who="watchdog" tag="SER" />
      <R n={19} t="Tech stack defaults: recommended frameworks, DBs, tools per sub-type (suggestions, not mandates)" who="watchdog" tag="HAL" />
    </S>

    <S title="HOW INJECTION WORKS" icon="💉" color={C.purple}>
      <R n={20} t="inject_into_prompt(): wraps base prompt with '═══ PROJECT CONTEXT ═══' block containing all domain rules" who="watchdog" tag="TRAP" />
      <R n={21} t="inject_into_task_md(): appends '## Project Context' section to every Task.md file" who="watchdog" tag="TRAP" />
      <R n={22} t="Every worker (DeepSeek, Qwen, Claude, Kimi, Gemini) receives injected context — no exceptions" who="watchdog" tag="TRAP" />
      <R n={23} t="Custom rules from factory_config.json merged on top of built-in rules per sub-type" who="watchdog" tag="SER" />
      <R n={24} t="Orchestrator calls inject_into_prompt() before EVERY worker dispatch" who="watchdog" tag="TRAP" />
      <R n={25} t="Kimi Gatekeeper verifies code follows injected rules — rejects if domain rules violated" who="llm" tag="DOM" />
      <R n={26} t="Rules are IMMUTABLE within a project — changing type requires new project" who="watchdog" tag="DOM" />
    </S>
  </div>
);

// ═══ PAGE 10: ROLE ROUTER (22 rules) ═══

const RoleRouterPage = () => (
  <div>
    <S title="WHAT ROLE ROUTER IS (pure Python, no LLM)" icon="🔀" color={C.purple}>
      <R n={1} t="Decouples ROLES (what needs doing) from WORKERS (who does it)" who="watchdog" tag="SER" />
      <R n={2} t="Loaded by Watchdog during boot — reads 'roles' section from factory_config.json" who="watchdog" tag="SER" />
      <R n={3} t="Holds in-memory dict: role_name → RoleAssignment { primary_worker, fallback_worker, updated_at }" who="watchdog" tag="SER" />
      <R n={4} t="Every component calls router.get_worker(role) — NEVER hardcodes worker names" who="watchdog" tag="SER" />
    </S>

    <S title="10 DEFINED ROLES" icon="📋" color={C.blue}>
      <R n={5} t="code_generation_simple: primary=qwen, fallback=deepseek — CRUD, config, boilerplate" who="watchdog" tag="SER" />
      <R n={6} t="code_generation_complex: primary=deepseek, fallback=qwen — algorithms, DB schemas, heavy logic" who="watchdog" tag="SER" />
      <R n={7} t="tdd_testing: primary=claude, fallback=gemini — 12-step TDD protocol execution" who="watchdog" tag="SER" />
      <R n={8} t="gatekeeper_review: primary=kimi, fallback=claude — quality gate, approve/reject with DaC tag" who="watchdog" tag="SER" />
      <R n={9} t="architecture_audit: primary=gemini, fallback=claude — blueprint & architecture review" who="watchdog" tag="SER" />
      <R n={10} t="task_planning_gsd: primary=claude, fallback=kimi — break phases into granular tasks" who="watchdog" tag="SER" />
      <R n={11} t="blueprint_generation: primary=claude, fallback=gemini — full project blueprint" who="watchdog" tag="SER" />
      <R n={12} t="summarization: primary=phi3, fallback=null — chat summarization (no fallback, non-critical)" who="watchdog" tag="SER" />
      <R n={13} t="frontend_design: primary=claude, fallback=gemini — design-to-code via Figma MCP context" who="watchdog" tag="SER" />
      <R n={14} t="project_classification: primary=kimi, fallback=claude — classify project type/complexity" who="watchdog" tag="SER" />
    </S>

    <S title="HOW GET_WORKER RESOLVES" icon="⚙️" color={C.teal}>
      <R n={15} t="Step 1: Look up role in _assignments dict — if not found, log error, return None" who="watchdog" tag="SER" />
      <R n={16} t="Step 2: Try primary worker — if adapter exists in workers dict, return it" who="watchdog" tag="SER" />
      <R n={17} t="Step 3: If primary missing/crashed → try fallback worker — log warning about fallback usage" who="watchdog" tag="HRO" />
      <R n={18} t="Step 4: If both unavailable → log error, return None → caller handles gracefully" who="watchdog" tag="HRO" />
    </S>

    <S title="HOT-SWAP FROM DASHBOARD (no restart)" icon="🔥" color={C.orange}>
      <R n={19} t="Dashboard sends WebSocket command: { action: 'swap_role', role, primary, fallback }" who="watchdog" tag="SER" />
      <R n={20} t="Role Router validates: role must be in VALID_ROLES set, workers must exist in workers dict" who="watchdog" tag="SER" />
      <R n={21} t="Swaps in-memory immediately — next get_worker() call uses new assignment" who="watchdog" tag="SER" />
      <R n={22} t="Persists change to factory_config.json on disk — survives restart" who="watchdog" tag="ENV" />
    </S>
  </div>
);

// ═══ PAGE 11: PROCESS REAPER (28 rules) ═══

const ProcessReaper = () => (
  <div>
    <S title="PURPOSE — ZERO GHOST PROCESSES (ever)" icon="💀" color={C.red}>
      <R n={1} t="Every spawned process MUST be tracked — no fire-and-forget subprocesses allowed" who="watchdog" tag="HRO" />
      <R n={2} t="Reaper maintains PID registry: { pid, name, parent_name, pgid, started_at, last_heartbeat, max_silent_seconds }" who="watchdog" tag="HRO" />
      <R n={3} t="Registry persisted to process_registry.json on disk — survives Watchdog crash for recovery" who="watchdog" tag="HRO" />
      <R n={4} t="Integrated into Watchdog's 30s monitoring loop — check_all() called every cycle" who="watchdog" tag="HRO" />
    </S>

    <S title="STARTUP SWEEP (first thing on boot)" icon="🧹" color={C.orange}>
      <R n={5} t="Reads old process_registry.json from disk (if exists from previous crashed session)" who="watchdog" tag="HRO" />
      <R n={6} t="For each PID in registry: check if still alive (os.kill(pid, 0))" who="watchdog" tag="HRO" />
      <R n={7} t="If alive AND not our current PID → SIGTERM, wait 0.5s, SIGKILL if still alive" who="watchdog" tag="HRO" />
      <R n={8} t="Also runs pgrep -f 'autonomous_factory' to catch any processes not in registry" who="watchdog" tag="HRO" />
      <R n={9} t="Deletes old registry file after sweep — fresh registry created for new session" who="watchdog" tag="HRO" />
    </S>

    <S title="REGISTRATION & HEARTBEAT" icon="💓" color={C.green}>
      <R n={10} t="register(): called when any process/subprocess spawned — adds to in-memory dict + persists to disk" who="watchdog" tag="HRO" />
      <R n={11} t="unregister(): called on clean process exit — removes from tracking" who="watchdog" tag="HRO" />
      <R n={12} t="heartbeat(): called by worker adapters during long operations — updates last_heartbeat timestamp" who="watchdog" tag="HRO" />
      <R n={13} t="CLI subprocess tracking: track_subprocess() called when claude/kimi/gemini CLI spawned" who="watchdog" tag="HRO" />
      <R n={14} t="Each tracked process has max_silent_seconds (default 120s, CLI workers get timeout+30s)" who="watchdog" tag="HRO" />
    </S>

    <S title="MONITORING — check_all() every 30s" icon="👁️" color={C.yellow}>
      <R n={15} t="For each tracked PID: is it alive? (os.kill(pid, 0) — signal 0 checks existence)" who="watchdog" tag="HRO" />
      <R n={16} t="If DEAD → remove from registry, kill its children, log as 'missing'" who="watchdog" tag="HRO" />
      <R n={17} t="If ALIVE but silent > max_silent_seconds → classify as GHOST" who="watchdog" tag="HRO" />
      <R n={18} t="GHOST + is_critical=true (e.g., claude) → escalate to Human before killing" who="watchdog" tag="HRO" />
      <R n={19} t="GHOST + is_critical=false → SIGKILL immediately, remove from registry" who="watchdog" tag="HRO" />
      <R n={20} t="ORPHAN check: if parent_name set but parent PID dead → kill orphan child" who="watchdog" tag="HRO" />
      <R n={21} t="ZOMBIE reap: os.waitpid(-1, WNOHANG) in loop — collect exit status of dead children" who="watchdog" tag="HRO" />
    </S>

    <S title="SHUTDOWN CASCADE (ordered teardown)" icon="🔌" color={C.red}>
      <R n={22} t="Shutdown order: Phi3 instances → subprocess CLIs → workers → dashboard → other" who="watchdog" tag="HRO" />
      <R n={23} t="Phase 1: SIGTERM to all processes in current group (graceful request)" who="watchdog" tag="HRO" />
      <R n={24} t="Phase 2: Wait up to 15s total for graceful shutdown" who="watchdog" tag="HRO" />
      <R n={25} t="Phase 3: SIGKILL anything still alive (force kill)" who="watchdog" tag="HRO" />
      <R n={26} t="Phase 4: Final zombie reap — collect all remaining dead children" who="watchdog" tag="HRO" />
      <R n={27} t="Phase 5: Clear registry, delete PID file from disk" who="watchdog" tag="HRO" />
      <R n={28} t="After shutdown: ps aux | grep python should show ZERO factory processes" who="watchdog" tag="HRO" />
    </S>
  </div>
);

// ═══ PAGE 12: DASHBOARD (26 rules) ═══

const Dashboard = () => (
  <div>
    <S title="SERVER SETUP (aiohttp — pure Python, no React)" icon="🖥️" color={C.sky}>
      <R n={1} t="aiohttp web server starts on 127.0.0.1:8420 during Watchdog boot" who="watchdog" tag="ENV" />
      <R n={2} t="Serves single-page HTML app (embedded in Python string — no build step)" who="watchdog" tag="ENV" />
      <R n={3} t="WebSocket endpoint at /ws — real-time bidirectional communication" who="watchdog" tag="HAL" />
      <R n={4} t="Dashboard has ReadOnlyDB access — can ONLY read from SQLite" who="watchdog" tag="TRAP" />
      <R n={5} t="All Dashboard writes go through message bus → Watchdog (same as every other component)" who="watchdog" tag="TRAP" />
    </S>

    <S title="REST API ENDPOINTS" icon="🔌" color={C.blue}>
      <R n={6} t="GET /api/status — full system status: workers, tasks, escalations, activity, roles" who="watchdog" tag="SER" />
      <R n={7} t="GET /api/tasks?status=X — task list filtered by status" who="watchdog" tag="SER" />
      <R n={8} t="GET /api/escalations — pending escalations (limit 20)" who="watchdog" tag="SER" />
      <R n={9} t="POST /api/escalation/{id}/resolve — Human resolves escalation (writes via bus)" who="watchdog" tag="TRAP" />
      <R n={10} t="GET /api/activity?limit=N — recent activity (checkpoints + escalations + quality gates)" who="watchdog" tag="SER" />
      <R n={11} t="GET /api/roles — current role→worker assignments + available workers" who="watchdog" tag="SER" />
      <R n={12} t="POST /api/roles/swap — hot-swap role assignment (body: { role, primary, fallback })" who="watchdog" tag="SER" />
      <R n={13} t="GET /api/workers/available — list of all initialized worker names" who="watchdog" tag="SER" />
    </S>

    <S title="WEBSOCKET REAL-TIME UPDATES" icon="📡" color={C.green}>
      <R n={14} t="Broadcast loop: every 2s (configurable), sends full status JSON to all connected WebSocket clients" who="watchdog" tag="TRAP" />
      <R n={15} t="Client sends commands via WebSocket: { action: 'resolve_escalation', escalation_id, decision }" who="watchdog" tag="TRAP" />
      <R n={16} t="Client sends role swap via WebSocket: { action: 'swap_role', role, primary, fallback }" who="watchdog" tag="TRAP" />
      <R n={17} t="Dead WebSocket connections automatically cleaned up from client set" who="watchdog" tag="HRO" />
      <R n={18} t="If WebSocket disconnects → frontend falls back to polling /api/status every 5s" who="watchdog" tag="HRO" />
    </S>

    <S title="UI SECTIONS" icon="📊" color={C.yellow}>
      <R n={19} t="Worker Status table: instance name, status dot (green/blue/yellow/red), current task, context %, tasks completed" who="watchdog" tag="TRAP" />
      <R n={20} t="Role Configuration panel: dropdown selects for primary/fallback per role, Apply button, hot-swap" who="watchdog" tag="SER" />
      <R n={21} t="Task Queue: horizontal bars showing pending/in_progress/testing/review/blocked/approved counts" who="watchdog" tag="TRAP" />
      <R n={22} t="Escalations: cards with type, reason, task_id, Approve/Dismiss buttons" who="watchdog" tag="TRAP" />
      <R n={23} t="Activity feed: timestamped log of checkpoints, escalations, quality gates" who="watchdog" tag="TRAP" />
      <R n={24} t="Connection indicator: top-right green 'Connected' / red 'Disconnected' badge" who="watchdog" tag="HRO" />
      <R n={25} t="DB Write notice: '🔒 DB: Watchdog-only writes' displayed at bottom of worker table" who="watchdog" tag="TRAP" />
      <R n={26} t="All styling: CSS-in-HTML, dark theme, monospace font, color-coded status indicators" who="watchdog" tag="ENV" />
    </S>
  </div>
);

// ═══ PAGE MAP ═══

const PAGES = {
  watchdog: WatchdogBoot,
  gatekeeper: Gatekeeper,
  orchestrator: Orchestrator,
  claude_tdd: ClaudeTDD,
  gemini: GeminiAuditor,
  deepseek: DeepSeekWorker,
  qwen: QwenWorker,
  phi3: Phi3Scribe,
  project_router: ProjectRouter,
  role_router: RoleRouterPage,
  reaper: ProcessReaper,
  dashboard: Dashboard,
};

// ═══ RULE COUNTS ═══
const RULE_COUNTS = {
  watchdog: 26, gatekeeper: 28, orchestrator: 32, claude_tdd: 34,
  gemini: 24, deepseek: 22, qwen: 20, phi3: 24,
  project_router: 26, role_router: 22, reaper: 28, dashboard: 26,
};
const TOTAL_RULES = Object.values(RULE_COUNTS).reduce((a, b) => a + b, 0);

// ═══ MAIN APP ═══

export default function WorkerDaC() {
  const [tab, setTab] = useState("watchdog");
  const Page = PAGES[tab];
  const active = TABS.find(t => t.id === tab);

  return (
    <div style={{ background: C.bg, minHeight: "100vh", fontFamily: "monospace", color: C.text }}>
      {/* Header */}
      <div style={{ textAlign: "center", padding: "12px 10px 6px", borderBottom: `1px solid ${C.border}` }}>
        <h1 style={{ fontSize: 14, color: C.cyan, letterSpacing: 2, margin: 0 }}>⚙ AUTONOMOUS FACTORY v1.1 — Worker Rules DaC</h1>
        <div style={{ fontSize: 9, color: C.dim, marginTop: 2 }}>{TOTAL_RULES} rules across 12 roles · WATCHDOG does → LLM thinks</div>
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", justifyContent: "center", gap: 4, padding: "8px 4px", flexWrap: "wrap", borderBottom: `1px solid ${C.border}` }}>
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} style={{
            padding: "5px 10px", border: `1.5px solid ${tab === t.id ? t.color : C.border}`,
            borderRadius: 5, background: tab === t.id ? t.color + "20" : C.panel,
            color: tab === t.id ? t.color : C.dim, cursor: "pointer", fontFamily: "monospace",
            fontSize: 9, fontWeight: tab === t.id ? "bold" : "normal", transition: "all 0.2s",
            position: "relative",
          }}>
            {t.icon} {t.label}
            <span style={{ fontSize: 7, color: tab === t.id ? t.color : C.dim, marginLeft: 3 }}>({RULE_COUNTS[t.id]})</span>
          </button>
        ))}
      </div>

      {/* Active tab info */}
      <div style={{ textAlign: "center", padding: "6px 10px 2px" }}>
        <span style={{ color: active.color, fontSize: 11, fontWeight: "bold" }}>{active.icon} {active.label}</span>
        <span style={{ color: C.dim, fontSize: 9, marginLeft: 6 }}>— {active.desc}</span>
      </div>

      {/* Legend */}
      <div style={{ display: "flex", justifyContent: "center", gap: 16, padding: "4px 10px 8px" }}>
        <span style={{ fontSize: 8, color: C.cyan, fontFamily: "monospace" }}>🟦 WATCHDOG = Python DOES it</span>
        <span style={{ fontSize: 8, color: C.purple, fontFamily: "monospace" }}>🟪 LLM = AI THINKS it</span>
        <span style={{ fontSize: 8, color: C.dim, fontFamily: "monospace" }}>Tags: TRAP · SER · DOM · HRO · HAL · ENV</span>
      </div>

      {/* Content */}
      <div style={{ padding: "2px 12px 16px", maxWidth: 880, margin: "0 auto" }}>
        <Page />
      </div>

      {/* Footer */}
      <div style={{ textAlign: "center", padding: 8, fontSize: 8, color: C.dim, borderTop: `1px solid ${C.border}` }}>
        Autonomous Factory v1.1 · {TOTAL_RULES} rules · 12 roles: Watchdog({RULE_COUNTS.watchdog}) · Kimi({RULE_COUNTS.gatekeeper}) · Orchestrator({RULE_COUNTS.orchestrator}) · Claude TDD({RULE_COUNTS.claude_tdd}) · Gemini({RULE_COUNTS.gemini}) · DeepSeek({RULE_COUNTS.deepseek}) · Qwen({RULE_COUNTS.qwen}) · Phi3({RULE_COUNTS.phi3}) · ProjectRouter({RULE_COUNTS.project_router}) · RoleRouter({RULE_COUNTS.role_router}) · Reaper({RULE_COUNTS.reaper}) · Dashboard({RULE_COUNTS.dashboard})
      </div>
    </div>
  );
}
