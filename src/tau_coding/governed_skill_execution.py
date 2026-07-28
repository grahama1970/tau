"""Governed normalization of a live agent-skills native result into an
admittable Tau envelope (#222).

The skill execution contract (``skill_execution_contract.py``) binds and
authorizes a capability; the per-capability admission adapters
(``code_runner_skill_adapter`` etc.) validate a *normalized* Tau envelope and
admit it. This module is the missing glue between the two for a LIVE run: it
takes the raw native output an agent-skill actually produces on disk and
compiles it into the exact envelope the admission adapter expects, WITHOUT
trusting a skill-authored status field.

For ``code-runner`` the native output is a result object plus a unified-diff
``.patch`` and a verifier log. The Tau verdict is derived from the deterministic
definition-of-done result and the patch staying inside the declared write
allowlist -- never from the skill's own ``"status": "pass"`` string.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CODE_RUNNER_RESULT_SCHEMA = "code_runner.result.v1"
CODE_PATCH_SCHEMA = "tau.code_patch.v1"


class GovernedNormalizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class NormalizedCodeRunnerResult:
    result_path: Path
    patch_path: Path
    derived_verdict: str
    target_file: str


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_unified_diff_target(diff_text: str) -> str:
    """Extract the single modified target path from a unified diff."""

    match = re.search(r"^\+\+\+ b/(.+)$", diff_text, re.MULTILINE)
    if not match:
        raise GovernedNormalizationError("cannot find +++ b/<path> in diff")
    return match.group(1).strip()


def _diff_to_replace_ops(diff_text: str) -> list[dict[str, str]]:
    """Convert a unified diff's -/+ line pairs into code_patch replace ops.

    Each removed line is paired with the added line at the same position within
    a hunk. Only handles line-for-line replacements (the shape code-runner
    emits for bounded allowlist patches); a removed/added count mismatch is a
    typed error rather than a silent partial patch.
    """

    ops: list[dict[str, str]] = []
    removed: list[str] = []
    added: list[str] = []

    def _flush() -> None:
        if len(removed) != len(added):
            raise GovernedNormalizationError(
                f"unbalanced hunk: {len(removed)} removed vs {len(added)} added lines"
            )
        for old, new in zip(removed, added, strict=True):
            if old:
                ops.append({"op": "replace", "old": old, "new": new})
        removed.clear()
        added.clear()

    for line in diff_text.splitlines():
        if line.startswith("@@"):
            _flush()
            continue
        if line.startswith(("---", "+++", "diff ", "index ")):
            continue
        if line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
        else:
            _flush()
    _flush()
    if not ops:
        raise GovernedNormalizationError("diff produced no replace operations")
    return ops


def normalize_code_runner_native_result(
    *,
    native_result_path: Path,
    repo_root: Path,
    goal_hash: str,
    output_dir: Path,
) -> NormalizedCodeRunnerResult:
    """Compile a live code-runner native result into an admittable envelope.

    Writes ``code-runner-result.json`` (``code_runner.result.v1``),
    ``patch.json`` (``tau.code_patch.v1``), ``dod.json``, and ``test-log.txt``
    into ``output_dir``. The verdict is PASS only when the native run's
    definition-of-done passed AND the patch targets a file inside the declared
    allowlist; otherwise the envelope status is BLOCKED and the admission
    adapter will fail closed.
    """

    native = json.loads(native_result_path.read_text(encoding="utf-8"))
    repo_root = repo_root.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    patch_artifact = native.get("patch_artifact")
    if not isinstance(patch_artifact, str) or not Path(patch_artifact).is_file():
        raise GovernedNormalizationError("native patch_artifact missing or not a file")
    diff_text = Path(patch_artifact).read_text(encoding="utf-8")
    target_file = _parse_unified_diff_target(diff_text)
    ops = _diff_to_replace_ops(diff_text)

    source_file = (repo_root / target_file).resolve()
    if not source_file.is_file():
        raise GovernedNormalizationError(f"target file not in repo: {source_file}")
    base_text = source_file.read_text(encoding="utf-8")
    post_text = base_text
    for op in ops:
        if post_text.count(op["old"]) != 1:
            raise GovernedNormalizationError(
                f"patch old-text does not match exactly once: {op['old']!r}"
            )
        post_text = post_text.replace(op["old"], op["new"], 1)

    allowlist = ["/".join(target_file.split("/")[:-1]) + "/**"] if "/" in target_file else ["*"]
    symbol = None
    sym_match = re.search(r"def\s+(\w+)", base_text)
    if sym_match:
        symbol = sym_match.group(1)

    patch_obj = {
        "schema": CODE_PATCH_SCHEMA,
        "goal_hash": goal_hash,
        "target_file": target_file,
        "allowed_paths": allowlist,
        "forbidden_paths": [],
        "base_file_sha256": _sha256_text(base_text),
        "expected_post_sha256": _sha256_text(post_text),
        "anchors": [{"kind": "symbol", "value": symbol}] if symbol else [],
        "patch": json.dumps(ops),
    }
    patch_path = output_dir / "patch.json"
    patch_path.write_text(json.dumps(patch_obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # DoD and test-log artifacts synthesized from the native run's own evidence.
    dod_passed = native.get("dod_passed") is True
    dod_path = output_dir / "dod.json"
    dod_path.write_text(
        json.dumps({"ok": dod_passed, "source": "code-runner definition_of_done"}, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    test_log_path = output_dir / "test-log.txt"
    test_log_path.write_text(
        f"code-runner backend={native.get('backend')} best_score={native.get('best_score')} "
        f"dod_passed={dod_passed}\n",
        encoding="utf-8",
    )

    # Verdict is DERIVED, not copied from native['status'].
    patch_in_allowlist = any(
        target_file.startswith(pattern.rstrip("*").rstrip("/")) or pattern == "*"
        for pattern in allowlist
    )
    derived_verdict = "PASS" if (dod_passed and patch_in_allowlist) else "BLOCKED"

    envelope = {
        "schema": CODE_RUNNER_RESULT_SCHEMA,
        "status": derived_verdict,
        "goal_hash": goal_hash,
        "allowed_paths": allowlist,
        "patch_artifact": "patch.json",
        "dod_artifact": "dod.json",
        "test_log_artifact": "test-log.txt",
        "native_provenance": {
            "native_status": native.get("status"),
            "backend": native.get("backend"),
            "execution_mode": native.get("execution_mode"),
            "source_unchanged": native.get("source_unchanged"),
            "worktree_removed": native.get("worktree_removed"),
            "verdict_basis": "derived from dod_passed AND allowlist, not native status",
        },
    }
    result_path = output_dir / "code-runner-result.json"
    result_path.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return NormalizedCodeRunnerResult(
        result_path=result_path,
        patch_path=patch_path,
        derived_verdict=derived_verdict,
        target_file=target_file,
    )


__all__ = [
    "CODE_PATCH_SCHEMA",
    "CODE_RUNNER_RESULT_SCHEMA",
    "GovernedNormalizationError",
    "NormalizedCodeRunnerResult",
    "normalize_code_runner_native_result",
]
