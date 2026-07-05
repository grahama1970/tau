# Tau Real-World Sanity Checks

Run all checks:

```bash
scripts/run-real-world-sanity.sh
```

Run selected levels or checks:

```bash
scripts/run-real-world-sanity.sh --levels simple,medium
scripts/run-real-world-sanity.sh --levels advanced --checks advanced.provider_readiness
scripts/run-real-world-sanity.sh --levels advanced --checks advanced.provider_readiness --provider-cleanup-mode apply
scripts/run-real-world-sanity.sh --levels advanced --checks advanced.browser_cdp_proof
```

The runner writes a receipt to:

```text
experiments/goal-locked-subagents/proofs/real-world-sanity/<run-id>/real-world-sanity-receipt.json
```

## Contract

- `mocked: false` is required for every check receipt.
- Fixture-only commands that report `mocked: true` are excluded.
- Live provider failures are recorded as `BLOCKED`; the runner does not replace them with fake evidence.
- Negative controls pass only when Tau emits the expected fail-closed receipt.
- Checks may record bounded retry attempts when the check itself is a live
  allocation probe. Each attempt preserves its own stdout/stderr paths and
  receipt summary.
- Generic DAG receipt summaries include `timed_node_count`,
  `node_duration_seconds_total`, `node_duration_seconds_max`, and
  `node_durations_seconds` when node receipts record timing. This lets project
  agents inspect provider-backed adapter duration from the suite receipt before
  opening nested run artifacts.
- Generic DAG receipt summaries include `dispatched_node_count`,
  `blocked_node_count`, `node_attempt_counts`, `node_statuses`,
  `node_verdicts`, and `node_error_counts` so timeout, retry, and blocked-node
  behavior can be audited from the suite receipt.
- Generic DAG receipt summaries include `resume_requested` and `resume_source`
  so project agents can distinguish direct spec-path resume from
  run-directory metadata recovery.
- Advanced provider checks accept `--provider-cleanup-mode off|audit|dry-run|apply`.
  The default is `dry-run`. `apply` closes only Herdr workspaces recorded in the
  run's own `runtime-manifest.json` and records a cleanup receipt. Apply-mode
  post-cleanup summaries include `applied_action_count` and
  `post_verified_absent_count`; the latter counts workspaces that Herdr
  confirmed as `workspace_not_found` after close.

## Simple

- `simple.version`: Tau CLI starts from the local checkout.
- `simple.command_spec_catalog`: planner, orchestrator, coder, and reviewer command specs are readable JSON command specs.
- `simple.local_handoff_loop`: real local Tau command-loop dispatch routes `goal-guardian -> project-or-harness-verifier -> human`.
- `simple.project_dag_creator_reviewer`: Tau runs a `tau.dag_contract.v1` creator/reviewer DAG with real local subprocess workers and records a reviewer verdict against the immutable goal.

## Medium

- `medium.provider_dag_plan`: Tau planner emits a scratch coder/reviewer DAG receipt.
- `medium.provider_dag_plan_status`: Tau summarizes a provider DAG planner-only run through the read-only `tau run-status` surface.
- `medium.project_dag_reviewer_repair_loop`: Tau runs a project-agent DAG repair loop where reviewer returns `REVISE`, creator reruns, and reviewer returns `PASS`.
- `medium.project_dag_ready_queue_parallel_join`: Tau runs a bounded ready-queue project DAG where a virtual start node fans out to concurrent `research-auditor` and `coder` local subprocess branches, then joins at `reviewer` for an immutable-goal verdict.
- `medium.generic_dag_run`: Tau executes a schema-validated generic local subprocess DAG with planner -> coder -> reviewer dependencies.
- `medium.generic_dag_status`: Tau summarizes the generic DAG run through the read-only `tau run-status` surface.
- `medium.generic_dag_resume`: Tau resumes a generic DAG from an existing valid node receipt and does not rerun that node command.
- `medium.generic_dag_resume_from_run_dir`: Tau resumes a generic DAG from run-directory checkpoint metadata without requiring the original spec path.
- `medium.generic_dag_stale_work_order_blocks`: Tau refuses to resume a generic DAG node from a stale work-order receipt and blocks when rerun fails.
- `medium.generic_dag_stale_work_order_status`: Tau summarizes the stale work-order blocked generic DAG through the read-only `tau run-status` surface.
- `medium.generic_dag_timeout_fail_closed`: Tau retries a timed-out generic DAG worker up to `max_attempts` and blocks with `SUBAGENT_TIMEOUT`.
- `medium.approval_gate_pass`: Tau accepts an explicit human approval packet for a gated working-tree mutation precondition.
- `medium.approval_gate_fail_closed`: Tau blocks a GitHub ticket-closure gate when the approval packet names a different action.
- `medium.approval_gate_expired_fail_closed`: Tau blocks a gated mutation when the approval packet is expired.
- `medium.approval_gate_status`: Tau summarizes the blocked approval-gate receipt through the read-only `tau run-status` surface.
- `medium.herdr_cleanup_dry_run`: Tau identifies run-owned Herdr cleanup candidates without mutating Herdr.
- `medium.herdr_cleanup_status`: Tau summarizes a standalone Herdr cleanup receipt through the read-only `tau run-status` surface.
- `medium.herdr_cleanup_session_apply_fail_closed`: Tau blocks Herdr cleanup apply when a run records a Herdr session candidate, because session stop/delete remains unsupported until Tau records stronger session ownership.
- `medium.herdr_gc_apply_requires_approval`: Tau blocks broad Herdr GC apply when no approval receipt authorizes label-based workspace cleanup.
- `medium.orchestration_evidence_status`: Tau summarizes a standalone orchestration evidence receipt through the read-only `tau run-status` surface.
- `medium.provider_lifecycle_status`: Tau summarizes provider lifecycle state artifacts through the read-only `tau run-status` surface.
- `medium.provider_lifecycle_crashed_ready_fail_closed`: Tau normalizes a provider readiness record that claims ready but has no live foreground process as crashed, not schedulable.
- `medium.provider_readiness_status`: Tau summarizes structured provider readiness records through the read-only `tau run-status` surface.
- `medium.provider_pane_status`: Tau summarizes provider-pane allocation records through the read-only `tau run-status` surface, including a fail-closed blocked pane allocation fixture.
- `medium.provider_dag_status`: Tau summarizes provider DAG visibility, cleanup, and orchestration evidence through the read-only `tau run-status` surface.
- `medium.dag_stress_poc`: deterministic Tau scheduler rungs cover one-pass, retry, fan-out/fan-in, timeout, error, invalid receipt, wrong result, and model-missing cases.
- `medium.dag_stress_status`: Tau summarizes a deterministic DAG stress suite through the read-only `tau run-status` surface.
- `medium.dag_stress_campaign`: repeated scheduler stress across retry budgets.
- `medium.dag_stress_campaign_status`: Tau summarizes a deterministic DAG stress campaign through the read-only `tau run-status` surface.

## Advanced

- `advanced.project_dag_reviewer_goal_drift_fail_closed`: Tau blocks a project-agent DAG when the reviewer verdict cites a goal hash that differs from the immutable DAG goal.
- `advanced.project_dag_timeout_fail_closed`: Tau blocks a project-agent DAG when a selected node command times out.
- `advanced.project_dag_non_json_fail_closed`: Tau blocks a project-agent DAG when a selected node emits non-JSON stdout.
- `advanced.project_dag_max_steps_fail_closed`: Tau blocks a project-agent DAG when reviewer keeps routing back and `max_total_attempts` is exhausted.
- `advanced.project_dag_bad_contract_course_correction`: Tau rejects an invalid project DAG contract and emits a `tau.dag_error.v1` course-correction JSON payload for project agents.
- `advanced.project_dag_evidence_manifest_goal_hash_fail_closed`: Tau rejects a project DAG before dispatch when a typed evidence manifest contains an artifact whose goal hash differs from the immutable DAG goal.
- `advanced.project_dag_command_policy_network_fail_closed`: Tau emits a project-agent-readable `command_policy_rejected` DAG error when a command spec declares network use without policy approval.
- `advanced.project_dag_command_policy_mutation_fail_closed`: Tau emits a project-agent-readable `command_policy_rejected` DAG error when a command spec declares mutation without policy approval.
- `advanced.project_dag_ready_queue_cycle_fail_closed`: Tau blocks a bounded ready-queue project DAG when the declared graph contains a cycle.
- `advanced.project_dag_ready_queue_mutating_branch_fail_closed`: Tau blocks bounded ready-queue scheduling when a concurrent node declares mutating behavior before branch locks exist.
- `advanced.project_dag_ready_queue_provider_policy_fail_closed`: Tau blocks bounded ready-queue scheduling when a concurrent node declares provider-live behavior before provider branch policy exists.
- `advanced.dag_expansion_apply_tampered_preview_fail_closed`: Tau validates and policy-checks an adaptive DAG expansion, then blocks `dag-expansion-apply` when the expanded preview artifact no longer matches the validation hash.
- `advanced.dag_route_memory_apply_requires_approval`: Tau projects route-memory candidates locally, then blocks Memory sync apply when no `memory_upsert` approval receipt is supplied.
- `advanced.github_apply_policy_missing_gates_fail_closed`: Tau blocks a GitHub apply projection when policy-required approval, preflight, and redaction gates are missing.
- `advanced.provider_readiness`: Herdr allocates visible Codex and OpenCode provider panes, writes structured `tau.provider_session_state.v1` lifecycle records, and records post-check cleanup when provider cleanup is enabled.
- `advanced.provider_dag_one_pass`: live visible Codex coder and OpenCode reviewer complete a one-pass scratch DAG.
- `advanced.generic_provider_dag_adapter`: generic DAG executes a provider-backed adapter node and carries `provider_live` evidence in the generic run receipt.
- `advanced.generic_provider_dag_adapter_resume`: generic DAG executes a provider-backed adapter node once, reruns the same DAG with resume enabled, and verifies the provider-backed node is resumed from its durable receipt.
- `advanced.provider_dag_repair_loop`: reviewer returns `REVISE`, coder retries, reviewer returns `PASS`.
- `advanced.provider_dag_max_attempts_fail_closed`: reviewer revisions exhaust max attempts and Tau blocks with verdict `REVISE`.
- `advanced.provider_dag_invalid_model_fail_closed`: invalid reviewer model blocks with verdict `REVIEWER_RECEIPT_INVALID`.
- `advanced.browser_cdp_proof`: Surf opens a local Tau proof page, Tau observes required proof text, captures a PNG screenshot, and writes `tau.browser_cdp_proof.v1`.

## Boundaries

This suite does not prove GitHub ticket closure, remote Tailscale monitoring,
production browser/chat UI rendering, or production repository mutation.
