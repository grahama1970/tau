# Handoff Report: Tau

**Timestamp**: 2026-07-27T19:12:45Z
**Active Agent**: Codex

## 1. Project Overview

- **Ecosystem**: Python package managed with `uv`; source under `src/`; tests under `tests/`; CLI entrypoint is `tau = tau_coding.cli:app`.
- **Core Purpose**: Tau is a memory-first, zero-trust containment harness for untrusted agent work. It coordinates policy/data-boundary gates, DAG contracts, typed receipts, evidence validators, bounded subagent dispatch, SciLLM/provider checks, Herdr/monitor integration, and human approval boundaries.
- **Current Immutable Product Goal**: `GOAL.md` says Tau must let a human choose and run five canonical real DAGs, from simple linear to durable mixed-topology recovery, with a shared dynamic React Flow progress viewer that shows truthful progress, accepted evidence, blockers, and required human decisions.

## 2. Current State (Doc-Code Alignment)

- **Documented Features**:
  - README frames Tau as the zero-trust control plane for Embry-OS and Sparta Explorer.
  - Current README explicitly says the five canonical DAG ladder and shared dynamic viewer are active goals, not complete proof.
  - README documents a Textual TUI, Memory-first routing, SciLLM/provider containment, typed receipts, Herdr-visible provider lanes, browser/CDP proof lanes, DAG viewer contracts, coding-evidence receipts, secure execution, resource leases, adaptive DAG revision, sprite conformance, project profile authority, audit/signing/RBAC, and `$tau` runtime handshake.
  - `pyproject.toml` still describes Tau narrowly as "A Python implementation of a minimalist Pi-style coding-agent harness."
- **Implemented Reality**:
  - Source tree has the intended layers: `tau_ai`, `tau_agent`, and a large `tau_coding` control plane.
  - Current source inventory found 165 Python source files under `src/tau_ai`, `src/tau_agent`, and `src/tau_coding`.
  - Current test inventory found 127 `tests/test_*.py` files.
  - Recent commits are focused on Pi-style TUI parity and session controls: command cancellation, overlapping terminal-command guards, relevance-sort session picker, Pi-style session picker search, hotkeys, key aliases, bindable session actions, and project trust for local resources.
- **Drift/Misalignments**:
  - `CONTEXT.md` and `0N_TASKS.md` are missing, despite the handoff skill expecting them when present.
  - `.pi/skills/handoff/run.sh` is missing or not executable in this checkout, so this handoff was produced by manual assessment rather than the automated handoff helper.
  - `pyproject.toml` understates the project compared with README and current code.
  - The local checkout is not clean and is on `issue-117-generated-ticket-dedupe`, not `main`.
  - README says the DAG React Flow viewer is an integration/inspection surface, while `GOAL.md` requires a shared dynamic progress viewer driven by authoritative live run state. Treat that as an active product gap unless a fresh receipt proves otherwise.

## 3. What is Working Well

- The core package structure is coherent: provider/model code in `tau_ai`, reusable agent/session primitives in `tau_agent`, and CLI/TUI/resources/harness behavior in `tau_coding`.
- Tau has substantial receipt and policy surfaces already present in code and docs: secure executor, resource leases, project profile/spine, worker controlled-data conformance, receipt signing, runtime handshake, DAG runtime scheduler, run store/replay, TUI app, session manager, and command registry.
- The full deterministic test suite mostly runs: `timeout 240 uv run pytest -q` reached completion with `1937 passed, 1 skipped, 3 failed in 133.97s`.
- GitHub issue state was recently reconciled: #186-#195, the #72 child tranche for canonical scheduler/security/resource/adaptive/sprite/profile/controlled-data/audit/runtime-handshake criteria, are closed with retained proof artifacts on main.

## 4. What is Currently Broken

- **Failed Tests**:
  - Command: `timeout 240 uv run pytest -q`
  - Result: `3 failed, 1937 passed, 1 skipped in 133.97s`
  - Skip: `tests/test_persona_dream_text_reasoning_agent.py:104` requires `TAU_TEXT_REASONING_LIVE=1`.
  - Failures:
    - `tests/test_tui_autocomplete.py::test_command_completion_suggests_registered_commands`
      - Expected `["/session"]`; actual `["/session", "/settings"]`.
    - `tests/test_tui_autocomplete.py::test_command_completion_matches_search_terms_with_canonical_replacement`
      - Expected `["/new"]`; actual `["/clone", "/copy", "/new"]` for `/cl`.
    - `tests/test_tui_autocomplete.py::test_command_completion_prioritizes_direct_matches_over_search_terms`
      - Expected first two `["/resume", "/new"]`; actual `["/resume", "/import"]`.
  - Diagnosis: this looks like TUI autocomplete expectation drift after Pi-style commands/settings/import/clone/copy were added. The next agent should decide whether the new suggestions are correct and update tests, or adjust ranking/filtering if the intended UX is narrower.
- **Known Issues**:
  - Open GitHub issues from live readback:
    - `#72` OPEN, labels `type:feature`, `maintainer-blocked`, `route:backend_python_or_skill_runtime`: program epic for durable, secure, adaptive Tau DAG runtime hardening.
    - `#180` OPEN, labels `type:feature`, `maintainer-blocked`, `dag`, `route:ops_or_scheduler`, `ease-of-use`, `memory`, `viewer`: productize Tau DAG templates and authoritative viewer UX.
  - `needs-human` was removed from #72 and #180 on 2026-07-27; both remain `maintainer-blocked`.
  - The repo has a very large untracked proof and experiment corpus. Do not clean, reset, or broadly stage it.
  - `rg TODO/FIXME` found intentional placeholder/stub language in `src/tau_coding/provider_dag_poc.py` and `src/tau_coding/media_explainer_orchestration.py`; review before treating these as bugs because some are fixture/demo contracts.
- **Recent Regressions / Risk Areas**:
  - TUI autocomplete is currently red and directly connected to recent Pi-parity session/control changes.
  - Current branch `issue-117-generated-ticket-dedupe` contains pre-existing modifications to `PROJECT_KNOWLEDGE.md`, `README.md`, `src/tau_coding/battle_live_handoff.py`, and `src/tau_coding/battle_scillm.py`.
  - Large untracked docs/proof paths include `docs/proofs/`, `docs/review-bundles/`, `docs/herdr-inspired-orchestration-requirements.md`, `docs/tau-planner-orchestrator-visible-proof-plan.md`, `docs/traycer-ideas-for-tau-requirements.md`, many `experiments/goal-locked-subagents/...` paths, `local-archives/`, and `run/`.

## 5. Next Steps

1. Fix the three failing TUI autocomplete tests or the autocomplete ranking behavior:
   - inspect `src/tau_coding/tui/autocomplete.py`, `src/tau_coding/commands.py`, and `tests/test_tui_autocomplete.py`;
   - preserve the new Pi-parity commands unless they are demonstrably wrong;
   - run `uv run pytest -q tests/test_tui_autocomplete.py`.
2. Re-run the full baseline after the focused fix: `timeout 240 uv run pytest -q`.
3. For #72 and #180, because `needs-human` is removed but `maintainer-blocked` remains, perform a ticket-runtime readback before any closure attempt. Do not close either epic unless the parent acceptance criteria and retained proof artifacts satisfy the issue body.
4. Continue Tau/Pi TUI parity work only after the red autocomplete baseline is addressed. The user explicitly wants Pi parity but also explicitly does not want Tau-specific features overwritten.
5. Keep the immutable goal centered: five canonical real DAGs plus a shared dynamic React Flow progress viewer. Do not treat issue closure, static receipts, or unit tests as product-goal completion.

## 6. Project Context for Success

- **Key Files**:
  - `GOAL.md` - active immutable product goal and completion criteria.
  - `README.md` - current positioning, non-claims, and feature surface.
  - `pyproject.toml` - package metadata, dependencies, Python `>=3.14`.
  - `src/tau_coding/cli.py` - main CLI.
  - `src/tau_coding/commands.py` - slash command registry and command semantics.
  - `src/tau_coding/tui/app.py` - Textual TUI application.
  - `src/tau_coding/tui/autocomplete.py` - current failing autocomplete behavior.
  - `tests/test_tui_autocomplete.py` - current red test file.
  - `src/tau_coding/dag_runtime/` - scheduler/runtime internals.
  - `src/tau_coding/secure_executor.py`, `src/tau_coding/resource_lease.py`, `src/tau_coding/project_profile.py`, `src/tau_coding/runtime_handshake.py` - recent #72 hardening surfaces.
  - `docs/proofs/tickets/issue-186-*` through `docs/proofs/tickets/issue-195-*` - retained proof artifacts for the #72 child tranche.
- **Recent Changes**:
  - `b00cf5b0 Cancel terminal commands through session signal`
  - `8a63db3a Guard overlapping terminal commands in TUI`
  - `e7f87744 Add relevance sort to session picker`
  - `1336ef1e Add Pi-style session picker search`
  - `4b76135b Update hotkeys for Pi session controls`
  - `eecbc003 Add Pi session picker key aliases`
  - `c0bb25d3 Add Pi-style bindable session actions`
  - `5b4ee601 Enforce project trust for local resources`
- **Git State Notes**:
  - Current branch: `issue-117-generated-ticket-dedupe`.
  - Current HEAD: `b00cf5b0e3ff58e253ce9346b8ebbf7d3e3d867d`.
  - Worktree is dirty before this handoff. Preserve unrelated changes.
  - This handoff intentionally updates only `local/HANDOFF.md`.

## 7. Evidence

- **mocked**: no
- **live**: yes for local filesystem/git/test/GitHub readback; no provider-live or browser-live lane was exercised.
- **Actually exercised**:
  - Read the installed `handoff` skill at `/home/graham/workspace/experiments/agent-skills/skills/handoff/SKILL.md`.
  - Attempted the skill helper: `.pi/skills/handoff/run.sh` was missing or not executable.
  - Read `README.md`, `GOAL.md`, `pyproject.toml`, existing `local/HANDOFF.md`, source/test inventory, TODO markers, recent commits, and focused TUI autocomplete snippets.
  - Ran `timeout 240 uv run pytest -q`; result was `3 failed, 1937 passed, 1 skipped in 133.97s`.
  - Read open GitHub issue state for `grahama1970/tau`: #72 and #180 are open and `maintainer-blocked`, with `needs-human` removed.
- **What remains unverified**:
  - No live provider/SciLLM, Herdr, Memory, browser/CDP, or dynamic React Flow viewer proof was run during this handoff.
  - Passing tests do not prove semantic correctness, model quality, legal/compliance sufficiency, or completion of `GOAL.md`.
  - The dirty worktree means this handoff is a state snapshot, not a clean-release assertion.
