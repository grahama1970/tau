#!/usr/bin/env bash
set -euo pipefail

EXAMPLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${EXAMPLE_DIR}/../.." && pwd)"
LABEL="${TAU_EXAMPLE_LABEL:-tau-herdr-visible-provider-example-$(date -u +%Y%m%dT%H%M%SZ)}"
CLEANUP_MODE="${TAU_PROVIDER_CLEANUP_MODE:-apply}"

cd "${REPO_ROOT}"
scripts/run-real-world-sanity.py \
  --levels advanced \
  --checks advanced.provider_readiness \
  --label "${LABEL}" \
  --receipt-timeout-seconds 300 \
  --provider-cleanup-mode "${CLEANUP_MODE}" >/tmp/tau-herdr-visible-provider-example.stdout.json

RECEIPT="$(jq -r '.run_dir + "/real-world-sanity-receipt.json"' /tmp/tau-herdr-visible-provider-example.stdout.json)"
jq '{
  receipt: "'"${RECEIPT}"'",
  status,
  ok,
  check_count,
  failed_check_count,
  mocked,
  live,
  provider_live,
  level_counts
}' "${RECEIPT}"
