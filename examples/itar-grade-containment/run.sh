#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-"$ROOT/out"}"
rm -rf "$OUT"
mkdir -p "$OUT/package"

expect_blocked() {
  local name="$1"
  shift
  if "$@"; then
    echo "expected $name to fail closed, but it passed" >&2
    exit 1
  fi
}

expect_blocked "research-query-gate" \
  uv run tau research-query-gate \
    --query "Search this phrase: rotor actuator calibration detail alpha bravo charlie delta echo foxtrot" \
    --method brave-search \
    --policy-profile "$ROOT/policy-profile.json" \
    --data-boundary "$ROOT/data-boundary.json" \
    --authorization "$ROOT/research-query-authorization.json" \
    --controlled-artifact "$ROOT/controlled-artifact.txt" \
    --receipt "$OUT/research-query-safety-receipt.json" \
    > "$OUT/research-query-gate.stdout.json"

expect_blocked "itar-access-preflight" \
  uv run tau itar-access-preflight \
    --actor-manifest "$ROOT/actor-manifest-unverified.json" \
    --data-boundary "$ROOT/data-boundary.json" \
    --approval-packet "$ROOT/approval-packet.json" \
    --receipt "$OUT/itar-access-preflight-blocked-receipt.json" \
    > "$OUT/itar-access-preflight-blocked.stdout.json"

expect_blocked "docker-sandbox-check" \
  uv run tau docker-sandbox-check \
    --image python@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
    --docker-socket-mounted \
    --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
    --receipt "$OUT/docker-sandbox-blocked-receipt.json" \
    --command python --version \
    > "$OUT/docker-sandbox-blocked.stdout.json"

uv run tau itar-access-preflight \
  --actor-manifest "$ROOT/actor-manifest-verified.json" \
  --data-boundary "$ROOT/data-boundary.json" \
  --approval-packet "$ROOT/approval-packet.json" \
  --receipt "$OUT/itar-access-preflight-receipt.json" \
  > "$OUT/itar-access-preflight.stdout.json"

uv run tau docker-sandbox-check \
  --image python@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --receipt "$OUT/sandbox-run-receipt.json" \
  --command python --version \
  > "$OUT/docker-sandbox.stdout.json"

cp "$ROOT/data-boundary.json" "$OUT/package/data-boundary.json"
cp "$ROOT/policy-profile.json" "$OUT/package/policy-profile.json"
cp "$ROOT/actor-manifest-verified.json" "$OUT/package/actor-access-manifest.json"
cp "$OUT/itar-access-preflight-receipt.json" "$OUT/package/itar-access-preflight-receipt.json"
cp "$OUT/sandbox-run-receipt.json" "$OUT/package/sandbox-run-receipt.json"

python3 - "$OUT/package" <<'PY'
import json
import sys
from pathlib import Path

pkg = Path(sys.argv[1])
for name, schema in {
    "zero-trust-preflight-receipt.json": "tau.zero_trust_preflight_receipt.v1",
    "memory-intent-gate-receipt.json": "tau.memory_intent_gate_receipt.v1",
    "evidence-case-gate-receipt.json": "tau.evidence_case_gate_receipt.v1",
    "evidence-validation-receipt.json": "tau.evidence_validation_receipt.v1",
    "signed-receipt-verification.json": "tau.signed_receipt_verification.v1",
    "environment-manifest.json": "tau.environment_manifest.v1",
}.items():
    payload = {"schema": schema, "status": "PASS", "goal_hash": "sha256:demo"}
    if name == "signed-receipt-verification.json":
        payload["verified_count"] = 1
    (pkg / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
(pkg / "non-claims.md").write_text(
    "This package does not prove ITAR compliance or legal sufficiency.\n",
    encoding="utf-8",
)
PY

uv run tau compliance-package-validate "$OUT/package" \
  --receipt "$OUT/package-validation-receipt.json" \
  > "$OUT/package-validation.stdout.json"

uv run tau zero-trust-redteam --run-dir "$OUT/redteam" \
  > "$OUT/zero-trust-redteam.stdout.json"

python3 - "$OUT" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
summary = {
    "schema": "tau.itar_grade_containment_demo_receipt.v1",
    "status": "PASS",
    "ok": True,
    "mocked": False,
    "live": False,
    "provider_live": False,
    "artifacts": sorted(str(path.relative_to(out)) for path in out.rglob("*.json")),
    "does_not_prove": [
        "ITAR compliance",
        "legal identity",
        "live Docker isolation",
        "live provider execution",
        "GitHub mutation",
        "Memory sync"
    ]
}
(out / "demo-receipt.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

cat "$OUT/demo-receipt.json"
