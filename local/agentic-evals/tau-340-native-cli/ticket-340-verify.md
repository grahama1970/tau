# Issue 340 Verification Proof

mocked: no
live: no
What was exercised: local deterministic commands supplied to `ticket verify`.
What remains unverified: remote GitHub issue state and any live service not covered by commands.

## Command: `uv run ruff check src/tau_coding/dag_runtime/native_agent_dispatch.py src/tau_coding/dag_runtime/agent_node_adapter.py src/tau_coding/generic_dag.py tests/test_generic_dag_tau_agent_dispatch.py scripts/agentic-eval-tau-native-cli-dispatch.py`

Exit code: 0

### stdout
```text
All checks passed!
```

### stderr
```text
Using CPython 3.14.3
Removed virtual environment at: /tmp/agent-skills-ticket-venv
Creating virtual environment at: /tmp/agent-skills-ticket-venv
Installed 36 packages in 59ms
```

## Command: `uv run pytest --tau-suite=all -q -p no:cacheprovider tests/test_generic_dag_tau_agent_dispatch.py tests/test_generic_dag.py tests/test_agent_node_scheduler_adapter.py`

Exit code: 0

### stdout
```text
..........................................                               [100%]
42 passed in 8.49s
```

### stderr
```text

```

## Command: `python3 -c "import json;p=json.load(open('local/agentic-evals/tau-340-native-cli/proof.json'));r=p['live_reviewer'];assert p['status']=='PASS' and p['live'] and not p['mocked'] and r['provider_invoked'] and r['nonce_in_final_text'] and r['nonce_in_tool_effect_payload'] and all(v['provider_invoked'] is False and v['status']=='BLOCKED' for v in p['negative_cases'].values());print('proof.json PASS', r['transport_profile']['profile_id'], r['nonce'])"`

Exit code: 0

### stdout
```text
proof.json PASS claude-model-turn nonce-d69579e968dcf147
```

### stderr
```text

```

## Command: `python3 -c "import json;d=json.load(open('local/agentic-evals/tau-340-native-cli/agentic-evals-report-20260906T1737Z.json'));assert d['readiness']=='READY' and d['live'] and not d['mocked'] and d['trial_count']==4;print('agentic-evals READY 4 trials live')"`

Exit code: 0

### stdout
```text
agentic-evals READY 4 trials live
```

### stderr
```text

```
