# Universal Blueprint Protocol (Distilled)

Worker instruction document for blueprint generation. Execute phases 0-4 sequentially.
Human input is required at Phase 2 and Phase 4. Do not skip phases.

---

## Phase 0: Domain Detection

Classify the project before generating any questions.

### Domain Classification Matrix

| Keyword Signals | Domain | Base Complexity |
|:---|:---|:---|
| REST, GraphQL, SPA, SSR, auth, dashboard | Web Application | Medium |
| endpoints, microservices, rate-limit, webhook | API / Backend | Medium |
| sensor, actuator, MQTT, firmware, GPIO, edge | IoT / Embedded | High |
| inventory, ERP, compliance, workflow, audit | Enterprise / PLM | High |
| camera, CV, model, inference, training, GPU | ML / AI Pipeline | High |
| iOS, Android, push, offline-first, BLE | Mobile | Medium |
| CI/CD, containers, k8s, terraform, monitoring | DevOps / Infra | Medium |
| e-commerce, payments, cart, catalog, shipping | Commerce | Medium-High |

Rules:
- If signals span 2+ domains, classify as Hybrid and note all domains.
- Complexity upgrades one tier if: real-time requirements, regulatory compliance, or hardware integration detected.
- Record classification as metadata in the blueprint header.

---

## Phase 1: MCQ Generation

Generate multiple-choice questions across 6 mandatory categories. Every question must include an AI recommendation with evidence-based reasoning.

### Web Research Protocol (execute before writing MCQs)

Run 15-25 search queries in 4 sequential sets:

| Set | Purpose | Query Count | Example Pattern |
|:---|:---|:---|:---|
| 1. Fundamentals | Core tech docs, official guides | 3-5 | "[domain] [framework] production architecture 2025" |
| 2. Benchmarks | Performance data, comparisons | 4-6 | "[tech A] vs [tech B] benchmark latency throughput" |
| 3. Failures | Post-mortems, known pitfalls | 4-7 | "[tech] production failure post-mortem scaling issues" |
| 4. Integration | Compatibility, migration paths | 4-7 | "[tech A] [tech B] integration gotchas migration guide" |

Rules:
- Cite sources when making recommendations.
- Prefer data from the last 18 months.
- Flag any technology with fewer than 3 reliable benchmark sources as "unverified".

### MCQ Categories

**Category A: Core Problem (2-4 questions)**
Define what the system must solve. Focus on primary use case, target users, and success metrics.
- At least 1 question must define a measurable success metric (latency, uptime, throughput).

**Category B: Scope Boundary (3-5 questions)**
Define what is IN and OUT of scope.
- Minimum 10 explicit OUT-OF-SCOPE items must be listed across this category.
- Each out-of-scope item needs a one-line justification.

**Category C: Technology Selection (3-6 questions)**
Language, framework, database, infrastructure, third-party services.
- Every tech-selection question MUST include benchmark data (latency, memory, cost).
- Options must include at least one conservative/proven choice and one modern alternative.

**Category D: Security (2-3 questions)**
Authentication, authorization, data protection, compliance.
- Must address: auth method, data encryption (at-rest + in-transit), secrets management.

**Category E: UX and Testing (2-3 questions)**
User experience priorities, testing strategy, accessibility.
- Must define: test coverage target, E2E scope, performance budget.

**Category F: Operations (2-3 questions)**
Deployment, monitoring, scaling, disaster recovery.
- Must address: deployment strategy, rollback plan, alerting thresholds.

### MCQ Format (required for every question)

```markdown
### [Category Letter][Number]: [Question Title]

**Question:** [Clear, specific question]

| Option | Description | Trade-off |
|:---|:---|:---|
| A | [Option A] | [Pro/con summary] |
| B | [Option B] | [Pro/con summary] |
| C | [Option C] | [Pro/con summary] |

**AI Recommendation:** [Option X]
**Reasoning:** [2-3 sentences with data or citations. No vague claims.]
```

---

## Phase 2: Human Input [HUMAN OVERRIDE POINT]

Present all MCQs to the human. Collect responses for every question.

Rules:
- Human may override any AI recommendation. Record overrides with rationale.
- Human may add free-text requirements not covered by MCQs.
- If human selects an option the AI flagged as risky, log a warning in the blueprint but proceed.
- Do not proceed to Phase 3 until all MCQs have responses.

---

## Phase 3: Blueprint Synthesis

Generate the structured blueprint from human-confirmed answers and research data.

### Gap Detection Checkpoint (mandatory before writing blueprint)

Review all answers and identify gaps. You must find a minimum of 5 gaps before proceeding.

Gap categories:
- Missing error handling strategy
- Undefined data migration path
- No rollback procedure specified
- Unclear ownership boundaries (who maintains what)
- Missing rate limits or resource caps
- No offline/degraded-mode behavior defined
- Unaddressed regulatory or compliance requirements
- Missing monitoring or observability plan
- Undefined inter-service communication contracts
- No capacity planning or scaling triggers

For each gap: present to human with a recommended default. Human confirms or overrides.

### Blueprint Output Format

```markdown
# Blueprint: [Project Name]

## Metadata
- Domain: [from Phase 0]
- Complexity: [tier]
- Generated: [timestamp]
- Human overrides: [count]

## 1. Problem Statement
[From Category A answers. 3-5 sentences max.]

## 2. Success Metrics
[Measurable targets with numbers. Table format.]

## 3. Scope
### In Scope
[Bulleted list]
### Out of Scope
[Minimum 10 items with justifications]

## 4. Architecture
### System Overview
[High-level component diagram description]
### Technology Stack
[Table: component, technology, justification, benchmark reference]
### Data Model
[Entity list with relationships. Schema-level detail.]
### API Contracts
[Endpoint list with method, path, request/response shape]

## 5. Security Plan
[Auth, encryption, secrets, compliance items]

## 6. Testing Strategy
[Coverage targets, E2E scope, performance budgets]

## 7. Operations
### Deployment
[Strategy, environments, rollback plan]
### Monitoring
[Metrics, alerting thresholds, dashboards]
### Scaling
[Triggers, capacity plan, cost projections]

## 8. Timeline
[Phase breakdown with deliverables and milestones. Realistic estimates.]

## 9. Risks and Mitigations
[Table: risk, probability, impact, mitigation]

## 10. Gaps Addressed
[From gap detection checkpoint. What was missing and what default was applied.]
```

---

## Phase 4: Validation [HUMAN OVERRIDE POINT]

Present the complete blueprint to the human for approval.

Validation checklist (worker self-check before presenting):
- [ ] All MCQ answers are reflected in the blueprint
- [ ] Out-of-scope list has 10+ items
- [ ] Every tech choice has benchmark backing
- [ ] Success metrics are numeric and measurable
- [ ] Security section covers auth, encryption, and secrets
- [ ] Timeline estimates are justified (not optimistic defaults)
- [ ] All gaps from checkpoint are addressed
- [ ] No section is placeholder or TBD

Human may:
- Approve as-is (proceed to contract generation)
- Request revisions (return to relevant phase)
- Reject (restart from Phase 1 with new constraints)

Record final approval status and any revision notes in blueprint metadata.
