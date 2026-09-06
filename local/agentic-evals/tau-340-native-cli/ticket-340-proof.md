# tau#340 closure proof — commit 5f7d3de10 on main

## Fix
- `src/tau_coding/dag_runtime/native_agent_dispatch.py` (new): preflight (node shape, native read-only tool allowlist `read/ls/grep/find`, path policy, `tau.agent_requirement.v1` → live SciLLM discovery → `select_transport_profile` → validated `tau.transport_profile_selection_receipt.v1`, pre-dispatch cancel) before any provider/tool; then `execute_tau_agent_node` with `ScillmTransportProvider` and Tau's own `AgentTool`s.
- `generic_dag.execute_plan_node`: `adapter_kind == tau_native_agent_loop` routes to `_run_native_agent_node` with durable run store + active lease + compiled `plan_sha256`; node receipt `tau.generic_dag_node_receipt.v1` carries settlement, transport profile, policy hash, transport turn results.
- `agent_node_adapter.execute_tau_agent_node`: `cancel_event` (scheduler cancel → `AgentNodeRun.cancel`) and `plan_sha256`.

## Required proof
1. Fail-before-fix: `tests/test_generic_dag_tau_agent_dispatch.py` — with src changes stashed 10/11 fail (`ADAPTER_EXECUTION_FAILED` path), with them 11/11 pass. tau_agent never reaches the legacy command runner.
2. Real CLI, live SciLLM: `scripts/agentic-eval-tau-native-cli-dispatch.py` runs `uv run tau run <spec>` as a subprocess. Profile `claude-model-turn` (anthropic-oauth / claude-sonnet-4-6). Reviewer read `notes/nonce.txt` (fresh `secrets.token_hex`) with the native `read` tool. Read back: `final_text = NONCE=<nonce>`, nonce present in SQLite `tool_effect_recorded` payload, settlement `completed` with `tool_effect_receipt_sha256s`, node receipt settlement sha == scheduler settlement sha, SQLite agent events `agent_node_started … agent_node_settled`. Artifact: `local/agentic-evals/tau-340-native-cli/proof.json` (`tau.native_cli_dispatch_proof.v1`, PASS, live=true, mocked=false).
3. Negative cases through the same CLI, all `BLOCKED` with `provider_invoked=false`: disallowed path → `NATIVE_PATH_POLICY_INVALID`; unsupported tool → `NATIVE_TOOL_UNSUPPORTED`; missing profile → `TRANSPORT_PROFILE_UNAVAILABLE`; malformed node → `NATIVE_NODE_INVALID`. Unit: 8 preflight rejections with zero `tool_effect_recorded` events; pre-dispatch cancel → `CANCELLED`, zero provider builds. Retained eval `evals/tau_native_cli_dispatch_agentic_eval.json` → report `agentic-evals-report-20260906T1737Z.json`: READY, 4/4 live trials.
4. Regression: `tests/test_generic_dag.py` + `test_generic_dag_tau_agent_dispatch.py::test_command_node_cli_path_still_dispatches` pass (134 passed with adapter/herdr/sanity-runner suites; 257 passed across generic_dag_diagnostics/scheduler/watched/cli). Contract suite `uv run pytest -q` → 278 passed.

Original watchdog repro spec (`model: fake`, no `agent_requirement`) now returns `BLOCKED NATIVE_NODE_INVALID: agent_requirement (tau.agent_requirement.v1) is required` — typed preflight, no provider call — instead of the legacy-runner failure.

## Not proven / out of scope
Write-capable native workers; Herdr-hosted native nodes (tau#315); semantic quality beyond nonce recall. Worktree audit reports 6 pre-existing dirty secondary worktrees (watchdog repair-worktrees tau-315/316/330/338, tau-causal-replay, tau-gs001) — none created or used by this ticket; work was in the primary checkout on main.

## Worktree retention (explicit)
`audit-worktrees.sh --scope-path src/tau_coding` flags three dirty secondary worktrees, none created or used by this ticket (all work was in the primary checkout on `main`):
- `~/.local/state/project-watchdog/repair-worktrees/tau-338` (branch `watchdog/issue-338`, `M src/tau_coding/generic_dag.py`) — live project-watchdog lane for tau#338; retained, owner is the watchdog.
- `~/workspace/experiments/tau-causal-replay` (branch `fix/external-review-integrity`, `M src/tau_coding/dag_viewer/server.py`) — separate human lane; retained.
- `~/workspace/experiments/tau-gs001` (detached at `53fc21ea7`, `M src/tau_coding/skill_dag_adapter.py`) — separate lane; retained.
Closed with `GH_TICKET_SKIP_WORKTREE_AUDIT=1` after recording this retention; nothing in these worktrees was committed, reset, or removed.
