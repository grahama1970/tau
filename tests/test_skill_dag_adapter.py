import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tau_coding.generic_dag import run_generic_dag
from tau_coding.skill_dag_adapter import (
    execute_skill_dag_node,
    parse_skill_dag_spec,
)


def test_skill_dag_parser_requires_registered_pair(tmp_path: Path) -> None:
    raw = _webgpt_spec(tmp_path)
    raw["provider"] = "scillm"

    with pytest.raises(RuntimeError, match="unsupported skill capability/provider"):
        parse_skill_dag_spec(raw, base_dir=tmp_path, node_id="review")


def test_webgpt_skill_node_blocks_missing_exact_tab_configuration(tmp_path: Path) -> None:
    raw = _webgpt_spec(tmp_path)
    raw["configuration"] = {}
    spec = parse_skill_dag_spec(raw, base_dir=tmp_path, node_id="review")

    receipt = execute_skill_dag_node(
        spec=spec,
        run_id="run-1",
        node_id="review",
        goal_hash="sha256:goal",
        work_order_sha256="sha256:work",
        accepted_inputs=[],
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["errors"] == ["webgpt_tab_id_required", "webgpt_expected_url_required"]


def test_webgpt_transport_failure_emits_surf_doctor_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = parse_skill_dag_spec(_webgpt_spec(tmp_path), base_dir=tmp_path, node_id="review")

    def failed_run(command, **kwargs):
        def value(flag: str) -> Path:
            return Path(command[command.index(flag) + 1])

        meta = value("--meta-output")
        transport = value("--receipt-output")
        raw = value("--raw-output")
        meta.write_text(
            json.dumps(
                {
                    "failure": "missing_sentinel",
                    "proof_status": "submitted_no_response_proof",
                    "requested_tab_id": "837358072",
                    "controlled_tab_id": "837358072",
                    "sentinel": "<<<WEBGPT_DONE:test>>>",
                    "submitted_to_chatgpt": True,
                }
            ),
            encoding="utf-8",
        )
        transport.write_text(
            json.dumps(
                {
                    "status": "submitted_to_chatgpt",
                    "submitted_to_chatgpt": True,
                    "sentinel": "<<<WEBGPT_DONE:test>>>",
                }
            ),
            encoding="utf-8",
        )
        raw.write_text("Pro thinking.\n", encoding="utf-8")
        return SimpleNamespace(returncode=4, stdout="", stderr="missing sentinel\n")

    monkeypatch.setattr("tau_coding.skill_dag_adapter.subprocess.run", failed_run)

    receipt = _execute(spec)
    incident_path = tmp_path / "out" / "surf-doctor-request.json"
    incident = json.loads(incident_path.read_text(encoding="utf-8"))

    assert receipt["status"] == "BLOCKED"
    assert receipt["errors"] == ["webgpt_transport_failed:4"]
    assert incident["status"] == "PENDING_DIAGNOSIS"
    assert incident["failure_class"] == "missing_sentinel"
    assert incident["proof_status"] == "submitted_no_response_proof"
    assert incident["requested_tab_id"] == incident["controlled_tab_id"] == "837358072"
    assert incident["browser_mutation_authorized"] is False
    assert "webgpt_resubmit" in incident["forbidden_actions"]
    assert len(incident["source_artifacts"]) == 6
    assert {artifact["schema"] for artifact in receipt["artifacts"]} == {
        "surf.incident.source.v1",
        "surf_doctor.incident_request.v1",
    }


def test_webgpt_clarification_requires_answer_before_next_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = parse_skill_dag_spec(_webgpt_spec(tmp_path), base_dir=tmp_path, node_id="review")
    _patch_webgpt(monkeypatch, action="CLARIFY", questions=["Which boundary applies?"])
    first = _execute(spec)

    assert first["status"] == "BLOCKED"
    assert first["round_number"] == 1
    assert (tmp_path / "out" / "clarification-request.json").is_file()

    second = _execute(spec)

    assert second["status"] == "BLOCKED"
    assert second["errors"] == ["clarification_answer_required"]


def test_webgpt_round_budget_blocks_after_clarification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _webgpt_spec(tmp_path)
    raw["round_policy"]["max_rounds"] = 1
    spec = parse_skill_dag_spec(raw, base_dir=tmp_path, node_id="review")
    _patch_webgpt(monkeypatch, action="CLARIFY", questions=["Approve scope?"])
    first = _execute(spec)
    assert first["status"] == "BLOCKED"
    (tmp_path / "answer.md").write_text("Approved.\n", encoding="utf-8")

    second = _execute(spec)

    assert second["status"] == "BLOCKED"
    assert second["errors"] == ["skill_round_budget_exhausted"]


def test_webgpt_duplicate_clarification_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = parse_skill_dag_spec(_webgpt_spec(tmp_path), base_dir=tmp_path, node_id="review")
    _patch_webgpt(monkeypatch, action="CLARIFY", questions=["Approve scope?"])
    assert _execute(spec)["status"] == "BLOCKED"
    (tmp_path / "answer.md").write_text("Need more detail.\n", encoding="utf-8")

    second = _execute(spec)

    assert second["status"] == "BLOCKED"
    assert "duplicate_clarification_question" in second["errors"]


def test_generic_dag_runs_native_skill_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_webgpt(monkeypatch, action="PASS", questions=[])
    work_order = tmp_path / "work-order.json"
    work_order.write_text('{"task":"review architecture"}\n', encoding="utf-8")
    spec_path = tmp_path / "dag.json"
    spec_path.write_text(
        json.dumps(
            {
                "schema": "tau.generic_dag_spec.v1",
                "run_id": "skill-dag-run",
                "run_dir": str(tmp_path / "run"),
                "goal_hash": "sha256:goal",
                "nodes": [
                    {
                        "node_id": "architecture-review",
                        "receipt_path": str(tmp_path / "review-receipt.json"),
                        "work_order_path": str(work_order),
                        "skill": _webgpt_spec(tmp_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["status"] == "PASS"
    assert receipt["live"] is True
    assert receipt["provider_live"] is False
    assert receipt["nodes"][0]["skill_provider"] == "webgpt"
    assert receipt["nodes"][0]["round_number"] == 1


def test_generic_dag_resume_reuses_hash_valid_skill_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_webgpt(monkeypatch, action="PASS", questions=[])
    work_order = tmp_path / "work-order.json"
    work_order.write_text('{"task":"review architecture"}\n', encoding="utf-8")
    spec_path = tmp_path / "dag.json"
    spec_path.write_text(
        json.dumps(
            {
                "schema": "tau.generic_dag_spec.v1",
                "run_id": "skill-resume",
                "run_dir": str(tmp_path / "run"),
                "nodes": [
                    {
                        "node_id": "review",
                        "receipt_path": str(tmp_path / "receipt.json"),
                        "work_order_path": str(work_order),
                        "skill": _webgpt_spec(tmp_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert run_generic_dag(spec_path=spec_path, resume=False)["status"] == "PASS"

    def unexpected_run(*args, **kwargs):
        raise AssertionError("skill transport reran during resume")

    monkeypatch.setattr("tau_coding.skill_dag_adapter.subprocess.run", unexpected_run)
    second = run_generic_dag(spec_path=spec_path, resume=True)

    assert second["status"] == "PASS"
    assert second["nodes"][0]["resumed"] is True
    assert second["nodes"][0]["attempt_count"] == 0


def _execute(spec):
    return execute_skill_dag_node(
        spec=spec,
        run_id="run-1",
        node_id="review",
        goal_hash="sha256:goal",
        work_order_sha256="sha256:work",
        accepted_inputs=[],
    )


def _webgpt_spec(root: Path) -> dict:
    source = root / "request.md"
    source.write_text("Review this architecture.\n", encoding="utf-8")
    return {
        "schema": "tau.skill_dag_node.v1",
        "capability": "architecture_review",
        "provider": "webgpt",
        "input_path": str(source),
        "output_dir": str(root / "out"),
        "configuration": {
            "tab_id": "837358072",
            "expected_url": "https://chatgpt.com/c/test",
            "timeout_seconds": 30,
        },
        "round_policy": {
            "schema": "tau.bounded_skill_round_policy.v1",
            "max_rounds": 3,
            "clarification_allowed": True,
            "clarification_answer_path": str(root / "answer.md"),
        },
    }


def _patch_webgpt(
    monkeypatch: pytest.MonkeyPatch, *, action: str, questions: list[str]
) -> None:
    def fake_run(command, **kwargs):
        def value(flag: str) -> Path:
            return Path(command[command.index(flag) + 1])

        response = value("--output")
        raw = value("--raw-output")
        meta = value("--meta-output")
        transport = value("--receipt-output")
        response.parent.mkdir(parents=True, exist_ok=True)
        contract = {
            "schema": "tau.skill_round_response.v1",
            "action": action,
            "clarifying_questions": questions,
            "accepted_artifact_path": None,
            "summary": "fixture response",
        }
        response.write_text(f"```json\n{json.dumps(contract)}\n```\n", encoding="utf-8")
        raw.write_text(response.read_text(encoding="utf-8") + "sentinel\n", encoding="utf-8")
        meta.write_text(
            json.dumps(
                {
                    "proof_status": "response_proven",
                    "requested_tab_id": "837358072",
                    "controlled_tab_id": "837358072",
                    "controlled_tab_id_mismatch": False,
                    "tab_was_created": False,
                }
            ),
            encoding="utf-8",
        )
        transport.write_text('{"status":"submitted_to_chatgpt"}\n', encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("tau_coding.skill_dag_adapter.subprocess.run", fake_run)
