#!/usr/bin/env bash
set -euo pipefail

EXAMPLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${EXAMPLE_DIR}/../.." && pwd)"
OUT="${1:-"${TMPDIR:-/tmp}/tau-skill-composition-basic"}"
SKILLS_ROOT="${TAU_SKILLS_ROOT:-/home/graham/workspace/experiments/agent-skills/skills}"

rm -rf "${OUT}"
mkdir -p "${OUT}"

cd "${REPO_ROOT}"

uv run tau skill-capability-registry-default \
  --out "${OUT}/registry.json" \
  > "${OUT}/default.stdout.json"

uv run tau skill-capability-registry-validate \
  --registry "${OUT}/registry.json" \
  --out "${OUT}/validation-receipt.json" \
  --skills-root "${SKILLS_ROOT}" \
  > "${OUT}/validation.stdout.json"

python3 - "${OUT}" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
registry = json.loads((out / "registry.json").read_text(encoding="utf-8"))
validation = json.loads((out / "validation-receipt.json").read_text(encoding="utf-8"))
ok = (
    registry.get("schema") == "tau.skill_capability_registry.v1"
    and validation.get("schema") == "tau.skill_capability_registry_validation_receipt.v1"
    and validation.get("ok") is True
)
payload = {
    "schema": "tau.skill_composition_basic_example_receipt.v1",
    "ok": ok,
    "status": "PASS" if ok else "BLOCKED",
    "mocked": False,
    "live": False,
    "provider_live": False,
    "registry_schema": registry.get("schema"),
    "validation_schema": validation.get("schema"),
    "capability_count": validation.get("capability_count"),
    "skill_names": validation.get("skill_names", []),
    "registry_path": str((out / "registry.json").resolve()),
    "validation_receipt_path": str((out / "validation-receipt.json").resolve()),
    "proof_scope": {
        "proves": [
            "Tau generated a default skill capability registry.",
            "Tau validated that mapped skills exist under the configured skills root.",
            "Tau recorded Tau receipt schemas for capability admissibility.",
        ],
        "does_not_prove": [
            "Any skill was executed.",
            "Skill output semantic correctness.",
            "Adapter acceptance of native skill artifacts.",
            "Provider/model semantic quality.",
            "Future route correctness.",
        ],
    },
}
(out / "demo-receipt.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
print(json.dumps(payload, indent=2, sort_keys=True))
raise SystemExit(0 if ok else 1)
PY
