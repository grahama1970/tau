# Tau #150 Memory Chain Path/Hop Retest

Issue: https://github.com/grahama1970/tau/issues/150

## Scope

Retest the only remaining failed #150 acceptance leg after
graph-memory-operator#62: governed Memory `/recall` chain products must expose
provenance plus an inspectable traversal path and hop count.

Earlier #150 clean-session proof is retained at:

- `docs/proofs/tickets/issue-150-memory-first-epic-20260727/`

That earlier proof recorded PASS for:

- bounded resident skill context;
- explicit Memory-down CLI degradation;
- explicit Memory-down TUI surface.

It recorded FAIL/DEGRADED only for:

- `graph_traversal_skill_chain`;
- `graph_traversal_tool_chain`.

This bundle retests those remaining live Memory-chain legs.

## Commands

```text
curl --max-time 5 http://127.0.0.1:8601/health
POST http://127.0.0.1:8601/recall {"q":"tau issue 150 memory skill chain proof","k":3,"brief":true}
POST http://127.0.0.1:8601/recall {"q":"tau issue 150 memory skill chain proof","k":3,"collections":["skill_chains"],"recommendation":"skill_chain"}
POST http://127.0.0.1:8601/recall {"q":"tau issue 150 memory tool chain proof","k":3,"collections":["tool_chains"],"recommendation":"tool_chain"}
```

## Receipts

- `health.json`
- `recall-brief.json`
- `recall-skill-chains.json`
- `recall-tool-chains.json`
- `summary.json`

## Observed Summary

```json
{
  "health.json": {
    "ok": true,
    "memory_db_connected": true
  },
  "recall-brief.json": {
    "found": true,
    "chain": "skill_chain",
    "has_provenance": true,
    "has_path": true,
    "path_len": 4,
    "has_hop_count": true,
    "hop_count": 3
  },
  "recall-skill-chains.json": {
    "found": true,
    "chain": "skill_chain",
    "has_provenance": true,
    "has_path": true,
    "path_len": 4,
    "has_hop_count": true,
    "hop_count": 3
  },
  "recall-tool-chains.json": {
    "found": true,
    "chain": "tool_chain",
    "has_provenance": true,
    "has_path": true,
    "path_len": 7,
    "has_hop_count": true,
    "hop_count": 6
  }
}
```

## Evidence Classification

mocked: no
live: yes
provider_live: no

Actually exercised: live Memory service `/health` and `/recall` through
`http://127.0.0.1:8601`.

Does not prove: full #72 runtime-hardening program completion or #180 final
integrated viewer/product conformance.
