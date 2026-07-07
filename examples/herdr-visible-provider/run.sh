#!/usr/bin/env bash
set -euo pipefail

EXAMPLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${EXAMPLE_DIR}/../.." && pwd)"
LABEL="${TAU_EXAMPLE_LABEL:-tau-herdr-visible-provider-example-$(date -u +%Y%m%dT%H%M%SZ)}"
CLEANUP_MODE="${TAU_PROVIDER_CLEANUP_MODE:-apply}"
OUT="${1:-}"
if [[ -n "${OUT}" ]]; then
  mkdir -p "${OUT}"
  RUN_ROOT="${OUT}/proofs"
  STDOUT_JSON="${OUT}/real-world-sanity.stdout.json"
else
  RUN_ROOT="${REPO_ROOT}/experiments/goal-locked-subagents/proofs/real-world-sanity"
  STDOUT_JSON="/tmp/tau-herdr-visible-provider-example.stdout.json"
fi

cd "${REPO_ROOT}"
scripts/run-real-world-sanity.py \
  --run-root "${RUN_ROOT}" \
  --levels advanced \
  --checks advanced.provider_readiness \
  --label "${LABEL}" \
  --receipt-timeout-seconds 300 \
  --provider-cleanup-mode "${CLEANUP_MODE}" >"${STDOUT_JSON}"

RECEIPT="$(jq -r '.run_dir + "/real-world-sanity-receipt.json"' "${STDOUT_JSON}")"
SUMMARY="$(jq '{
  schema: "tau.herdr_visible_provider_example_receipt.v1",
  receipt: "'"${RECEIPT}"'",
  status,
  ok,
  check_count,
  failed_check_count,
  mocked,
  live,
  provider_live,
  level_counts
}' "${RECEIPT}")"

if [[ -n "${OUT}" ]]; then
  python3 - "${OUT}" "${SUMMARY}" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
summary = json.loads(sys.argv[2])
summary["artifacts"] = sorted(
    str(path.relative_to(out))
    for path in out.rglob("*.json")
    if path.name != "demo-receipt.json"
)
(out / "demo-receipt.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps(summary, indent=2, sort_keys=True))
PY
else
  printf '%s\n' "${SUMMARY}"
fi
