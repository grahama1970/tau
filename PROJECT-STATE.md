# tau Project State -- 2026-08-30 (standard mode, generic profile)

## Phase 1: Infrastructure

### Daemons: not applicable (target root is not an Embry-style project)

### Tests: 3702 collected

### 3-Tier Cascade: not applicable (target root is not an Embry-style project)

### Cascade Wiring: not applicable (target root is not an Embry-style project)

### Skills: 1 total
  - 1 dirs without SKILL.md

### Deploy: 0 systemd units

## Phase 2: Memory Recall

- **tau features architecture deployment**: FOUND (conf=0.80, 5 items)
  - project knowledge chunk for tau: Current Understanding (10)
  - project knowledge chunk for tau: Current Understanding (702)
  - project knowledge chunk for tau: Current Understanding (690)
- **tau competitive advantages unique capabilities**: FOUND (conf=0.80, 5 items)
  - project knowledge chunk for tau: Current Understanding (463)
  - project knowledge chunk for tau: Current Understanding (453)
  - project knowledge chunk for tau: Current Understanding (465)
- **tau known issues gaps missing features**: FOUND (conf=0.80, 5 items)
  - project knowledge chunk for tau: Current Understanding (690)
  - project knowledge chunk for tau: Current Understanding (691)

## Phase 3: Doc-Code Drift (51 items)

- **aspirational_will** (3x): provider subprocesses will complete within 900s.
- **future** (6x): compliance, legal sufficiency, model safety, future route correctness, or that
- **not_yet** (2x): - Command-loop terminal GitHub transport now supports `target: "new"` for UI-ori
- **planned** (4x): In the planned GitHub-backed deployment, the same state machine can be driven by
- **stale_reference** (35x): References `project_dag.py` but file not found
- **todo** (1x): - 2026-08-13 project-state snapshot for Tau is /tmp/tau-project-state-20260813.j

## Phase 4: Best Practices (18 findings)
  Critical=11 High=0 Medium=0 Low=7

- hardcoded_secret: 11x
- hardcoded_home_path: 4x

## Phase 5: External Research (skipped -- quick mode)

## Phase 6: Gap Analysis (2 gaps)

1. **[CRITICAL]** 11 critical best-practice violations (possible hardcoded secrets)
   Action: Run /security-scan and fix immediately
2. **[LOW]** 3 aspirational/TODO items in docs
   Action: Implement or remove aspirational claims

## External Research Addendum

Brave Search was run separately after this standard `/project-state` report.

### Queries

- `Tau coding agent DAG ledger project status GitHub`
- `AI coding agent evals receipts DAG workflow audit trail`
- `terminal coding agent benchmark sessions MCP Textual TUI`

### Useful signals

- Search results surfaced other Tau/coding-agent projects and a CLI coding-agent landscape page. The external landscape emphasizes provider-neutral cores, durable sessions, skills/MCP-style extensions, TUI surfaces, and benchmark/eval framing.
- Governance/audit search results emphasized action logs, traceability, human-in-the-loop approvals, approval gates, audit trails, and evaluated agent workflows.
- These results support keeping Tau's next work focused on receipt binding, source-derived coverage, ledger audit projections, visible run correlation, provider-live proof, and explicit human acceptance rather than broad feature growth.

## Focused GitHub Ticket Plan

### Existing Tau tickets to prioritize

1. `grahama1970/tau#329` — bind retained Tau agentic-eval evidence to pushed revision.
   - Why now: external governance results stress traceability and audit trails; Tau's proof artifacts need a stable evidence-to-SHA index.
2. `grahama1970/tau#330` — derive Tau feature inventory from source and reconcile agentic eval claims.
   - Why now: project-state says Tau has strong memory/proof history, but inventory exhaustiveness remains unproven.
3. `grahama1970/tau#333` — compliance-officer ledger audit projection and independent verification.
   - Why now: external results emphasize independent audit trails and approval gates.
4. `grahama1970/tau#332` — correlate live React Flow DAG transitions with Tau ledger events.
   - Why now: visible UI state should be tied to ledger events, not trusted as a separate success signal.
5. `grahama1970/tau#304` and `grahama1970/tau#305` — provider-live proof and human acceptance remain the closure gates for the larger Tau goal.

### New tickets filed from this report

- `grahama1970/tau#334` — triage project-state hardcoded-secret findings in Tau.
- `grahama1970/tau#335` — prune stale Tau project-knowledge references found by project-state.
- `grahama1970/agent-skills#1551` — teach `/project-state` to emit this one-command current-state packet automatically.

## Automation Recommendation

Yes: `/project-state` should grow an explicit current-state packet mode so the manual chain does not have to be typed every time. The mode should produce:

- `PROJECT-STATE.md`
- machine JSON report
- `/project-knowledge` update/sync evidence
- Brave/GitHub research receipts
- focused ticket recommendations
- an explicit non-claims section

It should not auto-file tickets unless invoked with an explicit apply flag.

