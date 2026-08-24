# Sandboxed Python Workspace

Tau's sandboxed Python workspace is an optional mutable computation surface for
one authorized node attempt. It is working memory, not evidence authority:
exports become Tau output only after a normal artifact admission receipt.

The v1 Docker backend writes these contract schemas:

- `tau.python_workspace_request.v1`
- `tau.python_workspace_receipt.v1`
- `tau.python_execution_request.v1`
- `tau.python_execution_receipt.v1`
- `tau.python_workspace_snapshot.v1`
- `tau.python_package_manifest.v1`

The worker runs outside the scheduler process with Docker `--network none`,
read-only rootfs, dropped capabilities, non-root user, no provider credentials,
no host home mount, bounded stdout/stderr, bounded artifacts, a memory limit,
and a process limit. The package manifest records the pinned image digest and
the empty Tau environment allowlist.

Python values affect Tau only through explicit `tau_exports` plus
`tau.python_workspace_artifact_admission_receipt.v1`. A successful Python exit,
printed `PASS`, or live namespace value leaves `tau_admission_status:
not_admitted` until the output artifact hash is independently admitted.

Snapshots retain JSON-serializable namespace values and explicitly mark modules,
functions, and other unsupported values as non-restorable. Restore rejects stale
snapshots when the package manifest, goal, plan, attempt, policy, or data
boundary no longer matches.

Run the live proof:

```bash
uv run --project . python scripts/python-workspace-live-proof.py \
  --out artifacts/issue-317-agentic-eval/live-proof.json
```

Run the repeated eval:

```bash
/home/graham/workspace/experiments/agent-skills/skills/agentic-evals/run.sh \
  run evals/python_workspace_sandbox_agentic_eval.json \
  --output artifacts/issue-317-agentic-evals/report.json
```

Proof boundary: this proves the local Docker backend and backend-neutral
receipts. It does not prove Herdr-hosted workspace parity for issue #315, legal
compliance, provider/model behavior, or sandbox escape resistance against Docker
or kernel vulnerabilities.
