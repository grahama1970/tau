from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan, write_dag_plan
from tau_coding.dag_runtime.execution_profile import (
    EXECUTION_PROFILE_RESOLUTION_SCHEMA,
    ExecutionProfileError,
    evaluate_profile_revision,
)


def test_all_profiles_compile_into_hash_bound_generic_dag_plan(tmp_path: Path) -> None:
    hashes: set[str] = set()
    for profile_id in ("interactive", "standard", "assurance"):
        spec = _generic_spec(tmp_path / profile_id, execution_profile=profile_id)

        plan = compile_generic_dag_plan(spec, source_path=tmp_path / profile_id / "dag.json")
        payload = plan.to_payload()
        resolution = payload["source_extensions"]["execution_profile_resolution"]
        profile_limits = payload["execution_limits"]["execution_profile"]

        assert resolution["schema"] == EXECUTION_PROFILE_RESOLUTION_SCHEMA
        assert resolution["profile_id"] == profile_id
        assert resolution["selection_source"] == "source_declared"
        assert profile_limits["profile_id"] == profile_id
        assert profile_limits["resolution_sha256"] == resolution["resolution_sha256"]
        assert plan.plan_sha256
        hashes.add(plan.plan_sha256)

    assert len(hashes) == 3


def test_profileless_generic_dag_uses_explicit_compatibility_mapping(tmp_path: Path) -> None:
    spec = _generic_spec(tmp_path)

    plan = compile_generic_dag_plan(spec, source_path=tmp_path / "dag.json")
    resolution = plan.to_payload()["source_extensions"]["execution_profile_resolution"]

    assert resolution["profile_id"] == "standard"
    assert resolution["compatibility_default"] is True
    assert resolution["historical_profile_omitted"] is True
    assert resolution["selection_source"] == "compatibility_default_profileless_current_contract"
    assert resolution["proof_boundary"]["historical_runs_rewritten"] is False


def test_source_override_can_narrow_profile_budget(tmp_path: Path) -> None:
    spec = _generic_spec(
        tmp_path,
        execution_profile={
            "schema": "tau.execution_profile.v1",
            "profile_id": "standard",
            "overrides": {"max_concurrency": 2, "max_nodes": 4},
        },
        max_concurrency=2,
    )

    plan = compile_generic_dag_plan(spec, source_path=tmp_path / "dag.json")
    controls = plan.to_payload()["source_extensions"]["execution_profile_resolution"][
        "resolved_controls"
    ]

    assert controls["max_concurrency"] == 2
    assert controls["max_nodes"] == 4


def test_profile_override_cannot_broaden_parent_budget(tmp_path: Path) -> None:
    spec = _generic_spec(
        tmp_path,
        execution_profile={
            "schema": "tau.execution_profile.v1",
            "profile_id": "interactive",
            "overrides": {"max_concurrency": 8},
        },
    )

    with pytest.raises(ExecutionProfileError, match="execution_profile_override_broadens_policy"):
        compile_generic_dag_plan(spec, source_path=tmp_path / "dag.json")


def test_model_authored_profile_is_rejected(tmp_path: Path) -> None:
    spec = _generic_spec(
        tmp_path,
        execution_profile={
            "schema": "tau.execution_profile.v1",
            "profile_id": "interactive",
            "authored_by": "model",
        },
    )

    with pytest.raises(ExecutionProfileError, match="execution_profile_model_authored"):
        compile_generic_dag_plan(spec, source_path=tmp_path / "dag.json")


def test_data_boundary_narrows_profile_to_assurance(tmp_path: Path) -> None:
    spec = _generic_spec(
        tmp_path,
        execution_profile="interactive",
        data_boundary={"classification": "controlled"},
    )

    plan = compile_generic_dag_plan(spec, source_path=tmp_path / "dag.json")
    resolution = plan.to_payload()["source_extensions"]["execution_profile_resolution"]

    assert resolution["profile_id"] == "assurance"
    assert (
        resolution["policy_data_boundary_compatibility"]["data_boundary_requires_assurance"] is True
    )


def test_profile_revision_rejects_downgrade_and_requires_approval_for_strengthening(
    tmp_path: Path,
) -> None:
    spec = _generic_spec(tmp_path, execution_profile="standard")
    plan = compile_generic_dag_plan(spec, source_path=tmp_path / "dag.json")
    resolution = plan.to_payload()["source_extensions"]["execution_profile_resolution"]

    downgrade = evaluate_profile_revision(
        resolution,
        "interactive",
        approved_strengthening=False,
    )
    blocked_strengthening = evaluate_profile_revision(
        resolution,
        "assurance",
        approved_strengthening=False,
    )
    approved_strengthening = evaluate_profile_revision(
        resolution,
        "assurance",
        approved_strengthening=True,
    )

    assert downgrade["status"] == "BLOCKED"
    assert downgrade["verdict"] == "PROFILE_DOWNGRADE_REJECTED"
    assert blocked_strengthening["status"] == "BLOCKED"
    assert blocked_strengthening["verdict"] == "PROFILE_STRENGTHENING_REQUIRES_APPROVAL"
    assert approved_strengthening["status"] == "PASS"
    assert approved_strengthening["new_plan_required"] is True


def test_dag_plan_cli_receipt_surfaces_profile_resolution(tmp_path: Path) -> None:
    spec = _generic_spec(tmp_path, execution_profile="assurance")
    spec_path = tmp_path / "dag.json"
    output_path = tmp_path / "plan.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    receipt = write_dag_plan(spec_path, output_path=output_path)
    plan = json.loads(output_path.read_text(encoding="utf-8"))

    assert receipt["execution_profile"]["profile_id"] == "assurance"
    assert (
        receipt["execution_profile"]["resolution_sha256"]
        == plan["source_extensions"]["execution_profile_resolution"]["resolution_sha256"]
    )


def _generic_spec(
    root: Path,
    *,
    execution_profile: object | None = None,
    max_concurrency: int | None = None,
    data_boundary: dict[str, object] | None = None,
) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=True)
    receipt_path = root / "receipt.json"
    spec: dict[str, object] = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "profile-test",
        "run_dir": str(root / "run"),
        "nodes": [
            {
                "node_id": "node",
                "role": "node",
                "command": [sys.executable, "-c", "print('ok')"],
                "receipt_path": str(receipt_path),
                "timeout_seconds": 1,
                "max_attempts": 1,
            }
        ],
    }
    if execution_profile is not None:
        spec["execution_profile"] = execution_profile
    if max_concurrency is not None:
        spec["max_concurrency"] = max_concurrency
    if data_boundary is not None:
        spec["data_boundary"] = data_boundary
    return spec
