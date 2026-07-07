#!/usr/bin/env bash
set -euo pipefail

EXAMPLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${EXAMPLE_DIR}/../.." && pwd)"
OUT="${1:-"${TMPDIR:-/tmp}/tau-coding-reliability-basic"}"
WORK_REPO="${OUT}/work-repo"
GOAL_HASH="sha256:demo-coding-goal"

rm -rf "${OUT}"
mkdir -p "${WORK_REPO}/src" "${WORK_REPO}/tests" "${OUT}/receipts" "${OUT}/patches"

cat > "${WORK_REPO}/src/example.py" <<'PY'
def answer() -> int:
    return 41
PY

cat > "${WORK_REPO}/tests/test_example.py" <<'PY'
from src.example import answer


def test_answer() -> None:
    assert answer() == 42
PY

(
  cd "${WORK_REPO}"
  git init >/dev/null
  git config user.email tau-example@example.invalid
  git config user.name "Tau Example"
)

python3 - "${WORK_REPO}" "${OUT}/patches" "${GOAL_HASH}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

repo = Path(sys.argv[1])
patch_dir = Path(sys.argv[2])
goal_hash = sys.argv[3]
target = repo / "src" / "example.py"
before = target.read_text(encoding="utf-8")
after = before.replace("return 41", "return 42")

def sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

def write_patch(name: str, base_hash: str) -> None:
    payload = {
        "schema": "tau.code_patch.v1",
        "goal_hash": goal_hash,
        "target_file": "src/example.py",
        "base_file_sha256": base_hash,
        "allowed_paths": ["src/**", "tests/**"],
        "forbidden_paths": ["secrets/**"],
        "anchors": [{"kind": "symbol", "value": "answer"}],
        "patch": json.dumps([{"op": "replace", "old": "return 41", "new": "return 42"}]),
        "rationale": "Update the demo answer for the coding reliability example.",
        "expected_post_sha256": sha(after),
    }
    (patch_dir / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

write_patch("stale-code-patch.json", sha("def answer() -> int:\n    return 40\n"))
write_patch("valid-code-patch.json", sha(before))
PY

cd "${REPO_ROOT}"

if uv run tau code-patch \
  --patch "${OUT}/patches/stale-code-patch.json" \
  --repo "${WORK_REPO}" \
  --out "${OUT}/receipts/stale-code-patch-receipt.json" \
  --goal-hash "${GOAL_HASH}" \
  > "${OUT}/receipts/stale-code-patch.stdout.json"; then
  echo "expected stale code patch to fail closed" >&2
  exit 1
fi

uv run tau code-patch \
  --patch "${OUT}/patches/valid-code-patch.json" \
  --repo "${WORK_REPO}" \
  --out "${OUT}/receipts/valid-code-patch-receipt.json" \
  --goal-hash "${GOAL_HASH}" \
  > "${OUT}/receipts/valid-code-patch.stdout.json"

uv run tau lsp-diagnostics \
  --workspace "${WORK_REPO}" \
  --out "${OUT}/receipts/lsp-diagnostics-receipt.json" \
  > "${OUT}/receipts/lsp-diagnostics.stdout.json"

cat > "${OUT}/review-findings-pass.json" <<JSON
{
  "schema": "tau.review_findings.v1",
  "goal_hash": "${GOAL_HASH}",
  "reviewer": "reviewer",
  "verdict": "PASS",
  "findings": []
}
JSON

uv run tau review-findings \
  --findings "${OUT}/review-findings-pass.json" \
  --out "${OUT}/receipts/review-findings-pass-receipt.json" \
  --goal-hash "${GOAL_HASH}" \
  > "${OUT}/receipts/review-findings-pass.stdout.json"

cat > "${OUT}/review-findings-revise.json" <<JSON
{
  "schema": "tau.review_findings.v1",
  "goal_hash": "${GOAL_HASH}",
  "reviewer": "reviewer",
  "verdict": "REVISE",
  "findings": [
    {
      "id": "finding-001",
      "severity": "P1",
      "confidence": 0.87,
      "file": "src/example.py",
      "line": 2,
      "claim": "The patch needs a focused regression test before acceptance.",
      "evidence": ["tests/test_example.py"],
      "required_action": "revise"
    }
  ]
}
JSON

uv run tau review-findings \
  --findings "${OUT}/review-findings-revise.json" \
  --out "${OUT}/receipts/review-findings-revise-receipt.json" \
  --goal-hash "${GOAL_HASH}" \
  > "${OUT}/receipts/review-findings-revise.stdout.json"

cat > "${OUT}/review-findings-blocked.json" <<JSON
{
  "schema": "tau.review_findings.v1",
  "goal_hash": "${GOAL_HASH}",
  "reviewer": "reviewer",
  "verdict": "BLOCKED",
  "findings": [
    {
      "id": "finding-002",
      "severity": "P0",
      "confidence": 0.94,
      "file": "src/example.py",
      "line": 2,
      "claim": "The patch would skip the required policy gate.",
      "evidence": ["receipts/valid-code-patch-receipt.json"],
      "required_action": "block"
    }
  ]
}
JSON

uv run tau review-findings \
  --findings "${OUT}/review-findings-blocked.json" \
  --out "${OUT}/receipts/review-findings-blocked-receipt.json" \
  --goal-hash "${GOAL_HASH}" \
  > "${OUT}/receipts/review-findings-blocked.stdout.json"

python3 - "${OUT}/receipts" <<'PY'
import json
import sys
from pathlib import Path

receipts = Path(sys.argv[1])
expected = {
    "review-findings-pass-receipt.json": "PASS",
    "review-findings-revise-receipt.json": "REVISE",
    "review-findings-blocked-receipt.json": "BLOCKED",
}
observed = {}
for name, verdict in expected.items():
    payload = json.loads((receipts / name).read_text(encoding="utf-8"))
    observed[name] = payload.get("derived_verdict")
    if payload.get("ok") is not True or payload.get("derived_verdict") != verdict:
        raise SystemExit(
            f"{name} expected ok=true and derived_verdict={verdict}; got {observed[name]!r}"
        )
(receipts / "review-route-summary.json").write_text(
    json.dumps(
        {
            "schema": "tau.review_route_summary.v1",
            "ok": True,
            "status": "PASS",
            "routes": observed,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY

uv run tau commit-plan \
  --repo "${WORK_REPO}" \
  --out "${OUT}/receipts/commit-plan-receipt.json" \
  --evidence-receipt "${OUT}/receipts/valid-code-patch-receipt.json" \
  --evidence-receipt "${OUT}/receipts/lsp-diagnostics-receipt.json" \
  --evidence-receipt "${OUT}/receipts/review-findings-pass-receipt.json" \
  > "${OUT}/receipts/commit-plan.stdout.json"

python3 - "${OUT}" "${GOAL_HASH}" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
goal_hash = sys.argv[2]
receipts = out / "receipts"
dag = {
    "schema": "tau.dag_receipt.v1",
    "ok": True,
    "status": "PASS",
    "verdict": "PASS",
    "mocked": False,
    "live": True,
    "provider_live": False,
    "dag_id": "coding-reliability-basic",
    "active_goal_hash": goal_hash,
    "terminal_nodes": ["human"],
    "observed_edges": [
        {
            "from_agent": "coder",
            "from_node": "coder",
            "to_agent": "human",
            "to_node": "human",
        }
    ],
    "alerts": [],
    "artifacts": [
        str(receipts / "valid-code-patch-receipt.json"),
        str(receipts / "lsp-diagnostics-receipt.json"),
        str(receipts / "review-findings-pass-receipt.json"),
        str(receipts / "review-findings-revise-receipt.json"),
        str(receipts / "review-findings-blocked-receipt.json"),
        str(receipts / "review-route-summary.json"),
        str(receipts / "commit-plan-receipt.json"),
    ],
}
(out / "dag-receipt.json").write_text(
    json.dumps(dag, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

uv run tau orchestration-reliability \
  --dag-receipt "${OUT}/dag-receipt.json" \
  --out "${OUT}/receipts/orchestration-reliability-receipt.json" \
  > "${OUT}/receipts/orchestration-reliability.stdout.json"

python3 - "${OUT}" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
receipt_paths = sorted(str(path.relative_to(out)) for path in (out / "receipts").glob("*.json"))
summary = {
    "schema": "tau.coding_reliability_basic_demo_receipt.v1",
    "ok": True,
    "status": "PASS",
    "mocked": False,
    "live": True,
    "provider_live": False,
    "artifacts": receipt_paths,
    "proves": [
        "Tau blocked a stale hash-bound code patch.",
        "Tau applied a valid hash-bound exact replacement patch.",
        "Tau wrote local diagnostics, PASS/REVISE/BLOCKED review-findings, commit-plan, and orchestration reliability receipts.",
    ],
    "does_not_prove": [
        "Semantic code correctness.",
        "Agent truthfulness.",
        "Provider/model quality.",
        "Full DAG execution.",
        "GitHub mutation.",
        "Legal compliance.",
    ],
}
(out / "demo-receipt.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

cat "${OUT}/demo-receipt.json"
