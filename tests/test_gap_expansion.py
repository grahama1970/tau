import json
from pathlib import Path

import pytest

from tau_coding.gap_expansion import (
    EXPANSION_ENVELOPE_SCHEMA,
    GAP_EXPANSION_BRIDGE_RECEIPT_SCHEMA,
    derive_gap_expansion_results,
    write_gap_expansion_bridge_receipt,
)


def test_gap_expansion_bridge_writes_proposal_with_lineage(tmp_path: Path) -> None:
    contract_path = _write_json(tmp_path / "dag.json", _contract())
    boundary_path = _write_json(tmp_path / "boundary.json", _boundary())
    envelope_path = _write_json(tmp_path / "envelope.json", _envelope())
    receipt_path = tmp_path / "bridge.json"
    proposals_dir = tmp_path / "proposals"

    receipt = write_gap_expansion_bridge_receipt(
        dag_contract_path=contract_path,
        boundary_path=boundary_path,
        envelope_path=envelope_path,
        receipt_path=receipt_path,
        proposals_dir=proposals_dir,
        source_run_id="run-001",
    )
    proposal = json.loads(Path(receipt["proposal_paths"][0]["path"]).read_text())

    assert receipt["schema"] == GAP_EXPANSION_BRIDGE_RECEIPT_SCHEMA
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["proposal_count"] == 1
    assert receipt["dispositions"]["eligible_for_policy"] == 1
    assert proposal["schema"] == "tau.dag_expansion_proposal.v1"
    assert (
        proposal["source_gap_lineage"]["canonical_gap_identity"]
        == receipt["candidates"][0]["canonical_gap_identity"]
    )
    assert (
        proposal["new_nodes"][0]["source_gap_lineage"]["candidate_id"]
        == receipt["candidates"][0]["candidate_id"]
    )
    assert proposal["new_edges"] == [
        {"from": "coder", "to": "gap-coder-gap-validation"},
        {"from": "gap-coder-gap-validation", "to": "reviewer"},
    ]


@pytest.mark.parametrize(
    ("envelope_delta", "options", "expected"),
    [
        ({"allowed_paths": ["README.md"]}, {}, "out_of_envelope"),
        ({"allowed_capabilities": ["read"]}, {}, "out_of_envelope"),
        ({"allowed_data_classes": ["controlled"]}, {}, "out_of_envelope"),
        ({"allowed_side_effect_classes": ["filesystem-write"]}, {}, "out_of_envelope"),
        ({"permitted_child_roles": ["goal-guardian"]}, {}, "out_of_envelope"),
        ({"max_depth": 0}, {}, "out_of_envelope"),
        ({"human_approval_required": True}, {}, "human_required"),
        ({}, {"used_budget": 1}, "budget_exhausted"),
        ({}, {"existing_lineages": "from-first"}, "duplicate_or_superseded"),
    ],
)
def test_gap_expansion_bridge_dispositions_are_envelope_bound(
    envelope_delta: dict[str, object],
    options: dict[str, object],
    expected: str,
) -> None:
    contract = _contract()
    boundary = _boundary()
    envelope = {**_envelope(), **envelope_delta}
    existing_lineages = ()
    if options.get("existing_lineages") == "from-first":
        first = derive_gap_expansion_results(
            contract_payload=contract,
            boundary=boundary,
            envelope=_envelope(),
            source_run_id="run-001",
        )[0]
        existing_lineages = (str(first.candidate["canonical_gap_identity"]),)

    result = derive_gap_expansion_results(
        contract_payload=contract,
        boundary=boundary,
        envelope=envelope,
        source_run_id="run-001",
        existing_lineages=existing_lineages,
        used_budget=int(options.get("used_budget", 0)),
    )[0]

    assert result.disposition == expected
    assert result.proposal is None


def _contract() -> dict[str, object]:
    return {
        "schema": "tau.dag_contract.v1",
        "dag_id": "gap-expansion-test",
        "goal": {"goal_id": "gap-expansion-test", "goal_version": 1, "goal_hash": "sha256:goal"},
        "target": {"repo": "grahama1970/tau", "target": "gap-expansion-test"},
        "entry_node": "coder",
        "terminal_nodes": ["human"],
        "limits": {"resume": True, "default_timeout_seconds": 30, "max_total_attempts": 4},
        "nodes": [
            {
                "id": "coder",
                "agent": "coder",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": ["tau.node_completion_boundary.v1"],
            },
            {
                "id": "reviewer",
                "agent": "reviewer",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": ["reviewer_verdict"],
            },
        ],
        "edges": [{"from": "coder", "to": "reviewer"}, {"from": "reviewer", "to": "human"}],
        "required_evidence": ["reviewer_verdict"],
        "fail_closed_on": [
            "goal_hash_mismatch",
            "target_changed",
            "unexpected_node",
            "unexpected_edge",
            "missing_required_evidence",
            "max_attempts_exceeded",
        ],
    }


def _boundary() -> dict[str, object]:
    item = {
        "id": "gap-validation",
        "statement": "Add a deterministic validator before final review.",
        "evidence_refs": [{"kind": "accepted_output", "id": "coder"}],
        "proposed_node": {
            "id": "gap-coder-gap-validation",
            "role": "validator",
            "adapter": "local",
            "output_evidence": ["validation_receipt"],
            "max_attempts": 1,
        },
        "requested_paths": ["src/tau_coding/gap_expansion.py"],
        "requested_capabilities": ["read", "validate"],
        "requested_resources": ["local-filesystem"],
        "data_classes": ["public"],
        "side_effect_class": "none",
        "budget": {"max_attempts": 1, "max_seconds": 10, "max_tokens": 1000, "max_cost_usd": 0},
        "scope_claim": {"claim": "in scope", "confidence": 1.0},
    }
    return {
        "schema": "tau.node_completion_boundary.v1",
        "goal_hash": "sha256:runtime-goal",
        "plan_sha256": "sha256:plan",
        "node_id": "coder",
        "attempt_id": "attempt-001",
        "checked_scope": [{"id": "checked", "statement": "checked", "evidence_refs": []}],
        "not_checked": [{"id": "not", "statement": "not checked", "evidence_refs": []}],
        "assumptions": [{"id": "assumption", "statement": "assumption", "evidence_refs": []}],
        "known_unknowns": [{"id": "unknown", "statement": "unknown", "evidence_refs": []}],
        "evidence_gaps": [item],
        "recommended_followups": [{"id": "follow", "statement": "follow", "evidence_refs": []}],
        "proves": [{"id": "proves", "statement": "proves", "evidence_refs": []}],
        "does_not_prove": [
            {"id": "does-not-prove", "statement": "does not prove", "evidence_refs": []}
        ],
    }


def _envelope() -> dict[str, object]:
    return {
        "schema": EXPANSION_ENVELOPE_SCHEMA,
        "permitted_parent_nodes": ["coder"],
        "permitted_parent_roles": ["coder"],
        "permitted_child_roles": ["validator"],
        "permitted_adapters": ["local"],
        "allowed_paths": ["src/tau_coding/gap_expansion.py"],
        "allowed_capabilities": ["read", "validate"],
        "allowed_resources": ["local-filesystem"],
        "allowed_data_classes": ["public"],
        "allowed_side_effect_classes": ["none"],
        "max_added_nodes": 1,
        "max_attempts": 1,
        "max_seconds": 30,
        "max_tokens": 2000,
        "max_cost_usd": 0,
        "human_approval_required": False,
    }


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
