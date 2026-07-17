from tau_coding.workflows.catalog import (
    get_workflow,
    list_workflows,
    workflow_catalog_payload,
)


def test_catalog_contains_exactly_two_locked_workflows() -> None:
    workflows = list_workflows()

    assert [item.workflow_id for item in workflows] == [
        "repository-readiness",
        "tau-operator-reference",
    ]
    assert workflows[0].topology == "LINEAR"
    assert workflows[1].topology == "MULTI_STEP_SEQUENTIAL"
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


def test_catalog_public_payload_is_stable() -> None:
    payload = workflow_catalog_payload()

    assert payload["schema"] == "tau.workflow_catalog.v1"
    assert len(payload["workflows"]) == 2
    assert [workflow["workflow_id"] for workflow in payload["workflows"]] == [
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
