# Tau human-goal-change bridge slice

## Scope

This bundle implements the bounded bridge from trusted human input:

```text
tau.human_goal_change.v1
```

to normal Tau command-loop input:

```text
tau.agent_handoff.v1
```

The generated start handoff routes to `next_agent.name = "goal-guardian"` and preserves the active `goal` object and the original `github` target. The bridge does **not** apply, persist, or migrate a new immutable goal capsule. It only records a deterministic local transformation receipt and writes a normal handoff artifact that existing Tau dispatch can consume.

## Contract

### New CLI

```bash
uv run tau human-goal-change-bridge \
  experiments/goal-locked-subagents/fixtures/valid-human-goal-change.json \
  --active-goal-hash sha256:active-goal \
  --trusted-human \
  --handoff-out /tmp/tau/start-handoff.json \
  --receipt /tmp/tau/human-goal-change-bridge-receipt.json
```

Arguments:

- `<human-goal-change.json>`: JSON object with `schema = "tau.human_goal_change.v1"`.
- `--active-goal-hash <hash>`: required by bridge validation, even though the older validator accepts `None`; missing hash fails closed before handoff output.
- `--trusted-human`: explicit trust assertion for the local route. Omit it to prove untrusted input fails closed.
- `--handoff-out <path>`: generated `tau.agent_handoff.v1` start handoff. Written only on successful validation.
- `--receipt <path>`: machine-readable bridge receipt. Written on success and schema-level validation failure.
- `--agents-root <dir>`: optional agent registry root used only to validate the generated normal handoff projection.

### Receipt schema

New schema id:

```text
tau.human_goal_change_bridge_receipt.v1
```

Receipt fields:

```json
{
  "schema": "tau.human_goal_change_bridge_receipt.v1",
  "ok": true,
  "dry_run": true,
  "trusted_human": true,
  "source": "/abs/path/input.json",
  "active_goal_hash": "sha256:active-goal",
  "input_schema": "tau.human_goal_change.v1",
  "output_schema": "tau.agent_handoff.v1",
  "next_agent": "goal-guardian",
  "handoff_path": "/abs/path/start-handoff.json",
  "handoff_sha256": "sha256:<canonical-json-hash>",
  "start_handoff": { "schema": "tau.agent_handoff.v1" },
  "errors": []
}
```

On failure, `ok=false`, `output_schema=null`, `next_agent=null`, `handoff_sha256=null`, `start_handoff=null`, and `errors` contains fail-closed reasons. The output handoff file is not written on failure.

## Generated handoff shape

The bridge emits a normal `tau.agent_handoff.v1` object with:

- unchanged `github` from the human packet,
- unchanged current `goal` from the human packet,
- `previous_subagent = "human"`,
- `result.status = "GOAL_CHANGE_REQUESTED"`,
- `result.evidence[0]` naming the validated `tau.human_goal_change.v1` source packet,
- `next_agent = {"name": "goal-guardian", "executor": "local", "reason": <human packet next_agent.reason>}`,
- propagated `required_evidence` plus a tightening item requiring a goal-guardian reconciliation receipt before any non-human continuation,
- propagated `stop_condition` plus a tightening sentence forbidding non-human continuation before reconciliation,
- `context.human_goal_change.new_goal` carrying the new goal proposal for goal-guardian without replacing `goal`.

## Fail-closed behavior

The bridge refuses to write a handoff when any of these are true:

1. `--trusted-human` is omitted.
2. `previous_subagent` is not `human`.
3. `next_agent.name` is not `goal-guardian`.
4. `--active-goal-hash` is missing or does not match `goal.goal_hash`.
5. The generated handoff fails existing `tau.agent_handoff.v1` projection validation, including `github.repo`, `github.target`, routable `next_agent`, executor, and goal-hash preservation.

Failures are local and dry-run. This bundle does not create or mutate GitHub issues.

## File-by-file implementation

### `src/tau_coding/human_goal_change.py`

Adds:

- `TAU_HUMAN_GOAL_CHANGE_BRIDGE_RECEIPT_SCHEMA`.
- `load_human_goal_change`.
- `bridge_human_goal_change_to_handoff`.
- `build_human_goal_change_bridge_receipt`.
- `write_human_goal_change_bridge_receipt`.
- helper hashing and JSON writing functions.

The existing validator remains the first trust gate. The bridge then validates the generated handoff through the existing handoff projection path to avoid a second orchestration contract.

### `src/tau_coding/cli.py`

Adds command dispatch for:

```text
human-goal-change-bridge
```

The command writes the bridge receipt to `--receipt`, writes the generated handoff to `--handoff-out` only on success, echoes the receipt JSON to stdout, and returns non-zero when `ok=false`.

### `experiments/goal-locked-subagents/schemas/tau.human_goal_change_bridge_receipt.v1.schema.json`

Adds the deterministic receipt schema.

### `tests/test_human_goal_change.py`

Adds function-level tests for:

- bridge receipt schema id,
- valid bridge output shape,
- projection validation of generated handoff,
- untrusted bridge failure,
- non-human previous-subagent failure,
- stale goal-hash failure.

### `tests/test_cli.py`

Adds CLI tests for:

- valid bridge writes receipt and start handoff,
- omitted `--trusted-human` writes failure receipt and no handoff,
- generated start handoff can enter `tau handoff-command-loop` using a local goal-guardian command-spec smoke.

## Exact test commands

Focused slice:

```bash
uv run pytest tests/test_human_goal_change.py tests/test_handoff_dispatch.py tests/test_cli.py -q
```

Existing focused transport/CLI proof:

```bash
uv run pytest tests/test_github_handoff.py tests/test_cli.py -q
```

Manual bridge smoke:

```bash
TMPDIR=$(mktemp -d)
uv run tau human-goal-change-bridge \
  experiments/goal-locked-subagents/fixtures/valid-human-goal-change.json \
  --active-goal-hash sha256:active-goal \
  --trusted-human \
  --handoff-out "$TMPDIR/start-handoff.json" \
  --receipt "$TMPDIR/bridge-receipt.json"

uv run tau handoff-command-loop \
  --start "$TMPDIR/start-handoff.json" \
  --active-goal-hash sha256:active-goal \
  --agents-root experiments/goal-locked-subagents/agents \
  --command-spec-root experiments/goal-locked-subagents/agent-command-specs \
  --receipt-dir "$TMPDIR/command-loop" \
  --max-steps 2
```

The command-loop smoke is local/dry-run unless a command spec itself is configured to do live work. The committed `goal-guardian` adapter only checks the preserved active goal hash in this slice.

## Rollback / rebuild notes

Rollback is a normal git revert of the patch:

```bash
git apply -R patches/0001-human-goal-change-bridge.patch
```

No persistent goal capsules, GitHub issues, PRs, labels, database records, or migrations are created by this slice.

## Known limitations

- The bridge carries `new_goal` in the start handoff context and receipt; it does not write `goals/current.json` or any durable immutable-goal capsule.
- The built-in `handoff-goal-guardian-adapter` still performs preserved-goal-hash verification. A later slice can teach goal-guardian to emit a richer reconciliation receipt or generate a goal capsule candidate.
- Trust is a CLI flag for this local slice. A later UI/browser route should bind `--trusted-human` to an authenticated human action instead of exposing it to non-human agents.
- The proof is deterministic local/mock scope. It is not browser/WebGPT closure proof and it does not apply live GitHub mutations.
