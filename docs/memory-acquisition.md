# Memory Acquisition Receipts

Tau's memory/evidence gate validates supplied Memory artifacts before DAG
dispatch. The acquisition lane lets Tau own the preceding step as well: call
Graph Memory, store the observable response, hash the request/response, and
produce a receipt that another gate or reviewer can inspect.

## Commands

Acquire Memory intent:

```bash
uv run tau memory-intent \
  --query "Should Tau dispatch this DAG?" \
  --memory-url http://127.0.0.1:8601 \
  --out /tmp/tau-memory/memory-intent-acquisition.json
```

Create an evidence case from the captured intent response:

```bash
uv run tau evidence-case-create \
  --intent /tmp/tau-memory/memory-intent-acquisition-response.json \
  --memory-url http://127.0.0.1:8601 \
  --out /tmp/tau-memory/evidence-case-acquisition.json
```

## Receipts

`tau.memory_intent_acquisition_receipt.v1` records:

- Memory URL and endpoint;
- request hash;
- response artifact path and hash;
- observed response schema;
- HTTP call status;
- fail-closed alerts.

`tau.evidence_case_acquisition_receipt.v1` records the same information plus the
source intent path/hash.

## Boundary

These receipts prove:

- Tau made the observable Memory call.
- Tau wrote and hashed the request/response artifacts.
- Tau checked that Memory returned an intent or evidence-case shaped artifact.

They do not prove:

- Memory fact truth;
- evidence semantic completeness;
- DAG dispatch admissibility by themselves;
- provider/model semantic quality.

Run the memory/evidence gate after acquisition before dispatching a high-stakes
DAG.
