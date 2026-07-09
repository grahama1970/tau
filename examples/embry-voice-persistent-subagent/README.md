# Embry Voice Persistent Subagent Example

This example shows how a project agent declares a persistent Embry Chatterbox
voice surface directly inside a Tau DAG node.

The important field is `nodes[].persistent_subagent`. The UI route may remain
open at `http://localhost:3002/#embry-voice`, but Tau still runs bounded DAG
ticks and requires `persistent_subagent_receipt` before the subagent output can
count.

Validate the contract shape by running:

```bash
uv run python - <<'PY'
from pathlib import Path
from tau_coding.project_dag import load_dag_contract_payload, validate_dag_contract

path = Path("examples/embry-voice-persistent-subagent/dag-contract.json")
validate_dag_contract(load_dag_contract_payload(path))
print(f"validated {path}")
PY
```

This example is a contract pattern. It does not start UX Lab, Chatterbox,
Memory, SciLLM, or Herdr.
