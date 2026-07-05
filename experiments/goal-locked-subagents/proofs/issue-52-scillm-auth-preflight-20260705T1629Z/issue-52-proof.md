# Issue 52 Proof

Commit: see the final issue comment or repository history for the commit that contains this proof bundle.

## What changed

- Tau now runs an internal `/v1/scillm/auth` preflight before Battle worker materialization.
- The preflight uses the active Scillm proxy key and `X-Caller-Skill: battle`.
- If Codex OAuth is expired or stale inside the Scillm proxy, Tau diagnoses host/container auth state.
- If the container auth hash proves a stale bind mount, Tau attempts the known Scillm proxy recreate internally.
- If repair is not possible or auth remains invalid, Tau writes a fail-closed Battle manifest before dispatching workers.

## Proof Commands

```bash
uv run pytest tests/test_battle_scillm_auth_preflight.py
```

Result: `3 passed in 0.35s`

```bash
uv run python -m py_compile \
  src/tau_coding/battle_scillm.py \
  src/tau_coding/battle_live_handoff.py \
  tests/test_battle_scillm_auth_preflight.py
```

Result: exit code `0`

```bash
uv run ruff check tests/test_battle_scillm_auth_preflight.py
```

Result: `All checks passed!`

```bash
/home/graham/workspace/experiments/agent-skills/skills/battle/run.sh \
  arena-parent-spawn-proof battle-004 \
  --out /home/graham/workspace/experiments/tau/experiments/goal-locked-subagents/proofs/issue-52-scillm-auth-preflight-20260705T1629Z \
  --red-workers 2 \
  --blue-workers 2
```

Result: `run-receipt.json` status `PASS`, mocked `false`, live `brave_search_docker_arena_oracle_tau_harness`.

## Key Artifacts

- `run-receipt.json`: Battle parent-spawn proof status `PASS`, verdict `BLUE_SUCCESS`.
- `tau-live/scillm-auth-preflight.json`: Tau internal Scillm auth preflight status `PASS`, endpoint `/v1/scillm/auth`, caller skill `battle`, Codex status `valid`.
- `tau-live/manifest.json`: initial Tau materialization status `PASS`; two Blue workers and one initial Red worker materialized.
- `tau-live/spawn-manifest.json`: spawned Red child materialization status `PASS`.
- `lineage-receipts.json`: parent Red lane spawned child Red lane with explicit Tau subagent receipts.

Scillm call receipts:

- `tau-live/red/scillm-call-receipt.json`: `PASS`, HTTP `200`, parse `PASS`.
- `tau-live/red/workers/red-1/scillm-call-receipt.json`: `PASS`, HTTP `200`, parse `PASS`.
- `tau-live/blue/scillm-call-receipt.json`: `PASS`, HTTP `200`, parse `PASS`.
- `tau-live/blue/workers/blue-1/scillm-call-receipt.json`: `PASS`, HTTP `200`, parse `PASS`.

## Evidence Boundary

- mocked: no
- live: yes
- exercised: Tau internal Scillm auth preflight, live Battle parent-spawn proof, live Scillm worker materialization, Docker Judge replay.
- not exercised: an actual stale container bind mount during the final live run; stale/expired repair/block behavior is covered by deterministic simulation tests.
