# Issue 190 Sprite-Sheet Conformance Proof

Ticket: grahama1970/tau#190

## Live E2E Gate

Command:

```bash
PYTHONPATH=src uv run tau sprite-sheet-conformance --allow-live-filesystem --output docs/proofs/tickets/issue-190-sprite-sheet-conformance-v2-20260727/sprite-sheet-conformance.json
```

Result: exit code 0.

Receipt:

- `docs/proofs/tickets/issue-190-sprite-sheet-conformance-v2-20260727/sprite-sheet-conformance.json`
- `status=PASS`
- `mocked=false`
- `live=true`
- `provider_live=false`
- `failed_checks=[]`
- `frame_counts.blocked=6`
- `frame_counts.killed=8`
- `sequence_validator_pass=true`
- `atlas_pack_pass=true`
- `atlas_validator_pass=true`
- `playback_proof_present=true`
- `final_release_human_gated=true`

The live receipt records a Tau generic DAG run with five nodes:

- `frame-lineage`
- `sequence-validation`
- `atlas-validation`
- `playback-proof`
- `release-boundary`

It also retains:

- per-frame lineage receipt
- sprite-atlas named-frame validation receipt
- packed runtime atlas PNG/JSON
- runtime atlas validation receipt
- playback proof
- human-gated release boundary receipt
- SQLite DAG run journal

## Focused Checks

Commands:

```bash
PYTHONPATH=src uv run python -m py_compile src/tau_coding/sprite_sheet_conformance.py src/tau_coding/cli.py
uv run ruff check src/tau_coding/sprite_sheet_conformance.py src/tau_coding/cli.py
PYTHONPATH=src uv run pytest tests/test_dag_runtime_scheduler.py tests/test_battle_adaptive_lineage_tau_contract.py
```

Results:

- `py_compile`: exit code 0
- `ruff`: exit code 0, `All checks passed!`
- `pytest`: exit code 0, `18 passed`

## Proof Scope

Proves:

- Tau ran a sprite-sheet workload through the canonical DAG scheduler.
- Six blocked and eight killed frames were generated with per-frame lineage.
- The real sprite-atlas validator accepted the named frame tree.
- The real sprite-atlas packer and runtime atlas validator accepted the atlas.
- Tau produced a playback proof bound to the runtime manifest.
- Final release remains human-gated and no promotion was performed.

Does not prove:

- Battle art direction quality.
- Provider/model semantic quality.
- Browser playback rendering in PixiJS.
- Human approval to promote the candidate runtime atlas.
