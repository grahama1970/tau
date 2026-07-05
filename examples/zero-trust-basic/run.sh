#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
mkdir -p out

uv run tau zero-trust-doctor \
  --policy-profile policy-profile.json \
  --data-boundary data-boundary.json \
  --dag-contract dag.json \
  --receipt out/zero-trust-preflight-receipt.json

python3 - <<'PY'
import json
from pathlib import Path

receipt = json.loads(Path("out/zero-trust-preflight-receipt.json").read_text())
summary = {
    "schema": receipt.get("schema"),
    "ok": receipt.get("ok"),
    "status": receipt.get("status"),
    "mocked": receipt.get("mocked"),
    "live": receipt.get("live"),
    "provider_live": receipt.get("provider_live"),
    "alert_codes": receipt.get("alert_codes"),
}
print(json.dumps(summary, indent=2, sort_keys=True))
PY
