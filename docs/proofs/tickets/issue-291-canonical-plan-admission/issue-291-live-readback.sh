#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
PROOF_DIR="$ROOT/docs/proofs/tickets/issue-291-canonical-plan-admission"
RECEIPT_DIR="$PROOF_DIR/live-run-canonical-with-spec"
RECEIPT="$RECEIPT_DIR/dag-receipt.json"

set +e
uv run tau dag-run \
  "$PROOF_DIR/dead-end-project-dag.json" \
  --receipt-dir "$RECEIPT_DIR" \
  --agents-root "$PROOF_DIR/agents" \
  --scheduler bounded-ready-queue \
  --no-resume
exit_code=$?
set -e

if [[ "$exit_code" -eq 0 ]]; then
  echo "expected invalid DAG plan to return non-zero BLOCKED status" >&2
  exit 1
fi

jq -e '
  .schema == "tau.dag_receipt.v1"
  and .status == "BLOCKED"
  and .verdict == "node_dead_end"
  and .ok == false
  and .mocked == false
  and .live == true
  and .provider_live == false
  and .command_executed == false
  and (.selected_agents | length) == 0
  and (.dispatches | length) == 0
  and .dag_plan_sha256 == null
  and .dag_plan_validation.schema == "tau.dag_plan_validation.v1"
  and .dag_plan_validation.ok == false
  and .dag_plan_validation.codes[0] == "node_dead_end"
' "$RECEIPT" >/dev/null

echo "issue-291 live readback PASS: $RECEIPT"
