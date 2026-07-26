# Tau issue #140 proof notes

Ticket: <https://github.com/grahama1970/tau/issues/140>

Implemented Tau-side contract:

- `tau skill-chain-recall` writes `tau.skill_chain_selection_receipt.v1`.
- The receipt calls Graph Memory `/recall` with `brief: true`.
- A returned `skill_chain.skills` is the only PASS selection source.
- Missing Memory chain data degrades explicitly to one registry fallback skill.
- Prompt assembly can accept `selected_skill_names` so a Memory-selected chain can
  bound resident skill context without removing the existing full-catalog path.

Focused checks:

```text
uv run pytest -q tests/test_memory_acquisition.py tests/test_system_prompt.py
................
16 passed in 3.78s

uv run ruff check src/tau_coding/memory_acquisition.py src/tau_coding/cli.py src/tau_coding/system_prompt.py tests/test_memory_acquisition.py tests/test_system_prompt.py
All checks passed!

uv run python -m py_compile src/tau_coding/memory_acquisition.py src/tau_coding/cli.py src/tau_coding/system_prompt.py
exit 0
```

Live local Memory check:

```text
uv run tau skill-chain-recall --query "Tau issue 140 memory-backed skill selector should recommend ticket repair skill chain" --memory-url http://127.0.0.1:8601 --out docs/proofs/tickets/issue-140-memory-skill-selector-20260726/live-skill-chain-recall.json --fallback-skills-json '[{"name":"memory","description":"Memory-first prior lessons and skill chains"},{"name":"best-practices-github-ticket","description":"GitHub ticket workflow"},{"name":"checkpoint","description":"Persist restartable task evidence"}]' --timeout-seconds 10
```

Live receipt result:

- `schema`: `tau.skill_chain_selection_receipt.v1`
- `call.status_code`: `200`
- `found`: `true`
- `confidence`: `0.8`
- `status`: `DEGRADED`
- `alert_codes`: `["skill_chain_missing"]`
- `selection_source`: `registry_fallback`
- `selected_skills`: `["memory"]`

Additional Memory CLI checks:

```text
/home/graham/workspace/experiments/agent-skills/skills/memory/run.sh recall --q "Tau issue 140 memory-backed skill selector" --brief
keys ['confidence', 'found', 'items', 'should_scan']
skill_chain None

/home/graham/workspace/experiments/agent-skills/skills/memory/run.sh chain-recall "Tau issue 140 memory-backed skill selector" --limit 5
No matching chains found.
```

Interpretation:

- Fixture-backed tests exercise the PASS path where Memory returns a chain.
- The live local Memory service did not provide a chain for this query, so Tau
  produced a degraded receipt instead of a false PASS.
- This proves Tau's fail-closed contract and local integration behavior, not
  Memory corpus completeness or semantic optimality of the chosen chain.
