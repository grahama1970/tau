import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.code_patch import (
    CODE_PATCH_RECEIPT_SCHEMA,
    CODE_PATCH_SCHEMA,
    apply_code_patch_receipt,
)


def test_code_patch_passes_when_base_hash_and_post_hash_match(tmp_path: Path) -> None:
    target = tmp_path / "src" / "example.py"
    target.parent.mkdir()
    before = "def answer():\n    return 41\n"
    after = "def answer():\n    return 42\n"
    target.write_text(before, encoding="utf-8")
    patch_path = _write_patch(
        tmp_path,
        target_file="src/example.py",
        before=before,
        after=after,
        patch=json.dumps([{"op": "replace", "old": "return 41", "new": "return 42"}]),
    )

    receipt = apply_code_patch_receipt(
        patch_path=patch_path,
        repo_root=tmp_path,
        expected_goal_hash="sha256:goal",
    )

    assert receipt["schema"] == CODE_PATCH_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["applied"] is True
    assert receipt["patch_sha256"] == f"sha256:{_sha256_file(patch_path)}"
    assert receipt["patch_bytes"] == patch_path.stat().st_size
    assert receipt["before_sha256"] == f"sha256:{_sha256_text(before)}"
    assert receipt["after_sha256"] == f"sha256:{_sha256_text(after)}"
    assert receipt["target_artifact_before"] == {
        "path": str(target.resolve()),
        "sha256": f"sha256:{_sha256_text(before)}",
        "bytes": len(before.encode("utf-8")),
    }
    assert receipt["target_artifact_after"] == {
        "path": str(target.resolve()),
        "sha256": f"sha256:{_sha256_text(after)}",
        "bytes": len(after.encode("utf-8")),
    }
    assert target.read_text(encoding="utf-8") == after


def test_code_patch_blocks_stale_base_hash(tmp_path: Path) -> None:
    target = tmp_path / "src" / "example.py"
    target.parent.mkdir()
    actual = "value = 2\n"
    target.write_text(actual, encoding="utf-8")
    patch_path = _write_patch(
        tmp_path,
        target_file="src/example.py",
        before="value = 1\n",
        after="value = 3\n",
        patch=json.dumps([{"op": "replace", "old": "value = 2", "new": "value = 3"}]),
    )

    receipt = apply_code_patch_receipt(patch_path=patch_path, repo_root=tmp_path)

    assert receipt["status"] == "BLOCKED"
    assert "stale_base_hash" in receipt["alert_codes"]
    assert receipt["applied"] is False
    assert receipt["target_artifact_before"] == {
        "path": str(target.resolve()),
        "sha256": f"sha256:{_sha256_text(actual)}",
        "bytes": len(actual.encode("utf-8")),
    }
    assert receipt["target_artifact_after"] == receipt["target_artifact_before"]
    assert target.read_text(encoding="utf-8") == actual


def test_code_patch_blocks_missing_anchor(tmp_path: Path) -> None:
    target = tmp_path / "src" / "example.py"
    target.parent.mkdir()
    before = "value = 1\n"
    after = "value = 2\n"
    target.write_text(before, encoding="utf-8")
    patch_path = _write_patch(
        tmp_path,
        target_file="src/example.py",
        before=before,
        after=after,
        patch=json.dumps([{"op": "replace", "old": "value = 1", "new": "value = 2"}]),
        anchors=[{"kind": "symbol", "value": "missing_symbol"}],
    )

    receipt = apply_code_patch_receipt(patch_path=patch_path, repo_root=tmp_path)

    assert receipt["status"] == "BLOCKED"
    assert "missing_anchor" in receipt["alert_codes"]
    assert target.read_text(encoding="utf-8") == before


def test_code_patch_blocks_partial_line_span_anchor(tmp_path: Path) -> None:
    target = tmp_path / "src" / "example.py"
    target.parent.mkdir()
    before = "def answer():\n    return 41\n"
    after = "def answer():\n    return 42\n"
    target.write_text(before, encoding="utf-8")
    patch_path = _write_patch(
        tmp_path,
        target_file="src/example.py",
        before=before,
        after=after,
        patch=json.dumps([{"op": "replace", "old": "return 41", "new": "return 42"}]),
        anchors=[{"kind": "line_span", "value": "return 4"}],
    )

    receipt = apply_code_patch_receipt(patch_path=patch_path, repo_root=tmp_path)

    assert receipt["status"] == "BLOCKED"
    assert "missing_anchor" in receipt["alert_codes"]
    assert target.read_text(encoding="utf-8") == before


def test_code_patch_accepts_hash_bound_line_span_anchor(tmp_path: Path) -> None:
    target = tmp_path / "src" / "example.py"
    target.parent.mkdir()
    before = "def answer():\n    return 41\n"
    after = "def answer():\n    return 42\n"
    target.write_text(before, encoding="utf-8")
    span_text = "def answer():\n    return 41"
    patch_path = _write_patch(
        tmp_path,
        target_file="src/example.py",
        before=before,
        after=after,
        patch=json.dumps([{"op": "replace", "old": "return 41", "new": "return 42"}]),
        anchors=[{"kind": "line_span", "value": f"line_span:1:2:sha256:{_sha256_text(span_text)}"}],
    )

    receipt = apply_code_patch_receipt(patch_path=patch_path, repo_root=tmp_path)

    assert receipt["status"] == "PASS"
    assert receipt["applied"] is True
    assert target.read_text(encoding="utf-8") == after


def test_code_patch_blocks_disallowed_path(tmp_path: Path) -> None:
    target = tmp_path / "secrets" / "token.txt"
    target.parent.mkdir()
    before = "old\n"
    after = "new\n"
    target.write_text(before, encoding="utf-8")
    patch_path = _write_patch(
        tmp_path,
        target_file="secrets/token.txt",
        before=before,
        after=after,
        patch=json.dumps([{"op": "replace", "old": "old", "new": "new"}]),
    )

    receipt = apply_code_patch_receipt(patch_path=patch_path, repo_root=tmp_path)

    assert receipt["status"] == "BLOCKED"
    assert "disallowed_path" in receipt["alert_codes"]
    assert target.read_text(encoding="utf-8") == before


def test_code_patch_blocks_explicit_forbidden_path(tmp_path: Path) -> None:
    target = tmp_path / "src" / "generated_client.py"
    target.parent.mkdir()
    before = "value = 1\n"
    after = "value = 2\n"
    target.write_text(before, encoding="utf-8")
    patch_path = _write_patch(
        tmp_path,
        target_file="src/generated_client.py",
        before=before,
        after=after,
        patch=json.dumps([{"op": "replace", "old": "value = 1", "new": "value = 2"}]),
        forbidden_paths=["src/generated_client.py"],
    )

    receipt = apply_code_patch_receipt(patch_path=patch_path, repo_root=tmp_path)

    assert receipt["status"] == "BLOCKED"
    assert "forbidden_path" in receipt["alert_codes"]
    assert receipt["forbidden_paths"] == ["src/generated_client.py"]
    assert target.read_text(encoding="utf-8") == before


def test_code_patch_blocks_generated_path_pattern(tmp_path: Path) -> None:
    target = tmp_path / "src" / "generated" / "example.py"
    target.parent.mkdir(parents=True)
    before = "value = 1\n"
    after = "value = 2\n"
    target.write_text(before, encoding="utf-8")
    patch_path = _write_patch(
        tmp_path,
        target_file="src/generated/example.py",
        before=before,
        after=after,
        patch=json.dumps([{"op": "replace", "old": "value = 1", "new": "value = 2"}]),
    )

    receipt = apply_code_patch_receipt(patch_path=patch_path, repo_root=tmp_path)

    assert receipt["status"] == "BLOCKED"
    assert "forbidden_path" in receipt["alert_codes"]
    assert "**/generated/**" in receipt["generated_path_patterns"]
    assert target.read_text(encoding="utf-8") == before


def test_code_patch_blocks_goal_hash_mismatch(tmp_path: Path) -> None:
    target = tmp_path / "src" / "example.py"
    target.parent.mkdir()
    before = "value = 1\n"
    after = "value = 2\n"
    target.write_text(before, encoding="utf-8")
    patch_path = _write_patch(
        tmp_path,
        target_file="src/example.py",
        before=before,
        after=after,
        patch=json.dumps([{"op": "replace", "old": "value = 1", "new": "value = 2"}]),
    )

    receipt = apply_code_patch_receipt(
        patch_path=patch_path,
        repo_root=tmp_path,
        expected_goal_hash="sha256:other",
    )

    assert receipt["status"] == "BLOCKED"
    assert "goal_hash_mismatch" in receipt["alert_codes"]
    assert target.read_text(encoding="utf-8") == before


def test_code_patch_writes_blocked_receipt_for_unreadable_patch(tmp_path: Path) -> None:
    patch_path = tmp_path / "patch.json"
    receipt_path = tmp_path / "receipt.json"
    patch_path.write_text("{not-json", encoding="utf-8")

    receipt = apply_code_patch_receipt(
        patch_path=patch_path,
        repo_root=tmp_path,
        receipt_path=receipt_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert "code_patch_unreadable" in receipt["alert_codes"]
    assert receipt["patch_sha256"] == f"sha256:{_sha256_file(patch_path)}"
    assert receipt["patch_bytes"] == patch_path.stat().st_size
    assert receipt == json.loads(receipt_path.read_text(encoding="utf-8"))


def test_code_patch_writes_blocked_receipt_for_missing_patch_artifact(
    tmp_path: Path,
) -> None:
    patch_path = tmp_path / "missing-patch.json"
    receipt_path = tmp_path / "receipt.json"

    receipt = apply_code_patch_receipt(
        patch_path=patch_path,
        repo_root=tmp_path,
        receipt_path=receipt_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert "code_patch_missing" in receipt["alert_codes"]
    assert receipt["patch_path"] == str(patch_path.resolve())
    assert receipt["patch_sha256"] is None
    assert receipt["patch_bytes"] is None
    assert receipt == json.loads(receipt_path.read_text(encoding="utf-8"))


def test_code_patch_writes_blocked_receipt_for_non_object_patch(tmp_path: Path) -> None:
    patch_path = tmp_path / "patch.json"
    receipt_path = tmp_path / "receipt.json"
    patch_path.write_text("[]", encoding="utf-8")

    receipt = apply_code_patch_receipt(
        patch_path=patch_path,
        repo_root=tmp_path,
        receipt_path=receipt_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert "code_patch_not_object" in receipt["alert_codes"]
    assert receipt == json.loads(receipt_path.read_text(encoding="utf-8"))


def test_code_patch_zero_trust_requires_policy_and_data_boundary(tmp_path: Path) -> None:
    target = tmp_path / "src" / "example.py"
    target.parent.mkdir()
    before = "value = 1\n"
    after = "value = 2\n"
    target.write_text(before, encoding="utf-8")
    patch_path = _write_patch(
        tmp_path,
        target_file="src/example.py",
        before=before,
        after=after,
        patch=json.dumps([{"op": "replace", "old": "value = 1", "new": "value = 2"}]),
    )

    receipt = apply_code_patch_receipt(
        patch_path=patch_path,
        repo_root=tmp_path,
        zero_trust=True,
    )

    assert receipt["status"] == "BLOCKED"
    assert "missing_policy_profile" in receipt["alert_codes"]
    assert "missing_data_boundary" in receipt["alert_codes"]
    assert target.read_text(encoding="utf-8") == before


def test_code_patch_receipt_records_policy_and_data_boundary(tmp_path: Path) -> None:
    target = tmp_path / "src" / "example.py"
    target.parent.mkdir()
    before = "value = 1\n"
    after = "value = 2\n"
    target.write_text(before, encoding="utf-8")
    patch_path = _write_patch(
        tmp_path,
        target_file="src/example.py",
        before=before,
        after=after,
        patch=json.dumps([{"op": "replace", "old": "value = 1", "new": "value = 2"}]),
    )

    receipt = apply_code_patch_receipt(
        patch_path=patch_path,
        repo_root=tmp_path,
        zero_trust=True,
        policy_profile={"schema": "tau.policy_profile.v1", "profile_id": "local"},
        data_boundary={"schema": "tau.data_boundary.v1", "classification": "public"},
    )

    assert receipt["status"] == "PASS"
    assert receipt["zero_trust"] is True
    assert receipt["policy_profile"]["profile_id"] == "local"
    assert receipt["data_boundary"]["classification"] == "public"
    assert receipt["allowed_paths"] == ["src/**", "tests/**"]


def test_cli_code_patch_writes_receipt(tmp_path: Path) -> None:
    target = tmp_path / "src" / "example.py"
    target.parent.mkdir()
    before = "value = 1\n"
    after = "value = 2\n"
    target.write_text(before, encoding="utf-8")
    patch_path = _write_patch(
        tmp_path,
        target_file="src/example.py",
        before=before,
        after=after,
        patch=json.dumps([{"op": "replace", "old": "value = 1", "new": "value = 2"}]),
    )
    receipt_path = tmp_path / "receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "code-patch",
            "--patch",
            str(patch_path),
            "--repo",
            str(tmp_path),
            "--out",
            str(receipt_path),
            "--goal-hash",
            "sha256:goal",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["schema"] == CODE_PATCH_RECEIPT_SCHEMA
    assert payload["applied"] is True
    assert target.read_text(encoding="utf-8") == after


def test_cli_code_patch_unreadable_patch_writes_blocked_receipt(tmp_path: Path) -> None:
    patch_path = tmp_path / "patch.json"
    receipt_path = tmp_path / "receipt.json"
    patch_path.write_text("{not-json", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "code-patch",
            "--patch",
            str(patch_path),
            "--repo",
            str(tmp_path),
            "--out",
            str(receipt_path),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload == json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert "code_patch_unreadable" in payload["alert_codes"]


def _write_patch(
    root: Path,
    *,
    target_file: str,
    before: str,
    after: str,
    patch: str,
    anchors: list[dict[str, str]] | None = None,
    forbidden_paths: list[str] | None = None,
) -> Path:
    payload = {
        "schema": CODE_PATCH_SCHEMA,
        "goal_hash": "sha256:goal",
        "target_file": target_file,
        "base_file_sha256": f"sha256:{_sha256_text(before)}",
        "allowed_paths": ["src/**", "tests/**"],
        "forbidden_paths": forbidden_paths or [],
        "anchors": anchors or [{"kind": "line_span", "value": before.strip()}],
        "patch": patch,
        "rationale": "exercise hash-bound patch receipt",
        "expected_post_sha256": f"sha256:{_sha256_text(after)}",
    }
    path = root / "patch.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
