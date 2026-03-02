# Blueprint Quality Audit Protocol (Distilled)

Audit checklist for evaluating generated blueprints. Score each section, compute the weighted total, and apply disqualifiers.

---

## Scoring Formula

```
Final Score = (Practicality x 0.35) + (Completeness x 0.25) + (Clarity x 0.20) + (Feasibility x 0.15) + (Innovation x 0.05)
```

Each section scored 0-100. Final score determines grade:

| Score | Grade | Action |
|:---|:---|:---|
| 90-100 | Production-Grade | Approve. Proceed to contract generation. |
| 75-89 | Strong | Approve with minor revisions noted. |
| 60-74 | Adequate | Revise flagged sections before proceeding. |
| 45-59 | Weak | Return to blueprint generation. Major gaps. |
| 0-44 | Reject | Restart from Phase 1. Fundamental problems. |

---

## Core Philosophy: Sports Car, Not Spaceship

A good blueprint builds a sports car: fast, focused, achievable with known technology and realistic resources. A bad blueprint builds a spaceship: over-engineered, speculative, dependent on unproven tech or unlimited budget. When in doubt, penalize ambition without evidence and reward pragmatic constraint.

---

## Section 1: Practicality (weight: 0.35)

Score based on five sub-criteria. Average them for the section score.

**1a. Technology Realism**
- Are all chosen technologies production-proven for this use case?
- Are benchmarks cited for performance-critical choices?
- Red flag: Choosing bleeding-edge tech without fallback plan.
- Green flag: Conservative stack with clear upgrade path.

**1b. Timeline Feasibility**
- Do estimates account for integration, testing, and deployment (not just coding)?
- Is there buffer for unknowns (minimum 15-20% of total timeline)?
- Red flag: "MVP in 2 weeks" for a system with 5+ integrations.
- Green flag: Phased delivery with checkpoints and go/no-go gates.

**1c. Scope Discipline**
- Are there 10+ explicit out-of-scope items with justifications?
- Is the scope achievable by the stated team in the stated timeline?
- Red flag: Scope creep disguised as "nice-to-have" features in the core plan.
- Green flag: Ruthless prioritization with a clear "Phase 2 backlog".

**1d. Cost Realism**
- Are infrastructure costs estimated with specific tiers/SKUs?
- Are third-party service costs included (APIs, SaaS, licenses)?
- Red flag: No cost section or "costs TBD".
- Green flag: Monthly burn rate estimate with scaling projections.

**1e. Team Capability Match**
- Does the tech stack match the team's known skills?
- Is ramp-up time accounted for if new tech is introduced?
- Red flag: Choosing a stack nobody on the team has shipped with.
- Green flag: Stack aligns with team experience; new tech limited to one component.

---

## Section 2: Completeness (weight: 0.25)

**2a. Critical Sections Present**
All of the following must exist and be non-empty:
- Problem statement, success metrics, scope (in + out), architecture, data model, API contracts, security plan, testing strategy, deployment plan, monitoring plan, timeline, risks.
- Missing any critical section: cap this sub-score at 40.

**2b. Domain Intelligence**
- Does the blueprint reflect research specific to the domain?
- Are known failure modes for this domain addressed?
- Red flag: Generic architecture that could apply to any project.
- Green flag: Domain-specific constraints acknowledged (e.g., HIPAA for health, PCI for payments, latency budgets for real-time).

**2c. Edge Cases and Error Handling**
- Are failure modes defined for each integration point?
- Is there a degraded-mode strategy (what works when dependencies fail)?
- Are rate limits, timeouts, and retry policies specified?
- Red flag: Only happy-path described.
- Green flag: Explicit error taxonomy with recovery actions.

---

## Section 3: Clarity (weight: 0.20)

**3a. Decision Clarity**
- Is every technology choice justified with a reason (not just listed)?
- Are trade-offs stated for significant decisions?
- Red flag: "We will use PostgreSQL" with no reasoning.
- Green flag: "PostgreSQL chosen over MongoDB because [relational integrity needed for X; benchmark shows Y]."

**3b. API Contract Specificity**
- Are endpoints defined with method, path, request shape, and response shape?
- Are error responses documented?
- Red flag: "API will expose CRUD endpoints" with no detail.
- Green flag: Full endpoint table with payload schemas.

**3c. Data Model Precision**
- Are entities, relationships, and key fields defined?
- Are indexes, constraints, and migration strategy mentioned?
- Red flag: Entity list with no relationships or field types.
- Green flag: Schema-level detail with cardinality and constraints.

---

## Section 4: Feasibility (weight: 0.15)

**4a. Resource Constraints Acknowledged**
- Are compute, memory, storage, and bandwidth limits stated?
- Are scaling triggers defined (at what load do we scale, and how)?
- Red flag: "Will scale as needed" with no specifics.
- Green flag: "At 1000 RPS, add read replica; at 5000 RPS, shard by tenant ID."

**4b. Dependency Risk**
- Are external dependencies listed with risk assessment?
- Is there a fallback for any single-vendor dependency?
- Red flag: Critical path depends on a single beta-stage API.
- Green flag: Dependency matrix with alternatives identified.

**4c. Technical Debt Acknowledgment**
- Are known shortcuts or compromises documented?
- Is there a plan to address them post-launch?
- Red flag: No mention of trade-offs or debt.
- Green flag: Explicit debt register with priority and timeline.

---

## Section 5: Innovation Appropriateness (weight: 0.05)

- Is innovation applied where it creates measurable value?
- Is novelty avoided where proven solutions exist?
- Red flag: Custom-built auth system when OAuth/OIDC fits.
- Green flag: Novel approach to a domain-specific problem with clear justification.

---

## Section 6: Spaceship Detection (Automatic Disqualifiers)

If ANY of the following are true, cap the final score at 60 (maximum grade: Adequate). The blueprint must be revised before it can score higher.

| Disqualifier | Test |
|:---|:---|
| Unbounded scope | Fewer than 10 out-of-scope items, or no scope boundary section. |
| Fantasy timeline | Estimated delivery is less than 50% of comparable industry benchmarks. |
| Unproven core dependency | A critical-path component uses technology with no production track record. |
| Missing security section | No auth, encryption, or secrets management defined. |
| No error handling | Only happy-path flows described; no failure modes or recovery. |
| Infinite scaling assumption | "Will handle any load" or no capacity limits stated. |
| Zero cost analysis | No infrastructure or operational cost estimates. |
| No testing strategy | No test coverage targets, no E2E plan, no performance budgets. |

---

## Audit Output Format

```markdown
# Audit Report: [Blueprint Name]

## Scores
| Section | Score | Notes |
|:---|:---|:---|
| Practicality (x0.35) | [0-100] | [Key strengths/weaknesses] |
| Completeness (x0.25) | [0-100] | [Key strengths/weaknesses] |
| Clarity (x0.20) | [0-100] | [Key strengths/weaknesses] |
| Feasibility (x0.15) | [0-100] | [Key strengths/weaknesses] |
| Innovation (x0.05) | [0-100] | [Key strengths/weaknesses] |

**Weighted Total:** [score]
**Grade:** [Production-Grade / Strong / Adequate / Weak / Reject]

## Spaceship Disqualifiers
- [List any triggered disqualifiers, or "None"]
- [If triggered: "Score capped at 60"]

## Red Flags
- [Bulleted list of all red flags identified]

## Green Flags
- [Bulleted list of all green flags identified]

## Required Revisions
- [Specific, actionable items that must be fixed before approval]

## Recommendations
- [Optional improvements that would raise the score]
```
