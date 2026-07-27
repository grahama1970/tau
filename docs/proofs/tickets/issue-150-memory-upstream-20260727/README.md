# Issue #150 Upstream Memory Chain Repro

This bundle captures why Tau issue #150 remains blocked on Graph Memory rather
than Tau parsing.

## Contract Checked

`/home/graham/workspace/experiments/agent-skills/skills/memory/SKILL.md` says
`/recall` brief mode returns ordinary recall fields plus the best matching
top-level `skill_chain` from the `skill_chains` collection. Tau #150 requires
Tau to read that traversal-sourced chain receipt back from Memory and expose
path/provenance.

## Live Repro

Generated against `http://127.0.0.1:8601` on 2026-07-27:

- `health.json`: Memory service was healthy and connected.
- `recall_brief_default.json`: `/recall` with `brief: true` returned ordinary
  `items` but no top-level `skill_chain`.
- `recall_skill_chains_targeted.json`: targeted `collections:
  ["skill_chains"]` with `recommendation: "skill_chain"` returned no items and
  no top-level `skill_chain`.
- `recall_tool_chains_targeted.json`: targeted `collections: ["tool_chains"]`
  with `recommendation: "tool_chain"` returned no items and no top-level
  `tool_chain`.
- `list_skill_chains.json`: `/list` showed `skill_chains` exists but is empty
  (`total: 0`).
- `list_tool_chains.json`: `/list` returned 404 because `tool_chains` does not
  exist.

## Result

Tau should keep failing closed for chain-backed #150 proof. The missing upstream
state is concrete: Graph Memory does not currently provide the documented
`skill_chain`/`tool_chain` recall contract that Tau needs to accept a PASS
receipt.
