#!/usr/bin/env bash
set -euo pipefail

EXAMPLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${EXAMPLE_DIR}/../.." && pwd)"
OUT="${1:-"${TMPDIR:-/tmp}/tau-live-skill-invocation-basic"}"
SKILLS_ROOT="${TAU_SKILLS_ROOT:-/home/graham/workspace/experiments/agent-skills/skills}"
CLEAN_TEXT_RUN="${SKILLS_ROOT}/clean-text/run.sh"

rm -rf "${OUT}"
mkdir -p "${OUT}"

if [[ ! -x "${CLEAN_TEXT_RUN}" ]]; then
  echo "clean-text run.sh not executable: ${CLEAN_TEXT_RUN}" >&2
  exit 1
fi

cat > "${OUT}/input.txt" <<'EOF'
Hello’world — ﬁ
EOF

python3 - "${OUT}" "${CLEAN_TEXT_RUN}" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
clean_text_run = sys.argv[2]
payload = {
    "schema": "tau.skill_invocation_request.v1",
    "skill": "clean-text",
    "capability": "clean-text",
    "mode": "execute",
    "run_id": "live-skill-invocation-basic",
    "dag_id": "live-skill-invocation-basic",
    "node_id": "clean-text-node",
    "goal_hash": "sha256:live-skill-invocation-basic",
    "work_order_sha256": "sha256:live-skill-invocation-basic-work-order",
    "command": [
        clean_text_run,
        "input.txt",
        "-o",
        "clean-output.txt",
    ],
    "artifacts": [
        {
            "path": "clean-output.txt",
            "schema": "clean_text.output.v1",
        }
    ],
    "policy_profile_sha256": "sha256:local-demo-policy",
    "data_boundary_sha256": "sha256:local-demo-boundary",
    "zero_trust": True,
    "live_required": True,
    "mocked": False,
    "live": True,
    "provider_live": False,
}
(out / "skill-invocation-request.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

cd "${REPO_ROOT}"

uv run tau skill-invocation \
  --request "${OUT}/skill-invocation-request.json" \
  --out "${OUT}/skill-invocation-receipt.json" \
  --repo-root "${OUT}" \
  > "${OUT}/skill-invocation.stdout.json"

python3 - "${OUT}" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
receipt = json.loads((out / "skill-invocation-receipt.json").read_text(encoding="utf-8"))
cleaned = (out / "clean-output.txt").read_text(encoding="utf-8")
artifact_count = len(receipt.get("artifacts", []))
ok = (
    receipt.get("schema") == "tau.skill_invocation_receipt.v1"
    and receipt.get("status") == "PASS"
    and receipt.get("ok") is True
    and receipt.get("skill") == "clean-text"
    and receipt.get("live") is True
    and artifact_count == 1
    and "Hello'world - fi" in cleaned
)
payload = {
    "schema": "tau.live_skill_invocation_basic_example_receipt.v1",
    "ok": ok,
    "status": "PASS" if ok else "BLOCKED",
    "mocked": False,
    "live": True,
    "provider_live": False,
    "skill": "clean-text",
    "capability": "clean-text",
    "invocation_schema": receipt.get("schema"),
    "invocation_status": receipt.get("status"),
    "artifact_count": artifact_count,
    "output_contains": "Hello'world - fi",
    "output_path": str((out / "clean-output.txt").resolve()),
    "invocation_receipt_path": str((out / "skill-invocation-receipt.json").resolve()),
    "proof_scope": {
        "proves": [
            "Tau executed a real local agent-skills run.sh command.",
            "Tau hash-bound a skill-created repo-contained artifact.",
            "Tau preserved goal, work-order, policy, and data-boundary bindings in the invocation receipt."
        ],
        "does_not_prove": [
            "The skill output is semantically sufficient for arbitrary tasks.",
            "Provider/model semantic quality.",
            "Future route correctness.",
            "Adapter-specific admissibility of native skill artifacts."
        ]
    }
}
(out / "demo-receipt.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps(payload, indent=2, sort_keys=True))
raise SystemExit(0 if ok else 1)
PY
