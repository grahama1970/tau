# Issue 116 Proof: Battle Red Artifact Recognizer

Ticket: https://github.com/grahama1970/tau/issues/116

## Scope

Issue #116 reported that Tau's Red artifact materializer rejected valid
method_replace exploits that load local `app.py` through `importlib` rather than
the exact substring `from app import import_zip` or `import app`.

This repair changes Tau's recognizer from substring matching to AST-based local
`app.py` binding and call detection. It also updates Battle's downstream artifact
review gate so the same valid Red artifacts are not rejected after Tau
materializes them.

## Changed Paths

Tau:

- `src/tau_coding/battle_live_handoff.py`
- `tests/test_battle_live_handoff.py`
- `docs/proofs/tickets/issue-116-*`

Battle skill dependency:

- `skills/battle/src/battle_skill/team_artifact_pipeline.py`
- `skills/battle/tests/test_team_artifact_pipeline_contract.py`

## Recognized Local App Load Forms

Tau now accepts these AST-confirmed local `import_zip` bindings:

- `from app import import_zip` including aliases.
- `import app` including aliases, then `app.import_zip` or `getattr(app, "import_zip")`.
- `importlib.import_module("app")` including aliases, then attribute or `getattr` lookup.
- `importlib.util.spec_from_file_location(..., Path.cwd() / "app.py")` with
  `module_from_spec`, `exec_module`, and attribute or `getattr` lookup.
- One-hop helper functions that return the locally loaded `import_zip` callable
  and are then called by the exploit.

The recognizer still rejects HTTP-only scripts and wrong-module imports.

## Deterministic Commands

Tau formatting and static checks:

```bash
uv run ruff format src/tau_coding/battle_live_handoff.py tests/test_battle_live_handoff.py
uv run ruff check src/tau_coding/battle_live_handoff.py tests/test_battle_live_handoff.py
uv run python -m py_compile src/tau_coding/battle_live_handoff.py tests/test_battle_live_handoff.py
```

Results:

- `ruff format`: `2 files left unchanged`
- `ruff check`: `All checks passed!`
- `py_compile`: exit code `0`

Tau targeted behavior checks:

```bash
uv run pytest -q \
  tests/test_battle_live_handoff.py \
  tests/test_battle_adaptive_lineage_tau_contract.py \
  tests/test_battle_scillm_auth_preflight.py
```

Result:

- `23 passed in 0.59s`

Battle dependency checks:

```bash
uv run --project /home/graham/workspace/experiments/agent-skills/skills/battle \
  ruff check \
  /home/graham/workspace/experiments/agent-skills/skills/battle/src/battle_skill/team_artifact_pipeline.py \
  /home/graham/workspace/experiments/agent-skills/skills/battle/tests/test_team_artifact_pipeline_contract.py

uv run --project /home/graham/workspace/experiments/agent-skills/skills/battle \
  python -m py_compile \
  /home/graham/workspace/experiments/agent-skills/skills/battle/src/battle_skill/team_artifact_pipeline.py \
  /home/graham/workspace/experiments/agent-skills/skills/battle/tests/test_team_artifact_pipeline_contract.py

uv run --project /home/graham/workspace/experiments/agent-skills/skills/battle \
  pytest -q \
  /home/graham/workspace/experiments/agent-skills/skills/battle/tests/test_team_artifact_pipeline_contract.py
```

Results:

- Battle `ruff check`: `All checks passed!`
- Battle `py_compile`: exit code `0`
- Battle targeted pytest: `6 passed in 0.85s`

## Live And Replay Evidence

### Original Live False Negative

Command:

```bash
TAU_REPO=/tmp/tau-main-issue-137.7nchbf \
  /home/graham/workspace/experiments/agent-skills/skills/battle/run.sh \
  adaptive-red-blue-lineage-canary battle-004 \
  --out docs/proofs/tickets/issue-116-adaptive-canary-20260726/run \
  --run-id issue-116-adaptive-canary-20260726 \
  --timeout-s 300 \
  --generations 2
```

Receipt:

- `docs/proofs/tickets/issue-116-adaptive-canary-20260726/run/campaign-receipt.json`

Result:

- `mocked: false`
- `live: true`
- `status: FAIL`
- `failure_reason: red artifact pipeline blocked`
- Tau live materialization manifest: `status: PASS`
- Battle Red artifact review: `status: BLOCKED`
- Battle Red review error: `red artifact does not bind the public app import interface`

This proved Tau could materialize the provider-generated artifact after the Tau
recognizer change, but Battle's downstream review gate still used the old shape
constraint.

### Fresh Live Rerun Boundary

Command:

```bash
TAU_REPO=/tmp/tau-main-issue-137.7nchbf \
  /home/graham/workspace/experiments/agent-skills/skills/battle/run.sh \
  adaptive-red-blue-lineage-canary battle-004 \
  --out /tmp/tau-main-issue-137.7nchbf/docs/proofs/tickets/issue-116-adaptive-canary-rerun-20260726/run \
  --run-id issue-116-adaptive-canary-rerun-20260726 \
  --timeout-s 300 \
  --generations 2
```

Receipt:

- `docs/proofs/tickets/issue-116-adaptive-canary-rerun-20260726/run/campaign-receipt.json`

Result:

- `mocked: false`
- `live: true`
- `status: FAIL`
- `failure_reason: red materialized artifact is not PASS`
- Red and Blue provider call receipts both report `status: PASS`, `mocked: false`, `live: true`

The generated Red exploit used a helper-returned local `import_zip` callable.
That exposed a remaining recognizer gap, which is now covered by the AST helper
function detection and the exact generated Red artifact replay below.

### Final Fresh Live Qualification

Command:

```bash
TAU_REPO=/tmp/tau-main-issue-137.7nchbf \
  /home/graham/workspace/experiments/agent-skills/skills/battle/run.sh \
  adaptive-red-blue-lineage-canary battle-004 \
  --out /tmp/tau-main-issue-137.7nchbf/docs/proofs/tickets/issue-116-adaptive-canary-final-20260726/run \
  --run-id issue-116-adaptive-canary-final-20260726 \
  --timeout-s 300 \
  --generations 2
```

Receipt:

- `docs/proofs/tickets/issue-116-adaptive-canary-final-20260726/run/campaign-receipt.json`

Result:

```json
{
  "status": "PASS",
  "mocked": false,
  "live": "tau_scillm_docker_judge_two_generation_red_blue",
  "fixture_fallback_used": false,
  "generation_count": 2,
  "reason": "two_generation_red_blue_lineage_evaluated_1_lineage_stop"
}
```

Focused receipt fields:

- Generation 1 Tau live status: `PASS`
- Generation 1 Red artifact pipeline: `PASS`
- Generation 1 Blue artifact pipeline: `PASS`
- Generation 1 Docker Judge: `PASS`, verdict `BLUE_SUCCESS`
- Generation 2 Tau live status: `PASS`
- Generation 2 Red artifact pipeline: `PASS`
- Generation 2 Blue artifact pipeline: `PASS`
- Generation 2 Docker Judge: `PASS`, verdict `BLUE_SUCCESS`
- Selection receipt: `PASS`
- Specimen population: `specimens_evaluated=4`, `total_variants_sampled=32`,
  `total_variants_rejected=31`
- Event journal count: `24`

### Exact Generated Red Artifact Replay

Command:

```bash
uv run python - <<'PY'
import json
from pathlib import Path
from tau_coding.battle_live_handoff import _materialize_team_artifact

receipt = Path(
    "docs/proofs/tickets/issue-116-adaptive-canary-rerun-20260726/run/"
    "generation-1/tau-live/red/scillm-call-receipt.json"
)
payload = json.loads(receipt.read_text())
output = _materialize_team_artifact(
    payload["content"],
    team="red",
    out_dir=Path("/tmp/issue-116-tau-materialize-replay-red"),
)
print(json.dumps(output, indent=2, sort_keys=True))
PY
```

Result:

```json
{
  "artifact_type": "red_exploit",
  "path": "/tmp/issue-116-tau-materialize-replay-red/red_exploit_submission.py",
  "reason": null,
  "status": "PASS"
}
```

### Reviewed Replay With Docker Judge

Replay source:

- Real provider-generated artifacts from the live canary.
- No provider call in the replay itself.
- Docker review and Judge executed against copied target and artifacts.

Receipt:

- `docs/proofs/tickets/issue-116-reviewed-replay-20260726/summary.json`
- `docs/proofs/tickets/issue-116-reviewed-replay-20260726/generation-1/judge/judge-receipt.json`

Result:

```json
{
  "status": "PASS",
  "mocked": false,
  "live": true,
  "provider_live": false,
  "judge_status": "PASS",
  "judge_verdict": "BLUE_SUCCESS"
}
```

Pipeline result fields:

- Red artifact review pipeline: `PASS`
- Blue artifact review pipeline: `PASS`
- Docker Judge receipt: `PASS`
- Judge attempts: `1`

## What This Proves

- The Tau recognizer accepts the requested local-app AST load forms instead of
  fixed import substrings.
- The Tau recognizer accepts both classes of real provider-generated Red
  artifacts seen during this investigation, including helper-returned callable
  form.
- The Tau recognizer still rejects HTTP-only and wrong-module scripts.
- Battle's downstream review gate now accepts the same valid local-app Red load
  forms, avoiding a second false-negative after Tau materialization.
- Real provider artifacts can pass Red/Blue review and Docker Judge under the
  repaired gates.
- The fresh two-generation Red/Blue live qualification reaches `PASS` with
  `mocked=false`, no fixture fallback, four specimens evaluated, Docker Judge
  receipts, and deterministic selection.

## Evidence Classification

- `mocked: no`
- `live: yes`
- Actual behavior exercised: AST recognizer, Tau materialization, Battle artifact
  review, Docker compile/review, Docker Judge.
- Remaining unverified claim: none for issue #116's recognizer acceptance
  criterion.
