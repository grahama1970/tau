# Issue 191 Targeted Repair Conformance Proof

Ticket: grahama1970/tau#191

## Live E2E Gate

Command:

```bash
PYTHONPATH=src uv run tau targeted-repair-conformance --allow-live-filesystem --output docs/proofs/tickets/issue-191-targeted-repair-conformance-v2-20260727/targeted-repair-conformance.json
```

Result: exit code 0.

Receipt:

- `docs/proofs/tickets/issue-191-targeted-repair-conformance-v2-20260727/targeted-repair-conformance.json`
- `status=PASS`
- `mocked=false`
- `live=true`
- `provider_live=false`
- `failed_checks=[]`
- `changed_target={"state":"killed","frame_index":3,"relative_path":"killed/003.png"}`
- `unaffected_accepted_frame_regeneration_count=0`
- `changed_frame_hash_changed=true`
- `unaffected_frames_reused=true`
- `downstream_atlas_rebuilt=true`
- `downstream_playback_rebuilt=true`
- `sequence_validator_pass=true`
- `atlas_validator_pass=true`
- `final_release_human_gated=true`

The live receipt records a Tau generic DAG run with five nodes:

- `baseline-acceptance`
- `targeted-repair`
- `downstream-rebuild`
- `lineage-readback`
- `release-boundary`

The proof bundle retains baseline and repaired frame trees, targeted repair plan,
lineage readback, sequence validation, atlas validation, playback proof, release
boundary, and the SQLite DAG journal.

## Focused Checks

Commands:

```bash
PYTHONPATH=src uv run python -m py_compile src/tau_coding/targeted_repair_conformance.py src/tau_coding/sprite_sheet_conformance.py src/tau_coding/cli.py
uv run ruff check src/tau_coding/targeted_repair_conformance.py src/tau_coding/sprite_sheet_conformance.py src/tau_coding/cli.py
PYTHONPATH=src uv run pytest tests/test_dag_runtime_scheduler.py tests/test_battle_adaptive_lineage_tau_contract.py
```

Results:

- `py_compile`: exit code 0
- `ruff`: exit code 0, `All checks passed!`
- `pytest`: exit code 0, `18 passed`

## Proof Scope

Proves:

- Tau ran a targeted repair workload through the canonical DAG scheduler.
- Exactly one changed frame target was regenerated.
- Unaffected accepted frames were reused byte-for-byte with regeneration count zero.
- Sequence, atlas, and playback outputs were invalidated and rebuilt.
- Lineage readback proves reuse for unaffected accepted frames.

Does not prove:

- Battle art direction quality.
- Provider/model semantic quality.
- Automatic selection of the correct human repair target.
- Human approval to promote the rebuilt candidate atlas.
