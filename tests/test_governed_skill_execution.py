"""Tests for governed normalization of live skill output into an admittable
Tau envelope (#222)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tau_coding.code_runner_skill_adapter import write_code_runner_skill_adapter_receipt
from tau_coding.governed_skill_execution import (
    GovernedNormalizationError,
    normalize_code_runner_native_result,
)

_GOAL = "sha256:" + "a" * 64
_DIFF = """diff --git a/src/app.py b/src/app.py
index 8d96fe4..fe81f17 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
 def add_one(x):
-    return x + 2
+    return x + 1
"""


def _fixture(tmp_path: Path, *, dod_passed: bool = True) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("def add_one(x):\n    return x + 2\n", encoding="utf-8")
    patch = tmp_path / "native.patch"
    patch.write_text(_DIFF, encoding="utf-8")
    native = {
        "task_id": "t", "status": "pass", "dod_passed": dod_passed,
        "backend": "codex", "best_score": 1.0, "execution_mode": "isolated_worktree",
        "source_unchanged": True, "worktree_removed": True,
        "patch_artifact": str(patch),
    }
    native_path = tmp_path / "native-result.json"
    native_path.write_text(json.dumps(native), encoding="utf-8")
    return repo, native_path


def _admit(repo: Path, result_path: Path, outdir: Path) -> dict:
    env = json.loads(result_path.read_text())
    for key in ("patch_artifact", "dod_artifact", "test_log_artifact"):
        env[key] = str((outdir / env[key]).relative_to(repo))
    result_path.write_text(json.dumps(env, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return write_code_runner_skill_adapter_receipt(
        result_path=result_path, output_path=outdir / "admission.json",
        repo_root=repo, expected_goal_hash=_GOAL,
    )


def test_normalized_live_diff_admits(tmp_path: Path) -> None:
    repo, native_path = _fixture(tmp_path)
    outdir = repo / ".tau-admission"
    norm = normalize_code_runner_native_result(
        native_result_path=native_path, repo_root=repo, goal_hash=_GOAL, output_dir=outdir
    )
    assert norm.derived_verdict == "PASS"
    assert norm.target_file == "src/app.py"
    receipt = _admit(repo, norm.result_path, outdir)
    assert receipt["status"] == "PASS"
    assert receipt["code_patch_receipt_status"] == "PASS"
    assert receipt["errors"] == []


def test_verdict_is_derived_from_dod_not_native_status(tmp_path: Path) -> None:
    # native says "pass" but the deterministic DoD did NOT pass -> BLOCKED
    repo, native_path = _fixture(tmp_path, dod_passed=False)
    outdir = repo / ".tau-admission"
    norm = normalize_code_runner_native_result(
        native_result_path=native_path, repo_root=repo, goal_hash=_GOAL, output_dir=outdir
    )
    assert norm.derived_verdict == "BLOCKED"
    env = json.loads(norm.result_path.read_text())
    assert env["native_provenance"]["native_status"] == "pass"
    assert env["status"] == "BLOCKED"


def test_patch_outside_allowlist_is_rejected_on_admission(tmp_path: Path) -> None:
    repo, native_path = _fixture(tmp_path)
    outdir = repo / ".tau-admission"
    norm = normalize_code_runner_native_result(
        native_result_path=native_path, repo_root=repo, goal_hash=_GOAL, output_dir=outdir
    )
    env = json.loads(norm.result_path.read_text())
    env["allowed_paths"] = ["docs/**"]
    patch = json.loads((outdir / "patch.json").read_text())
    patch["allowed_paths"] = ["docs/**"]
    (outdir / "patch.json").write_text(json.dumps(patch, indent=2, sort_keys=True) + "\n")
    for key in ("patch_artifact", "dod_artifact", "test_log_artifact"):
        env[key] = str((outdir / Path(env[key]).name).relative_to(repo))
    norm.result_path.write_text(json.dumps(env, indent=2, sort_keys=True) + "\n")
    receipt = write_code_runner_skill_adapter_receipt(
        result_path=norm.result_path, output_path=outdir / "neg.json",
        repo_root=repo, expected_goal_hash=_GOAL,
    )
    assert receipt["status"] == "BLOCKED"
    assert any("outside allowed_paths" in e for e in receipt["errors"])


def test_base_post_sha_binding_is_deterministic(tmp_path: Path) -> None:
    repo, native_path = _fixture(tmp_path)
    norm = normalize_code_runner_native_result(
        native_result_path=native_path, repo_root=repo, goal_hash=_GOAL,
        output_dir=repo / ".tau-admission",
    )
    patch = json.loads(norm.patch_path.read_text())
    # post-image is the corrected file; base is the buggy one
    assert patch["base_file_sha256"] != patch["expected_post_sha256"]
    assert patch["target_file"] == "src/app.py"
    ops = json.loads(patch["patch"])
    assert ops == [{"op": "replace", "old": "    return x + 2", "new": "    return x + 1"}]


def test_unbalanced_hunk_is_typed_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("a\nb\n", encoding="utf-8")
    bad = tmp_path / "bad.patch"
    bad.write_text("--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1,2 @@\n-a\n+a\n+b\n", encoding="utf-8")
    native_path = tmp_path / "n.json"
    native_path.write_text(json.dumps({"patch_artifact": str(bad), "dod_passed": True}))
    with pytest.raises(GovernedNormalizationError):
        normalize_code_runner_native_result(
            native_result_path=native_path, repo_root=repo, goal_hash=_GOAL,
            output_dir=repo / ".t",
        )
