from tau_coding.workflows.catalog import (
    dag_ladder_manifest_payload,
    get_workflow,
    list_workflows,
    workflow_catalog_payload,
)


def test_catalog_contains_exactly_five_locked_workflows() -> None:
    workflows = list_workflows()

    assert [item.workflow_id for item in workflows] == [
        "repository-readiness",
        "tau-operator-reference",
        "repository-evidence-map",
        "approved-release-bundle",
        "durable-repository-qualification",
    ]
    assert [item.rung for item in workflows] == [1, 2, 3, 4, 5]
    assert workflows[0].topology == "LINEAR"
    assert workflows[1].topology == "MULTI_STEP_SEQUENTIAL"
    assert workflows[2].topology == "FAN_OUT_FAN_IN"
    assert workflows[3].topology == "MIXED_RETRY_APPROVAL"
    assert workflows[4].topology == "DURABLE_MIXED_REPAIR_APPROVAL"
    assert all(
        item.runtime
        == {
            "local": True,
            "network_required": False,
            "provider_required": False,
            "mutation_allowed": False,
        }
        for item in workflows
    )
    assert get_workflow("repository-readiness") == workflows[0]
    assert get_workflow("tau-operator-reference") == workflows[1]
    assert get_workflow("repository-evidence-map") == workflows[2]
    assert get_workflow("approved-release-bundle") == workflows[3]
    assert get_workflow("durable-repository-qualification") == workflows[4]


def test_catalog_public_payload_is_stable() -> None:
    payload = workflow_catalog_payload()

    assert payload["schema"] == "tau.workflow_catalog.v1"
    assert len(payload["workflows"]) == 5
    assert [workflow["workflow_id"] for workflow in payload["workflows"]] == [
        "repository-readiness",
        "tau-operator-reference",
        "repository-evidence-map",
        "approved-release-bundle",
        "durable-repository-qualification",
    ]
    assert [workflow["rung"] for workflow in payload["workflows"]] == [1, 2, 3, 4, 5]
    assert all(
        workflow["proof_boundary"]
        == {
            "mocked": False,
            "live": True,
            "provider_live": False,
        }
        for workflow in payload["workflows"]
    )


def test_dag_ladder_manifest_names_five_rungs_and_boundaries() -> None:
    manifest = dag_ladder_manifest_payload()

    assert manifest["schema"] == "tau.dag_ladder_manifest.v1"
    assert manifest["status"] == "RUNG_1_READY_OTHERS_NAMED"
    assert manifest["topology_progression"] == [
        "LINEAR",
        "MULTI_STEP_SEQUENTIAL",
        "FAN_OUT_FAN_IN",
        "MIXED_RETRY_APPROVAL",
        "DURABLE_MIXED_REPAIR_APPROVAL",
    ]
    rungs = manifest["rungs"]
    assert [rung["workflow_id"] for rung in rungs] == [
        "repository-readiness",
        "tau-operator-reference",
        "repository-evidence-map",
        "approved-release-bundle",
        "durable-repository-qualification",
    ]
    assert rungs[0]["proof_status"] == "READY"
    assert rungs[0]["retained_proof"].endswith("proof-receipt.json")
    assert all(rung["acceptance_boundary"] for rung in rungs)
    assert all(rung["next_proof_required"] for rung in rungs[1:])
