import json
import math
import sys
from pathlib import Path

import pytest
import yaml

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan, compile_project_dag_plan
from tau_coding.generic_dag import load_generic_dag_spec, validate_generic_dag_spec
from tau_coding.project_dag import validate_dag_contract


def test_project_contract_rejects_public_coercions_and_unknown_fields() -> None:
    cases = [
        ("string_attempts", ["nodes", 0, "max_attempts"], "2", "nodes[0].max_attempts"),
        ("bool_attempts", ["nodes", 0, "max_attempts"], True, "nodes[0].max_attempts"),
        ("string_timeout", ["nodes", 0, "timeout_seconds"], "5", "nodes[0].timeout_seconds"),
        ("numeric_executor", ["nodes", 0, "executor"], 7, "nodes[0].executor"),
        ("numeric_command_spec", ["nodes", 0, "command_spec"], 7, "nodes[0].command_spec"),
        ("unknown_root", ["max_atempts"], 2, "max_atempts is not allowed outside extensions"),
        ("unknown_goal", ["goal", "summmary"], "typo", "goal.summmary"),
        ("unknown_node", ["nodes", 0, "unexpected"], "x", "nodes[0].unexpected"),
        (
            "nonfinite_extension",
            ["extensions", "nested", "score"],
            math.inf,
            "extensions.nested.score must not be NaN or Infinity",
        ),
    ]
    for _name, path, value, expected in cases:
        payload = _project_payload()
        _assign(payload, path, value)

        with pytest.raises(RuntimeError) as excinfo:
            validate_dag_contract(payload)
        assert expected in str(excinfo.value)


def test_generic_contract_rejects_public_coercions_and_unknown_fields(tmp_path: Path) -> None:
    cases = [
        ("string_attempts", ["nodes", 0, "max_attempts"], "2", "node worker max_attempts"),
        ("bool_attempts", ["nodes", 0, "max_attempts"], True, "node worker max_attempts"),
        ("string_timeout", ["nodes", 0, "timeout_seconds"], "5", "node worker timeout_seconds"),
        ("numeric_role", ["nodes", 0, "role"], 7, "node worker role"),
        ("numeric_work_order", ["nodes", 0, "work_order_path"], 7, "node worker work_order_path"),
        ("unknown_root", ["max_atempts"], 2, "max_atempts is not allowed outside extensions"),
        ("unknown_node", ["nodes", 0, "reciept_path"], "typo", "nodes[0].reciept_path"),
        (
            "nonfinite_extension",
            ["extensions", "nested", "score"],
            math.nan,
            "extensions.nested.score must not be NaN or Infinity",
        ),
    ]
    for _name, path, value, expected in cases:
        payload = _generic_payload(tmp_path)
        _assign(payload, path, value)

        with pytest.raises(RuntimeError) as excinfo:
            validate_generic_dag_spec(payload, source_path=tmp_path / "dag.json")
        assert expected in str(excinfo.value)


def test_public_extensions_are_explicit_preserved_and_hash_bound(tmp_path: Path) -> None:
    first = _generic_payload(tmp_path / "first")
    first["extensions"] = {"project_extension": {"revision": 1}}
    second = _generic_payload(tmp_path / "second")
    second["extensions"] = {"project_extension": {"revision": 2}}

    first_plan = compile_generic_dag_plan(first, source_path=tmp_path / "first" / "dag.json")
    second_plan = compile_generic_dag_plan(second, source_path=tmp_path / "second" / "dag.json")

    assert first_plan.to_payload()["source_extensions"]["project_extension"] == {"revision": 1}
    assert first_plan.plan_sha256 != second_plan.plan_sha256

    undeclared = _generic_payload(tmp_path / "bad")
    undeclared["project_extension"] = {"revision": 1}
    with pytest.raises(RuntimeError, match="project_extension is not allowed outside extensions"):
        compile_generic_dag_plan(undeclared, source_path=tmp_path / "bad" / "dag.json")


def test_project_public_extensions_are_explicit_preserved_and_hash_bound(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    bad_root = tmp_path / "bad"
    first = _project_payload(first_root)
    first["extensions"] = {"project_extension": {"revision": 1}}
    second = _project_payload(second_root)
    second["extensions"] = {"project_extension": {"revision": 2}}

    first_plan = compile_project_dag_plan(first, source_path=first_root / "dag.json")
    second_plan = compile_project_dag_plan(second, source_path=second_root / "dag.json")

    assert first_plan.to_payload()["source_extensions"]["project_extension"] == {"revision": 1}
    assert first_plan.plan_sha256 != second_plan.plan_sha256

    undeclared = _project_payload(bad_root)
    undeclared["project_extension"] = {"revision": 1}
    with pytest.raises(RuntimeError, match="project_extension is not allowed outside extensions"):
        compile_project_dag_plan(undeclared, source_path=bad_root / "dag.json")


def test_generic_json_and_yaml_load_to_same_canonical_plan(tmp_path: Path) -> None:
    payload = _generic_payload(tmp_path)
    json_path = tmp_path / "dag.json"
    yaml_path = tmp_path / "dag.yaml"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    yaml_path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")

    json_plan = compile_generic_dag_plan(load_generic_dag_spec(json_path), source_path=json_path)
    yaml_plan = compile_generic_dag_plan(load_generic_dag_spec(yaml_path), source_path=yaml_path)

    assert json_plan.to_payload() == yaml_plan.to_payload()


def _assign(payload: dict[str, object], path: list[object], value: object) -> None:
    cursor: object = payload
    for index, key in enumerate(path[:-1]):
        next_key = path[index + 1]
        if isinstance(key, int):
            cursor = cursor[key]  # type: ignore[index]
        else:
            if key not in cursor:  # type: ignore[operator]
                cursor[key] = [] if isinstance(next_key, int) else {}  # type: ignore[index]
            cursor = cursor[key]  # type: ignore[index]
    last = path[-1]
    if isinstance(last, int):
        cursor[last] = value  # type: ignore[index]
    else:
        cursor[last] = value  # type: ignore[index]


def _project_payload(root: Path | None = None) -> dict[str, object]:
    command_spec = "specs/worker.json"
    if root is not None:
        spec_path = root / command_spec
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text('{"schema":"tau.agent_command_spec.v1"}\n', encoding="utf-8")
    return {
        "schema": "tau.dag_contract.v1",
        "dag_id": "strict-public-project",
        "goal": {
            "goal_id": "strict-public-project",
            "goal_version": 1,
            "goal_hash": "sha256:strict-public-project",
        },
        "target": {"repo": "grahama1970/tau", "target": "issue#296"},
        "entry_node": "worker",
        "terminal_nodes": ["human"],
        "limits": {"default_timeout_seconds": 5, "max_total_attempts": 1},
        "nodes": [
            {
                "id": "worker",
                "agent": "worker",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": command_spec,
                "required_evidence": ["artifact"],
            }
        ],
        "edges": [{"from": "worker", "to": "human"}],
        "required_evidence": ["artifact"],
        "fail_closed_on": [
            "goal_hash_mismatch",
            "target_changed",
            "unexpected_node",
            "unexpected_edge",
            "missing_required_evidence",
            "max_attempts_exceeded",
            "malformed_handoff",
        ],
    }


def _generic_payload(root: Path) -> dict[str, object]:
    return {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "strict-public-generic",
        "run_dir": str(root / "run"),
        "nodes": [
            {
                "node_id": "worker",
                "role": "worker",
                "command": [sys.executable, "-c", "print('ok')"],
                "receipt_path": str(root / "worker-receipt.json"),
                "timeout_seconds": 5,
                "max_attempts": 1,
            }
        ],
    }
