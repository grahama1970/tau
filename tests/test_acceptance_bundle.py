"""Acceptance bundle generator tests (#217)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tau_coding.acceptance_bundle import (
    AcceptanceBundleError,
    generate_acceptance_bundle,
)


def _clean_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "f").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"], check=True
    )
    return repo


def _wheel(tmp_path: Path) -> Path:
    w = tmp_path / "tau-0.1.0-py3-none-any.whl"
    w.write_bytes(b"PK\x03\x04 fake wheel bytes")
    return w


def test_bundle_has_all_required_artifacts(tmp_path: Path) -> None:
    repo, wheel = _clean_repo(tmp_path), _wheel(tmp_path)
    out = tmp_path / "bundle"
    generate_acceptance_bundle(repo=repo, wheel=wheel, output_dir=out)
    for name in ("BUILD_MANIFEST.json", "WALKTHROUGH.md", "OBSERVATIONS.json",
                 "DEFECT_LOG.md", "verify_bundle.sh", "ACCEPTANCE.json"):
        assert (out / name).is_file(), name


def test_observations_are_all_unchecked_with_two_negative_paths(tmp_path: Path) -> None:
    repo, wheel = _clean_repo(tmp_path), _wheel(tmp_path)
    out = tmp_path / "bundle"
    generate_acceptance_bundle(repo=repo, wheel=wheel, output_dir=out)
    obs = json.loads((out / "OBSERVATIONS.json").read_text())
    assert all(o["checked"] is False for o in obs)
    negatives = [o for o in obs if o["kind"] == "negative"]
    assert len(negatives) == 2


def test_acceptance_record_is_unsigned(tmp_path: Path) -> None:
    repo, wheel = _clean_repo(tmp_path), _wheel(tmp_path)
    out = tmp_path / "bundle"
    generate_acceptance_bundle(repo=repo, wheel=wheel, output_dir=out)
    rec = json.loads((out / "ACCEPTANCE.json").read_text())
    assert rec["signature"] is None
    assert rec["decision"] is None
    assert rec["bundle_digest"].startswith("sha256:")


def test_dirty_tree_is_refused(tmp_path: Path) -> None:
    repo, wheel = _clean_repo(tmp_path), _wheel(tmp_path)
    (repo / "dirty").write_text("uncommitted")
    with pytest.raises(AcceptanceBundleError, match="dirty tree"):
        generate_acceptance_bundle(repo=repo, wheel=wheel, output_dir=tmp_path / "b")


def test_same_commit_is_byte_identical(tmp_path: Path) -> None:
    repo, wheel = _clean_repo(tmp_path), _wheel(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    r1 = generate_acceptance_bundle(repo=repo, wheel=wheel, output_dir=a)
    r2 = generate_acceptance_bundle(repo=repo, wheel=wheel, output_dir=b)
    assert r1.bundle_digest == r2.bundle_digest
    for name in ("BUILD_MANIFEST.json", "WALKTHROUGH.md", "ACCEPTANCE.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_verify_bundle_detects_drift(tmp_path: Path) -> None:
    repo, wheel = _clean_repo(tmp_path), _wheel(tmp_path)
    out = tmp_path / "bundle"
    generate_acceptance_bundle(repo=repo, wheel=wheel, output_dir=out)
    assert subprocess.run(["bash", str(out / "verify_bundle.sh")]).returncode == 0
    (out / "WALKTHROUGH.md").write_text("tampered")
    assert subprocess.run(["bash", str(out / "verify_bundle.sh")]).returncode != 0
