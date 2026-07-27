# Issue 184 Memory Provenance Proof

This proof demonstrates that Tau consumes governed Graph Memory chain
provenance and fails closed when the chain contract is missing or invalid.

## Commands

```bash
/home/graham/workspace/experiments/agent-skills/skills/memory/run.sh recall --q "Tau Memory provenance recall-chain skill_chain tool_chain viewer provenance hop count" --brief
PYTHONPATH=src uv run python -m py_compile src/tau_coding/memory_acquisition.py src/tau_coding/memory_provenance_proof.py src/tau_coding/cli.py
uv run ruff check src/tau_coding/memory_acquisition.py src/tau_coding/memory_provenance_proof.py src/tau_coding/cli.py tests/test_memory_acquisition.py
PYTHONPATH=src uv run pytest -q tests/test_memory_acquisition.py
PYTHONPATH=src uv run tau memory-provenance-proof --allow-live-memory --output docs/proofs/tickets/issue-184-memory-provenance-proof-20260727/memory-provenance-proof.json
jq '{status,mocked,live,provider_live,populated_path,memory_down_degraded,invalid_chain_blocked,hop_count:.live_skill_chain.hop_count,viewer_html:.viewer_artifact.html,viewer_screenshot:.viewer_artifact.screenshot}' docs/proofs/tickets/issue-184-memory-provenance-proof-20260727/memory-provenance-proof.json
```

## Artifacts

- `memory-provenance-proof.json`: aggregate live proof receipt.
- `populated-skill-chain-selection.json`: live Memory `/recall` skill-chain receipt.
- `invalid-skill-chain-selection.json`: loopback invalid-chain receipt blocked by Tau validation.
- `memory-down-skill-chain-selection.json`: Memory-down degraded receipt.
- `memory-provenance-viewer.html`: browser-readable provenance artifact.
- `memory-provenance-viewer.png`: Chrome screenshot of the provenance artifact.

## Readback

```json
{
  "status": "PASS",
  "mocked": false,
  "live": true,
  "provider_live": false,
  "populated_path": true,
  "memory_down_degraded": true,
  "invalid_chain_blocked": true,
  "hop_count": 3
}
```

## Visual Inspection

The screenshot visibly shows the populated Memory path:

```text
memory -> ticket -> checkpoint -> recommend-skill-chain
```

It also shows the invalid chain blocked with `skill_chain_missing` and the
Memory-down case blocked with `memory_http_error, memory_recall_unavailable`.

## Boundary

This proves Tau consumes Memory's governed chain path and hop-count fields
instead of inferring a workflow. It does not prove Memory facts are true, that
the selected chain is semantically optimal, or that Tau writes anything back to
Memory.
