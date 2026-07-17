from tau_coding.workflows.catalog import (
    get_workflow,
    list_workflows,
    workflow_catalog_payload,
)


def test_catalog_contains_only_repository_readiness() -> None:
    workflows = list_workflows()

    assert [item.workflow_id for item in workflows] == ["repository-readiness"]
    assert workflows[0].topology == "LINEAR"
    assert workflows[0].runtime == {
        "local": True,
        "network_required": False,
        "provider_required": False,
        "mutation_allowed": False,
    }
    assert get_workflow("repository-readiness") == workflows[0]


def test_catalog_public_payload_is_stable() -> None:
    payload = workflow_catalog_payload()

    assert payload["schema"] == "tau.workflow_catalog.v1"
    assert len(payload["workflows"]) == 1
    workflow = payload["workflows"][0]
    assert workflow["workflow_id"] == "repository-readiness"
    assert workflow["proof_boundary"] == {
        "mocked": False,
        "live": True,
        "provider_live": False,
    }
