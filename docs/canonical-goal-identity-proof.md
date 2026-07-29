# Canonical Goal Identity Proof

Tau issue #253 requires goal identity to be a runtime invariant for canonical
DAG execution. The canonical goal identity proof launches all five canonical DAGs
and reads back the emitted receipts, progress files, and useful output artifacts.
It then runs one adversarial goal-drift control per canonical topology.

Run the proof:

```bash
uv run python scripts/prove-canonical-goal-identity.py \
  --repo . \
  --run-root /tmp/tau-canonical-goal-identity-proof
```

The aggregate receipt is written to:

```text
/tmp/tau-canonical-goal-identity-proof/canonical-goal-identity-proof-receipt.json
```

The proof reports `mocked: false` and `live: true`. It checks:

- every canonical positive run has a structured `goal_identity`;
- `goal_identity.goal_hash` matches `active_goal_hash`;
- `goal_identity.goal_version` matches `active_goal_version`;
- useful output artifacts preserve the active goal hash and version;
- each adversarial drift check blocks with `EVIDENCE_GOAL_HASH_MISMATCH`;
- rejected drift artifacts are listed for inspection.

This proof does not establish provider/model semantic quality, dynamic browser
rendering, or human acceptance of the full immutable Tau goal.
