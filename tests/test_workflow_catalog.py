from tau_coding.workflows.catalog import (
    get_workflow,
    list_workflows,
    workflow_catalog_payload,
)


def test_catalog_contains_exactly_four_locked_workflows() -> None:
    workflows = list_workflows()

    assert [item.workflow_id for item in workflows] == [
        "approved-release-bundle",
        "repository-evidence-map",
        "repository-readiness",
        "tau-operator-reference",
    ]
    assert workflows[0].topology == "MIXED_RETRY_APPROVAL"
    assert workflows[1].topology == "FAN_OUT_FAN_IN"
    assert workflows[2].topology == "LINEAR"
    assert workflows[3].topology == "MULTI_STEP_SEQUENTIAL"
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
    assert get_workflow("approved-release-bundle") == workflows[0]
    assert get_workflow("repository-evidence-map") == workflows[1]
    assert get_workflow("repository-readiness") == workflows[2]
    assert get_workflow("tau-operator-reference") == workflows[3]


def test_catalog_public_payload_is_stable() -> None:
    payload = workflow_catalog_payload()

    assert payload["schema"] == "tau.workflow_catalog.v1"
    assert len(payload["workflows"]) == 4
    assert [workflow["workflow_id"] for workflow in payload["workflows"]] == [
        "approved-release-bundle",
        "repository-evidence-map",
        "repository-readiness",
        "tau-operator-reference",
    ]
    assert all(
        workflow["proof_boundary"]
        == {
            "mocked": False,
            "live": True,
            "provider_live": False,
        }
        for workflow in payload["workflows"]
    )
