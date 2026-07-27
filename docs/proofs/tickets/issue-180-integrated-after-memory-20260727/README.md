# Tau #180 Integrated Snapshot After Memory #62

Issue: https://github.com/grahama1970/tau/issues/180

## Scope

Refresh the #180 integrated snapshot after Tau #150 and
graph-memory-operator#62 resolved the governed Memory chain path/hop contract.

## Commands

```text
uv run tau dag-template-catalog
uv run tau dag-view-capabilities --json
/home/graham/workspace/experiments/agent-skills/skills/ticket/run.sh lookup --repo grahama1970/tau --search "state:open" --limit 50
git ls-remote origin refs/heads/main
POST http://127.0.0.1:8601/recall {"q":"Tau memory first skill chain DAG setup","k":3,"brief":true}
```

## Receipts

- `catalog.json`
- `dag-view-capabilities.json`
- `open-tau-issues.json`
- `remote-main.txt`
- `memory-recall-brief.json`
- `summary.json`

## Observed Summary

```json
{
  "catalog_templates": 10,
  "viewer_read_only": true,
  "viewer_memory_provenance": "DEGRADED_WHEN_MEMORY_CHAIN_UNAVAILABLE",
  "memory_skill_chain": true,
  "memory_provenance": true,
  "memory_path": true,
  "memory_path_len": 4,
  "memory_hop": true,
  "memory_hop_count": 3
}
```

## Disposition

Memory provenance is no longer the blocker recorded in the prior #180 snapshot.

#180 remains open because this snapshot does not satisfy the issue's final
completion criteria: one clean-checkout browser session demonstrating
discover -> inspect -> compile -> run -> block/revise/approve -> resume ->
complete without manual reload, retained desktop/mobile captures, full Python
and web suite results, and final integrated product conformance.

mocked: no
live: yes
provider_live: no

Actually exercised: local Tau CLI, `$ticket` open-issue lookup, Git remote-ref
read, and live Memory `/recall` through `http://127.0.0.1:8601`.
