"""Human-acceptance bundle generator (#217, ratified by the acceptance panel).

Emits the artifact set a human uses to run rungs 1-5 from a clean installed
wheel and sign an acceptance that closes #180. The generator emits QUESTIONS,
never answers: every observation is unchecked and carries an explicit
falsifier; ACCEPTANCE.json is unsigned with a blank signature block. The
signature (ssh-keygen -Y sign, human-only) binds
``sha256(commit || wheel || sorted receipt hashes || observations)`` so a
record cannot be re-pointed at a later build.

Determinism: the generator refuses a dirty tree and produces byte-identical
output for the same commit, so ``verify_bundle.sh`` can detect drift.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

ACCEPTANCE_BUNDLE_SCHEMA = "tau.acceptance_bundle.v1"

# Each rung: id, topology, and the four walkthrough beats. FALSIFIER names what
# a human would see if Tau were lying; two rungs carry an explicit negative
# path that must visibly FAIL correctly.
_RUNGS = [
    {
        "rung": 1, "workflow": "repository-readiness", "topology": "LINEAR",
        "prepare": (
            "tau workflows run repository-readiness --repo <repo> --run-dir <dir> --goal '<goal>'"
        ),
        "observe": "Terminal prints status PASS; the viewer shows exactly one node reaching green.",
        "falsifier": (
            "Any node shows green before its receipt file exists on disk. Log as defect; do not "
            "tick."
        ),
        "record": "Paste the run-dir and receipts/*.json paths.",
        "negative": "Omit --goal: the command must EXIT NON-ZERO naming --goal as required.",
    },
    {
        "rung": 2, "workflow": "tau-operator-reference", "topology": "MULTI_STEP_SEQUENTIAL",
        "prepare": "tau workflows run tau-operator-reference --repo <tau-checkout> --run-dir <dir>",
        "observe": "The viewer shows a linear chain of >=2 nodes advancing in order; status PASS.",
        "falsifier": "A later node greens before its predecessor's receipt is admitted.",
        "record": "Paste the node receipt paths in order.",
        "negative": None,
    },
    {
        "rung": 3, "workflow": "repository-evidence-map", "topology": "FAN_OUT_FAN_IN",
        "prepare": (
            "tau workflows run repository-evidence-map --repo <repo> --run-dir <dir> --goal "
            "'<goal>'"
        ),
        "observe": "The viewer shows a diamond: parallel nodes fan into one join; status PASS.",
        "falsifier": "The join greens while a sibling receipt is still absent.",
        "record": "Paste every sibling receipt and the join receipt.",
        "negative": None,
    },
    {
        "rung": 4, "workflow": "approved-release-bundle", "topology": "MIXED_RETRY_APPROVAL",
        "prepare": "tau workflows run approved-release-bundle --repo <repo> --run-dir <dir> "
                   "--goal '<goal>' --publish-path <pub>",
        "observe": (
            "The run HALTS at an approval gate showing the exact prompt; approving completes it."
        ),
        "falsifier": "The publish path is written before you approve.",
        "record": "Paste the approval-gate receipt and the published bundle hash.",
        "negative": (
            "Type 'no' (or decline) at the approval gate: the run must ABORT with nothing "
            "published."
        ),
    },
    {
        "rung": 5, "workflow": "durable-repository-qualification",
        "topology": "DURABLE_MIXED_REPAIR_APPROVAL",
        "prepare": (
            "tau workflows run durable-repository-qualification --repo <repo> "
            "--run-dir <dir> --goal '<goal>' --publish-path <pub>"
        ),
        "observe": (
            "The run BLOCKS at a deliberate failure; you watch it in the "
            "ALREADY-OPEN viewer, run --repair --resume, the resume ENTERS AT "
            "THE BLOCKED NODE (not step 0), and the published effect count "
            "stays 1 (not doubled)."
        ),
        "falsifier": (
            "The blocked run silently reports PASS, or resume restarts from "
            "step 0, or the publish effect count doubles after repair."
        ),
        "record": (
            "Paste the blocked receipt, the repair attempt receipt, and the "
            "publication ledger (effect_count must read 1)."
        ),
        "negative": None,
    },
]


@dataclass(frozen=True)
class BundleResult:
    directory: Path
    bundle_digest: str


class AcceptanceBundleError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _observations() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for spec in _RUNGS:
        rows.append({
            "rung": spec["rung"], "kind": "positive",
            "observe": spec["observe"], "falsifier": spec["falsifier"],
            "checked": False,
        })
        if spec["negative"]:
            rows.append({
                "rung": spec["rung"], "kind": "negative",
                "observe": spec["negative"],
                "falsifier": "The negative path SUCCEEDS or fails silently instead of the "
                             "documented visible failure.",
                "checked": False,
            })
    return rows


def _walkthrough_markdown() -> str:
    lines = [
        "# Tau Acceptance Walkthrough",
        "",
        "Run each rung from the CLEAN INSTALLED WHEEL (env -i, throwaway HOME).",
        "For every beat: PREPARE (exact command), OBSERVE (truthful behavior),",
        "FALSIFIER (what lying looks like - log as a defect, never tick), RECORD.",
        "",
    ]
    for spec in _RUNGS:
        lines += [
            f"## Rung {spec['rung']}: {spec['workflow']} ({spec['topology']})",
            "",
            f"- PREPARE: `{spec['prepare']}`",
            f"- OBSERVE: {spec['observe']}",
            f"- FALSIFIER: {spec['falsifier']}",
            f"- RECORD: {spec['record']}",
        ]
        if spec["negative"]:
            lines.append(f"- NEGATIVE PATH: {spec['negative']}")
        lines.append("")
    lines += [
        "## Signing",
        "",
        "When every positive observation is confirmed and every negative path",
        "failed correctly, sign ACCEPTANCE.json with your own key:",
        "",
        "    ssh-keygen -Y sign -n tau-acceptance -f ~/.ssh/id_ed25519 ACCEPTANCE.json",
        "",
        "Then close #180 citing the record path, its bundle_digest, and the",
        "`ssh-keygen -Y verify` output. #72 closes separately on hardening evidence.",
        "",
    ]
    return "\n".join(lines)


_VERIFY_BUNDLE_SH = """#!/usr/bin/env bash
# verify_bundle.sh (#217): recompute every hash the manifest binds and exit
# non-zero on any drift. Run from inside the bundle directory.
set -euo pipefail
cd "$(dirname "$0")"
python3 - <<'PY'
import hashlib, json, sys
from pathlib import Path
manifest = json.loads(Path("BUILD_MANIFEST.json").read_text())
def digest(p):
    return "sha256:" + hashlib.sha256(Path(p).read_bytes()).hexdigest()
drift = []
for name, expected in manifest["bound_files"].items():
    actual = digest(name)
    if actual != expected:
        drift.append(f"{name}: {actual} != {expected}")
if drift:
    print("BUNDLE DRIFT:\\n" + "\\n".join(drift)); sys.exit(1)
print("bundle verified: all bound hashes match")
PY
"""


def generate_acceptance_bundle(
    *,
    repo: Path,
    wheel: Path,
    output_dir: Path,
    receipt_paths: list[Path] | None = None,
) -> BundleResult:
    """Emit the acceptance bundle. Refuses a dirty tree; deterministic per commit."""

    repo = repo.resolve()
    if _git(repo, "status", "--porcelain"):
        raise AcceptanceBundleError("refusing to build an acceptance bundle from a dirty tree")
    if not wheel.is_file():
        raise AcceptanceBundleError(f"wheel not found: {wheel}")
    commit = _git(repo, "rev-parse", "HEAD")
    output_dir.mkdir(parents=True, exist_ok=True)

    walkthrough = _walkthrough_markdown()
    observations = _observations()
    observations_text = json.dumps(observations, indent=2, sort_keys=True) + "\n"
    wheel_sha = _sha256_file(wheel)
    receipt_hashes = sorted(_sha256_file(p) for p in (receipt_paths or []))

    bundle_digest = _sha256_text(
        commit + "\n" + wheel_sha + "\n" + "\n".join(receipt_hashes)
        + "\n" + _sha256_text(observations_text)
    )

    (output_dir / "WALKTHROUGH.md").write_text(walkthrough, encoding="utf-8")
    (output_dir / "OBSERVATIONS.json").write_text(observations_text, encoding="utf-8")
    (output_dir / "DEFECT_LOG.md").write_text(
        "# Defect Log\n\n| rung | beat | expected | actual | severity | run-dir |\n"
        "| --- | --- | --- | --- | --- | --- |\n", encoding="utf-8"
    )
    verify_path = output_dir / "verify_bundle.sh"
    verify_path.write_text(_VERIFY_BUNDLE_SH, encoding="utf-8")
    verify_path.chmod(0o755)
    acceptance = {
        "schema": "tau.acceptance_record.v1",
        "commit": commit,
        "wheel_sha256": wheel_sha,
        "receipt_hashes": receipt_hashes,
        "observations_sha256": _sha256_text(observations_text),
        "bundle_digest": bundle_digest,
        "decision": None,
        "signature": None,
        "signer": None,
        "signed_at": None,
    }
    (output_dir / "ACCEPTANCE.json").write_text(
        json.dumps(acceptance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    bound = {
        name: _sha256_file(output_dir / name)
        for name in ("WALKTHROUGH.md", "OBSERVATIONS.json", "verify_bundle.sh", "ACCEPTANCE.json")
    }
    manifest = {
        "schema": ACCEPTANCE_BUNDLE_SCHEMA,
        "commit": commit,
        "wheel": str(wheel),
        "wheel_sha256": wheel_sha,
        "bundle_digest": bundle_digest,
        "bound_files": bound,
        "rungs": [s["rung"] for s in _RUNGS],
    }
    (output_dir / "BUILD_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return BundleResult(directory=output_dir, bundle_digest=bundle_digest)


__all__ = [
    "ACCEPTANCE_BUNDLE_SCHEMA",
    "AcceptanceBundleError",
    "BundleResult",
    "generate_acceptance_bundle",
]
