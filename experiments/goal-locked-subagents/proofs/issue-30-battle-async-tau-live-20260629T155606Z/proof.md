# Issue #30 Proof: Battle async Tau live handoff

Issue: https://github.com/grahama1970/tau/issues/30

## Diagnosis

Current HEAD contains the async Battle Tau handoff implementation in
`src/tau_coding/battle_live_handoff.py`. The implementation creates one task
per team and consumes them with `asyncio.as_completed`, then writes one
`tau.subagent_receipt.v1` and validation receipt per team.

## Commands

```bash
PYTHONPATH=src uv run pytest tests/test_battle_live_handoff.py tests/test_subagent_receipt.py -q
```

Result: `9 passed in 0.62s`.

```bash
cd /home/graham/workspace/experiments/agent-skills/skills/battle
./run.sh battle-v1-operational battle-003 \
  --out /tmp/battle-v1-tau-live-issue30-20260629T155606Z \
  --red-workers 1 \
  --blue-workers 1 \
  --max-attempts 1 \
  --require-memory \
  --tau-live
```

Result: exit `0`, `run-receipt.json` status `PASS`.

```bash
cd /home/graham/workspace/experiments/agent-skills/skills/battle
python3 sanity/battle_v1_operational_acceptance.py \
  /tmp/battle-v1-tau-live-issue30-20260629T155606Z \
  --allow-first-recall-empty \
  --min-red-workers 1 \
  --min-blue-workers 1
```

Result: `BATTLE_V1_OPERATIONAL_ACCEPTANCE_PASS`.

## Key Artifacts

- Summary manifest: `experiments/goal-locked-subagents/proofs/issue-30-battle-async-tau-live-20260629T155606Z/manifest.json`
- Fresh Battle run: `experiments/goal-locked-subagents/proofs/issue-30-battle-async-tau-live-20260629T155606Z/battle-v1-tau-live-issue30-20260629T155606Z`
- Tau live manifest: `experiments/goal-locked-subagents/proofs/issue-30-battle-async-tau-live-20260629T155606Z/battle-v1-tau-live-issue30-20260629T155606Z/tau-live/manifest.json`
- Red Tau receipt: `experiments/goal-locked-subagents/proofs/issue-30-battle-async-tau-live-20260629T155606Z/battle-v1-tau-live-issue30-20260629T155606Z/tau-live/red/tau-subagent-receipt.json`
- Blue Tau receipt: `experiments/goal-locked-subagents/proofs/issue-30-battle-async-tau-live-20260629T155606Z/battle-v1-tau-live-issue30-20260629T155606Z/tau-live/blue/tau-subagent-receipt.json`

## Observed Acceptance Fields

```json
{
  "mocked": false,
  "live": true,
  "status": "PASS",
  "scheduling": {
    "mode": "asyncio.as_completed",
    "team_count": 2
  },
  "red": {
    "http_status": 200,
    "status": "PASS",
    "validation_ok": true
  },
  "blue": {
    "http_status": 200,
    "status": "PASS",
    "validation_ok": true
  }
}
```

## Proof Boundary

This proves the bounded Battle tau-live Red/Blue handoffs are concurrent and
receipt-backed for one Red and one Blue worker.

This does not prove unbounded Battle swarm scheduling, Scillm delegate/batch/tool
execution, QEMU/AFL campaigns, or autonomous GitHub issue monitoring.
