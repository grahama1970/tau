#!/usr/bin/env bash
set -euo pipefail

EXAMPLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${EXAMPLE_DIR}/../.." && pwd)"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-tau-zero-trust-basic"
RUN_DIR="${TMPDIR:-/tmp}/${RUN_ID}"
RECEIPT="${RUN_DIR}/zero-trust-preflight-receipt.json"

mkdir -p "${RUN_DIR}"

cd "${REPO_ROOT}"
uv run tau zero-trust-doctor \
  --policy-profile "${EXAMPLE_DIR}/policy-profile.json" \
  --data-boundary "${EXAMPLE_DIR}/data-boundary.json" \
  --dag-contract "${EXAMPLE_DIR}/dag-contract.json" \
  --receipt "${RECEIPT}" >/dev/null

jq '{
  receipt: "'"${RECEIPT}"'",
  schema,
  status,
  ok,
  mocked,
  live,
  provider_live,
  alert_count: (.alerts | length),
  does_not_prove: .proof_scope.does_not_prove
}' "${RECEIPT}"
