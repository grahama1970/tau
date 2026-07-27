# Issue #150 Memory Chain Retest After Graph Memory #61

This bundle retests Tau #150 after
`grahama1970/graph-memory-operator#61` was closed.

## Live Receipts

- `health.json`: Memory service at `http://127.0.0.1:8601` is healthy.
- `recall-brief.json`: default brief recall now returns a top-level
  `skill_chain`.
- `recall-skill-chains.json`: targeted `skill_chains` recall now returns a
  top-level `skill_chain`.
- `recall-tool-chains.json`: targeted `tool_chains` recall now returns a
  top-level `tool_chain`.

## Observed Fields

```text
health.json ok= True found= None has_skill_chain= False has_tool_chain= False items= 0
recall-brief.json ok= None found= True has_skill_chain= True has_tool_chain= False items= 3
recall-skill-chains.json ok= None found= True has_skill_chain= True has_tool_chain= False items= 1
recall-tool-chains.json ok= None found= True has_skill_chain= False has_tool_chain= True items= 1
```

Chain field audit:

```text
recall-brief.json skill_chain has provenance, skills, source, score
recall-skill-chains.json skill_chain has provenance, skills, source, score
recall-tool-chains.json tool_chain has provenance, tools, source, score
path None
hop_count None
hops None
traversal_path None
```

## Disposition

The #61 repair partially unblocks #150: top-level `skill_chain` and
`tool_chain` products now exist. #150 still cannot close because its acceptance
requires an inspectable traversal path, hop count, and provenance. Provenance is
present; traversal path and hop count are still absent.

Filed remaining upstream blocker:
`https://github.com/grahama1970/graph-memory-operator/issues/62`

mocked: no
live: yes for Memory HTTP calls against `http://127.0.0.1:8601`
provider_live: no

This proof does not modify Tau runtime code. It updates #150's blocker from
missing chain objects to missing chain path/hop provenance fields.
