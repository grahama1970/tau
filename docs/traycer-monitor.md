# Traycer Offline Monitor

Traycer Slice 1 adds a deterministic offline monitor for one subagent run. It
does not inspect hidden chain-of-thought, steer live agents, validate a DAG, or
rewrite `tau.agent_handoff.v1`.

Run it with:

```bash
uv run tau traycer validate \
  --trace creator.trace.jsonl \
  --handoff creator-final-handoff.json \
  --active-goal-hash sha256:active-goal \
  --required-evidence required-evidence.json \
  --receipt monitor-receipt.json
```

Strict mode requires `--required-evidence` or `--start-handoff`. The final
handoff's `required_evidence` may be used only with
`--advisory-final-handoff-evidence`; receipts record that as
`evidence_authority: "final_handoff_fallback"`.

The command writes `tau.monitor_receipt.v1`. `PASS` and `WARN` allow normal
continuation. `REVIEW`, `REROUTE`, and `BLOCKED` set `ok:false` and return a
nonzero CLI exit status.

Slice 1 proves only offline JSONL and final handoff invariants:

- trace rows parse as JSONL;
- sequences are monotonic;
- trace and handoff goal hashes match the active goal hash;
- targets do not drift;
- required evidence ids are supported by evidence claim rows;
- the final handoff passes Tau's existing `tau.agent_handoff.v1` validator.

Later slices are separate commands: `chain-validate`, `dag-validate`, `tail`,
and `herdr-watch`.
