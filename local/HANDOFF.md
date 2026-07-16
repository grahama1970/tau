# Handoff Report: Tau

**Timestamp**: 2026-07-16T20:45:02Z
**Active Agent**: Codex

## 1. Project Overview

- **Ecosystem**: Python package managed with `uv`; source lives under `src/`; tests under `tests/`.
- **Core Purpose**: Tau is an experimental zero-trust containment harness for agent work. The current README frames it as the control plane for Embry-OS and Sparta Explorer, with policy/data-boundary gates, DAG contracts, typed receipts, evidence validators, bounded subagent dispatch, provider checks, and human approval gates.

## 2. Current State (Doc-Code Alignment)

- **Documented Features**:
  - Package layers: `tau_ai`, `tau_agent`, and `tau_coding`.
  - Roadmap says phases 0 through 20.4, 22, and 23 are implemented and documented; phase 21 extensions are deferred.
  - README lists zero-trust gates, evidence manifests, coding receipts, compliance package validation, command-spec policy, Herdr/provider lanes, persistent subagent surfaces, GitHub apply policy, browser/CDP proofs, proof index, route-memory signals, adaptive DAG expansion, memory/evidence-case gates, and `tau run`.
- **Implemented Reality**:
  - The source tree contains the expected core layers plus a large `tau_coding` surface implementing many receipt, DAG, policy, provider, Herdr, compliance, proof-index, run-status, TUI, and adapter modules.
  - The test suite is broad: the latest run executed 1834 tests total.
- **Drift/Misalignments**:
  - README positioning is now much broader than `pyproject.toml` description, which still says "A Python implementation of a minimalist Pi-style coding-agent harness."
  - Compliance package CLI behavior and its test fixture are misaligned: the test uses a minimal policy/data-boundary payload, while current validation requires additional fields.
  - The repo has a very large untracked `experiments/` proof tree and several untracked docs outside it; a new agent should avoid treating untracked files as disposable.

## 3. What is Working Well

- Core layering remains visible in the source tree: `tau_ai` for provider streaming, `tau_agent` for reusable harness/session primitives, and `tau_coding` for CLI/resources/tools/UI.
- Full deterministic pytest run nearly passes: `1833 passed, 1 failed in 85.05s`.
- The project has extensive documentation under `docs/architecture/` and focused topic docs for zero-trust policy, provider lifecycle, compliance packaging, run reports, proof index, Herdr cleanup, and related surfaces.

## 4. What is Currently Broken

- **Failed Tests**:
  - Command: `timeout 180 uv run pytest -q`
  - Result: `1 failed, 1833 passed in 85.05s`
  - Failing test: `tests/test_cli.py::test_cli_compliance_package_writes_review_bundle`
  - Failure: `tau compliance-package <run-dir> --out <package-dir>` exits with code `1` for the test fixture instead of `0`.
  - Reproduced CLI output reports `status: BLOCKED` and errors including:
    - `invalid_policy_profile: requires_data_boundary must be a boolean`
    - `invalid_policy_profile: network/providers/research/memory/github/filesystem must be objects`
    - `invalid_data_boundary: external_provider_allowed/external_research_allowed/public_repo_allowed must be booleans`
    - `invalid_data_boundary: foreign_person_access must be one of ['allowed', 'restricted', 'prohibited']`
- **Known Issues**:
  - TODO scan found one intentional fixture-style TODO in `src/tau_coding/provider_dag_poc.py` where a generated target file starts with `TODO: replace this line with a completed implementation.`
  - There is no project-local `.pi/skills/handoff/run.sh`; this report was produced by manual handoff assessment using the skill contract.
- **Recent Regressions**:
  - Recent commits are centered on ready-queue condition blocking, Battle context/reflection, WebGPT recovery, and DAG incident preservation. The current failing compliance-package fixture likely relates to stricter policy/data-boundary validation rather than those commit subjects directly.

## 5. Next Steps

1. Fix `tests/test_cli.py::test_cli_compliance_package_writes_review_bundle` by aligning the fixture with the current policy/data-boundary schema, or by intentionally accepting minimal policy/data-boundary inputs in the compliance package command. The safer first check is to inspect current schema defaults in `src/tau_coding/policy_profile.py`, `src/tau_coding/itar_boundary.py`, and `src/tau_coding/compliance_package.py`.
2. Re-run `uv run pytest tests/test_cli.py::test_cli_compliance_package_writes_review_bundle -q`, then the full `uv run pytest -q`.
3. Reconcile docs metadata: update `pyproject.toml` description or README positioning so the package summary does not understate the current zero-trust control-plane scope.
4. Inventory and classify untracked project docs before committing anything: `docs/herdr-inspired-orchestration-requirements.md`, `docs/review-bundles/tau-herdr-provider-pane-poc-webgpt-review.md`, `docs/tau-planner-orchestrator-visible-proof-plan.md`, and `docs/traycer-ideas-for-tau-requirements.md`.

## 6. Project Context for Success

- **Key Files**:
  - `README.md`
  - `pyproject.toml`
  - `docs/00-roadmap.md`
  - `docs/01-architecture.md`
  - `src/tau_agent/harness.py`
  - `src/tau_agent/loop.py`
  - `src/tau_ai/provider.py`
  - `src/tau_coding/cli.py`
  - `src/tau_coding/compliance_package.py`
  - `src/tau_coding/policy_profile.py`
  - `src/tau_coding/itar_boundary.py`
  - `tests/test_cli.py`
- **Recent Changes**:
  - `a3e316a7 tau: isolate Battle team-specific context`
  - `c33db84d docs(knowledge): record issue 74 acceptance proof`
  - `30219a27 fix(dag): reject malformed ready-queue conditions`
  - `d04b6392 fix(dag): block unsupported ready-queue conditions`
  - `84c06e63 docs: record bounded WebGPT clarification proof`
- **Git State Notes**:
  - Current branch: `issue-74-ready-queue-condition-block`, ahead of `grahama1970/issue-74-ready-queue-condition-block` by 1 commit.
  - Visible untracked docs outside `experiments/` and `local-archives/`: four docs/review-bundle paths listed in Next Steps.
  - There are many untracked files under `experiments/goal-locked-subagents/proofs/`; do not clean or reset them without explicit human direction.

## 7. Evidence

- **mocked**: no
- **live**: no
- **Actually exercised**:
  - Read the current `handoff` skill contract.
  - Checked the memory-first recall hook output; it was unrelated to Tau and was not used as evidence.
  - Read `README.md`, `pyproject.toml`, `docs/00-roadmap.md`, `docs/01-architecture.md`, source/test file inventory, recent git commits, and TODO markers.
  - Ran `timeout 180 uv run pytest -q`.
  - Reproduced the failing compliance-package CLI path with `uv run python` and `typer.testing.CliRunner`.
- **What remains unverified**:
  - No live provider, Herdr, browser/CDP, GitHub, Memory, or external service lane was exercised.
  - The passing tests do not prove semantic correctness, legal/compliance sufficiency, provider/model quality, or full production readiness.
