"""Immutable source DAG retention checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tau_coding.dag_viewer.source_artifact import write_dag_source_artifact


def test_source_artifact_is_immutable_and_reusable(tmp_path: Path) -> None:
    payload = {"schema": "tau.generic_dag_spec.v1", "run_id": "run-1"}
    first = write_dag_source_artifact(
        source_payload=payload,
        source_schema=payload["schema"],
        source_path=tmp_path / "dag.json",
        run_dir=tmp_path / "run",
    )
    second = write_dag_source_artifact(
        source_payload=payload,
        source_schema=payload["schema"],
        source_path=tmp_path / "dag.json",
        run_dir=tmp_path / "run",
    )
    assert first.source_sha256 == second.source_sha256
    assert json.loads((tmp_path / "run/source-dag.json").read_text()) == payload


def test_source_artifact_conflict_blocks(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    write_dag_source_artifact(
        source_payload={"schema": "one"},
        source_schema="one",
        source_path=tmp_path / "one.json",
        run_dir=run_dir,
    )
    with pytest.raises(RuntimeError, match="dag_source_artifact_conflict"):
        write_dag_source_artifact(
            source_payload={"schema": "two"},
            source_schema="two",
            source_path=tmp_path / "two.json",
            run_dir=run_dir,
        )
