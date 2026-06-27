# Clarify, Then Create Full Architecture And Code Solution

## Objective

Create the next bounded Tau agentic-harness slice:

`tau.human_goal_change.v1` must become executable as a trusted-human input route that produces a normal `tau.agent_handoff.v1` start handoff for `goal-guardian`, so the existing command-loop can reconcile a human-only immutable goal change before any non-human agent continues.

If any material ambiguity remains, return only numbered clarifying questions.

If no material ambiguity remains, return a complete solution bundle for this scoped slice. If more than one finished file is required, return one zip named:

`tau-human-goal-change-bridge-solution.zip`

Do not return a review verdict. Do not return PASS/NEEDS_CHANGES/BLOCKED. Do not ask the project agent to invent architecture choices if the constraints below are sufficient for WebGPT to choose.

## Operating Policy For Tau

Tau should use direct `$webgpt` escalation when:

- moving between major phases,
- blocked or repeating the same failure,
- error-spiraling into unrelated tests or implementation churn,
- validating complex loop, harness, chat, memory, TUI, or subagent architecture,
- using `$create-architecture` for a scoped missing implementation slice.

`$browser-oracle` stores the project binding. Direct `$webgpt` submits the creation bundle. `$ask` is intentionally not the default path for Tau because it added too much wrapper complexity for this project-agent workflow.

Under `$create-architecture`, WebGPT creates or clarifies the scoped solution; the local project agent ports and proves it with deterministic tests and receipts.

## Handoff

Current Tau repository state:

- Branch: `main`
- Remote: `grahama1970/main`
- Recent pushed commits:
  - `184c014 Support new-target GitHub transport for Tau handoffs`
  - `fe3f008 Record Tau UI handoff command-loop proof`
  - `2847e3f Add reviewer command overlay for Tau handoffs`

Recent local proof:

- Focused transport/CLI tests: `uv run pytest tests/test_github_handoff.py tests/test_cli.py -q`
  - result: `109 passed in 3.21s`
- UI-extracted Tau handoff entered `handoff-command-loop`, selected `reviewer`, ran one bounded command, and stopped at `human`.
- Terminal GitHub transport now handles `github.target: "new"` as dry-run issue creation.
  - rendered command:
    `gh issue create --repo grahama1970/tau --title "Tau handoff: human" --body-file - --label agent-work,next:human,executor:human`
- Fresh UI marker exists for the Tau chat surface after the latest check.
- No human-goal-change bridge implementation has been added in this round yet.

## Goal

### Product Goal

Harden Tau into a goal-locked agentic harness that can carry a Watch-style chat handoff into durable, receipt-backed subagent orchestration without allowing non-human actors to mutate the immutable human goal.

### Scoped Slice Goal

Implement a small, deterministic bridge from:

```text
trusted human input: tau.human_goal_change.v1
```

to:

```text
normal route input: tau.agent_handoff.v1 with next_agent.name = goal-guardian
```

The bridge should let Tau use the existing command-loop and `goal-guardian` adapter for goal-change reconciliation instead of inventing a second orchestration path.

### Non-Goals

- Do not write or migrate persistent goal capsules in this slice unless a minimal file artifact is necessary for the handoff.
- Do not create or mutate live GitHub issues by default.
- Do not bypass trusted-human validation.
- Do not let WebGPT, ChatGPT Pro, reviewer, coder, or any non-human agent create a new immutable goal.
- Do not broaden the subagent list beyond the current allowlist unless tests require one explicitly.
- Do not redesign the Tau UI in this slice.

### Implemented Behavior

- Minimal `tau.agent_handoff.v1` validation and GitHub projection exist.
- Minimal `tau.generated_ticket.v1` validation and dry-run issue creation exist.
- Minimal `tau.human_goal_change.v1` validation exists.
- `handoff-command-loop` can execute bounded command specs and stop at `human`.
- Built-in `goal-guardian` adapter can verify preserved active goal hash for normal handoffs.
- Terminal GitHub transport can render dry-run `gh issue create` for `target: "new"`.

### Missing Behavior For This Slice

- No CLI currently turns a valid trusted `tau.human_goal_change.v1` packet into a normal `tau.agent_handoff.v1` start handoff.
- No receipt currently records this bridge as a deterministic transformation.
- The command-loop cannot yet start from a human goal-change packet directly.
- There is no focused test proving:
  - trusted human goal-change routes to goal-guardian as a handoff,
  - untrusted or stale goal-change fails closed,
  - generated start handoff validates under existing handoff projection rules,
  - generated start handoff can enter existing `handoff-command-loop`.

### Acceptance Gates

The returned solution should include tests for:

1. Valid trusted human goal-change converts into `tau.agent_handoff.v1`.
2. Output handoff has:
   - same `github`,
   - same current `goal`,
   - `previous_subagent: "human"`,
   - `result.status` such as `GOAL_CHANGE_REQUESTED`,
   - `result.evidence` naming the validated `tau.human_goal_change.v1` packet,
   - `next_agent.name: "goal-guardian"`,
   - `required_evidence` and `stop_condition` propagated or tightened for reconciliation.
3. Stale `goal.goal_hash` fails closed.
4. Untrusted human input fails closed.
5. Non-human `previous_subagent` fails closed.
6. CLI writes a machine-readable receipt and exits non-zero on invalid input.
7. Existing tests remain passing:
   - `uv run pytest tests/test_human_goal_change.py tests/test_handoff_dispatch.py tests/test_cli.py -q`

Optional if simple:

8. A smoke command proves the generated start handoff can be consumed by:
   `tau handoff-command-loop --start <generated-handoff.json> ...`

## Current Local Evidence

### `tau.human_goal_change.v1` validator

Relevant repo file: `src/tau_coding/human_goal_change.py`

Current behavior:

```python
TAU_HUMAN_GOAL_CHANGE_SCHEMA = "tau.human_goal_change.v1"

@dataclass(frozen=True, slots=True)
class HumanGoalChangeValidationResult:
    ok: bool
    next_agent: str | None = None
    errors: tuple[str, ...] = ()

def validate_human_goal_change(
    payload: Mapping[str, Any],
    *,
    active_goal_hash: str | None = None,
    trusted_human: bool = False,
) -> HumanGoalChangeValidationResult:
    errors: list[str] = []
    _require_fields(
        payload,
        (
            "schema",
            "github",
            "goal",
            "previous_subagent",
            "context",
            "new_goal",
            "rationale",
            "next_agent",
            "required_evidence",
            "stop_condition",
        ),
        "human_goal_change",
        errors,
    )
    if payload.get("schema") != TAU_HUMAN_GOAL_CHANGE_SCHEMA:
        errors.append(...)
    if not trusted_human:
        errors.append("human goal change requires trusted human author")
    if previous_agent != "human":
        errors.append("human goal change requires previous_subagent=human")
    if next_name != "goal-guardian":
        errors.append("human goal change must route next_agent.name to goal-guardian")
```

### Valid fixture

Repo file: `experiments/goal-locked-subagents/fixtures/valid-human-goal-change.json`

```json
{
  "schema": "tau.human_goal_change.v1",
  "github": {
    "repo": "grahama1970/chatgpt-lab",
    "target": "issue#123"
  },
  "goal": {
    "goal_id": "goal-tau-orchestration-001",
    "goal_version": 1,
    "goal_hash": "sha256:active-goal"
  },
  "previous_subagent": "human",
  "context": {
    "summary": "The current goal needs to include GitHub ticket orchestration.",
    "artifacts": []
  },
  "new_goal": {
    "text": "Build Tau's goal-locked GitHub ticket harness one validated slice at a time.",
    "success_criteria": [
      "New goal capsule is written."
    ],
    "constraints": [
      "Only humans can amend immutable goals."
    ],
    "non_goals": []
  },
  "rationale": "The goal change is required to align the harness with durable GitHub routing.",
  "next_agent": {
    "name": "goal-guardian",
    "reason": "Goal changes must be reconciled before further work."
  },
  "required_evidence": [
    "Open tickets are classified as keep, close, migrate, or regenerate."
  ],
  "stop_condition": "Goal guardian posts a reconciliation receipt."
}
```

### Existing goal-guardian adapter

Relevant repo file: `src/tau_coding/cli.py`

Current behavior:

```python
def project_agent_handoff_goal_guardian_adapter_command(...):
    start = json.loads(sys.stdin.read())
    active_goal_hash = os.environ.get("TAU_HANDOFF_ACTIVE_GOAL_HASH")
    goal_hash = goal.get("goal_hash")
    if not active_goal_hash or goal_hash != active_goal_hash:
        raise RuntimeError("goal-guardian refused stale or changed goal hash")
    return {
        "schema": "tau.agent_handoff.v1",
        "github": dict(github),
        "goal": dict(goal),
        "previous_subagent": "goal-guardian",
        "context": {...},
        "result": {
            "status": "PASS",
            "summary": "Goal guardian verified that the handoff preserved the active goal hash.",
            "evidence": ["TAU_HANDOFF_ACTIVE_GOAL_HASH matched handoff.goal.goal_hash"]
        },
        "next_agent": {...},
        ...
    }
```

### Existing command-loop CLI

Current CLI command:

```bash
uv run tau handoff-command-loop \
  --start <handoff.json> \
  --agents-root <agent-registry-root> \
  --command-spec-root experiments/goal-locked-subagents/agent-command-specs \
  --receipt-dir <receipt-dir> \
  --max-steps 2
```

### Existing goal-guardian command spec

Repo file: `experiments/goal-locked-subagents/agent-command-specs/goal-guardian/tau-dispatch-command.json`

The command spec already invokes:

```bash
uv run tau handoff-goal-guardian-adapter
```

## Relevant Files

Likely files in scope:

- `src/tau_coding/human_goal_change.py`
- `src/tau_coding/cli.py`
- `tests/test_human_goal_change.py`
- `tests/test_cli.py`
- maybe `README.md`
- maybe `PROJECT_KNOWLEDGE.md`

Current CLI dispatcher does not include a `human-goal-change-*` command. Nearby commands include:

```python
if prompt_option is None and command == "handoff-project":
    ...

if prompt_option is None and command == "handoff-command-loop":
    ...

if prompt_option is None and command == "handoff-goal-guardian-adapter":
    ...
```

## Constraints

- Keep the model-facing contract small.
- Tau derives labels and projections; do not add GitHub label fields to the human goal-change schema.
- Human goal changes are trusted-human only.
- Default behavior must be dry-run or local file receipt only.
- Invalid input must fail closed before command-loop dispatch.
- Prefer functions over classes unless state is required.
- Do not add a broad orchestrator, cron worker, GitHub mutation, or persistence migration in this slice.
- Maintain compatibility with existing `tau.agent_handoff.v1` validation.
- All proof claims must state mocked/live scope.

## Non-Goals

- Do not create the final Sparta Chat.
- Do not create a full GitHub issue/PR queue.
- Do not implement live GitHub apply for goal changes.
- Do not implement a durable `goals/current.json` store unless you make it explicitly dry-run and tested.
- Do not use browser/WebGPT output as closure proof.

## Required Output

If questions remain, ask only numbered clarifying questions.

If ready, return a complete implementation-ready solution. If multiple files are changed, return one zip named:

`tau-human-goal-change-bridge-solution.zip`

The solution should include:

- architecture contract for the bridge,
- exact CLI command name and arguments,
- exact receipt schema for the bridge,
- file-by-file finished implementation,
- tests and fixtures,
- exact commands to run,
- fail-closed behavior,
- rollback/rebuild notes,
- known limitations,
- `MANIFEST.json`,
- `prompt_improvements.md`.

Do not return a review verdict.
