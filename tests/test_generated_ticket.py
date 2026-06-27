import json
from pathlib import Path

from tau_coding.generated_ticket import (
    TAU_AGENT_COMMON_SCHEMA,
    TAU_AGENT_HANDOFF_SCHEMA,
    TAU_GENERATED_TICKET_SCHEMA,
    derived_labels,
    load_agent_registry_ids,
    project_agent_handoff,
    project_agent_handoff_chain,
    run_agent_handoff_loop,
    validate_agent_handoff,
    validate_generated_ticket,
    validate_generated_ticket_file,
    write_agent_handoff_chain_receipt,
    write_agent_handoff_loop_receipt,
    write_agent_handoff_projection_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "experiments" / "goal-locked-subagents"
FIXTURES = CONTRACT_ROOT / "fixtures"
SCHEMAS = CONTRACT_ROOT / "schemas"


def test_minimal_schema_artifacts_have_tau_ids() -> None:
    common = json.loads((SCHEMAS / "tau.agent_common.v1.schema.json").read_text())
    handoff = json.loads((SCHEMAS / "tau.agent_handoff.v1.schema.json").read_text())
    generated = json.loads((SCHEMAS / "tau.generated_ticket.v1.schema.json").read_text())

    assert common["$id"] == TAU_AGENT_COMMON_SCHEMA
    assert handoff["$id"] == TAU_AGENT_HANDOFF_SCHEMA
    assert generated["$id"] == TAU_GENERATED_TICKET_SCHEMA
    assert generated["properties"]["schema"]["const"] == TAU_GENERATED_TICKET_SCHEMA


def test_valid_generated_ticket_derives_github_create_projection() -> None:
    result = validate_generated_ticket_file(
        FIXTURES / "valid-generated-ticket.json",
        active_goal_hash="sha256:active-goal",
    )

    assert result.ok is True
    assert result.errors == ()
    assert result.next_agent == "reviewer"
    assert result.github_create == {
        "kind": "issue",
        "title": "Review Tau generated-ticket contract evidence",
        "body": "Review the generated-ticket contract evidence.",
        "labels": ["agent-work", "next:reviewer", "executor:either"],
    }


def test_generated_ticket_does_not_require_agent_authored_labels() -> None:
    payload = json.loads((FIXTURES / "valid-generated-ticket.json").read_text())

    assert "labels" not in payload["ticket"]
    assert "labels" not in payload["next_agent"]
    assert "create" not in payload["github"]

    result = validate_generated_ticket(payload, active_goal_hash="sha256:active-goal")

    assert result.ok is True
    assert result.github_create is not None
    assert result.github_create["labels"] == ["agent-work", "next:reviewer", "executor:either"]


def test_generated_ticket_defaults_executor_to_either() -> None:
    assert derived_labels("reviewer", None) == (
        "agent-work",
        "next:reviewer",
        "executor:either",
    )


def test_generated_ticket_refuses_unknown_next_agent() -> None:
    result = validate_generated_ticket_file(
        FIXTURES / "invalid-generated-ticket-unknown-next-agent.json",
        active_goal_hash="sha256:active-goal",
    )

    assert result.ok is False
    assert result.github_create is None
    assert "next_agent.name must be one of" in "\n".join(result.errors)


def test_generated_ticket_refuses_goal_hash_change() -> None:
    payload = json.loads((FIXTURES / "valid-generated-ticket.json").read_text())
    payload["goal"]["goal_hash"] = "sha256:changed-goal"

    result = validate_generated_ticket(payload, active_goal_hash="sha256:active-goal")

    assert result.ok is False
    assert "generated ticket may not change goal.goal_hash" in result.errors


def test_generated_ticket_refuses_unauthorized_ticket_creator() -> None:
    payload = json.loads((FIXTURES / "valid-generated-ticket.json").read_text())
    payload["previous_subagent"] = "coder"

    result = validate_generated_ticket(payload, active_goal_hash="sha256:active-goal")

    assert result.ok is False
    assert "previous_subagent may not create tickets: coder" in result.errors


def test_agent_handoff_accepts_monitor_sparta_lane_agents() -> None:
    payload = _valid_agent_handoff_payload()

    result = validate_agent_handoff(payload, active_goal_hash="sha256:active-goal")

    assert result.ok is True
    assert result.next_agent == "prompt-health-auditor"


def test_agent_handoff_refuses_unknown_monitor_sparta_route() -> None:
    payload = _valid_agent_handoff_payload()
    payload["context"] = {"summary": "Bad route.", "artifacts": []}
    payload["result"] = {"status": "NEEDS_AGENT", "summary": "Bad route.", "evidence": []}
    payload["rationale"] = "Unknown owner should be refused."
    payload["next_agent"] = {
        "name": "unknown-lane-agent",
        "reason": "This agent is not registered.",
    }
    payload["required_evidence"] = []
    payload["stop_condition"] = "Tau refuses routing."

    result = validate_agent_handoff(payload, active_goal_hash="sha256:active-goal")

    assert result.ok is False
    assert "next_agent.name must be one of" in "\n".join(result.errors)


def test_agent_registry_loader_reads_active_agent_ids(tmp_path: Path) -> None:
    active = tmp_path / "external-agent"
    inactive = tmp_path / "deprecated-agent"
    active.mkdir()
    inactive.mkdir()
    (active / "AGENTS.md").write_text(
        "---\nid: external-agent-alias\nkind: worker\n---\n# External agent\n",
        encoding="utf-8",
    )
    (inactive / "AGENTS.md").write_text(
        "---\nid: deprecated-agent\nactive: false\n---\n# Deprecated\n",
        encoding="utf-8",
    )

    agent_ids = load_agent_registry_ids(tmp_path)

    assert "external-agent" in agent_ids
    assert "external-agent-alias" in agent_ids
    assert "deprecated-agent" not in agent_ids


def test_agent_handoff_accepts_agent_registry_route(tmp_path: Path) -> None:
    agent_dir = tmp_path / "external-agent"
    agent_dir.mkdir()
    (agent_dir / "AGENTS.md").write_text(
        "---\nid: external-agent\nkind: worker\n---\n# External agent\n",
        encoding="utf-8",
    )
    payload = _valid_agent_handoff_payload()
    payload["next_agent"] = {
        "name": "external-agent",
        "executor": "either",
        "reason": "This route is supplied by the external registry.",
    }

    without_registry = validate_agent_handoff(payload, active_goal_hash="sha256:active-goal")
    with_registry = validate_agent_handoff(
        payload,
        active_goal_hash="sha256:active-goal",
        agent_registry_root=tmp_path,
    )

    assert without_registry.ok is False
    assert "next_agent.name must be one of" in "\n".join(without_registry.errors)
    assert with_registry.ok is True
    assert with_registry.next_agent == "external-agent"


def test_agent_handoff_projection_derives_comment_and_labels() -> None:
    payload = _valid_agent_handoff_payload()

    projection = project_agent_handoff(payload, active_goal_hash="sha256:active-goal")

    assert projection.ok is True
    assert projection.dry_run is True
    assert projection.next_agent == "prompt-health-auditor"
    assert projection.target == {
        "repo": "grahama1970/chatgpt-lab",
        "target": "issue#123",
    }
    assert projection.labels == {
        "add": ["agent-work", "next:prompt-health-auditor", "executor:either"],
        "remove": ["agent-active", "agent-blocked"],
    }
    assert projection.comment is not None
    assert "<!-- tau-agent-handoff:v1 -->" in projection.comment["body"]
    assert '"schema": "tau.agent_handoff.v1"' in projection.comment["body"]
    assert "- Next agent: `prompt-health-auditor`" in projection.comment["body"]


def test_agent_handoff_projection_refuses_stale_goal_hash() -> None:
    payload = _valid_agent_handoff_payload()
    payload["goal"]["goal_hash"] = "sha256:stale-goal"

    projection = project_agent_handoff(payload, active_goal_hash="sha256:active-goal")

    assert projection.ok is False
    assert projection.comment is None
    assert projection.labels is None
    assert "agent handoff may not change goal.goal_hash" in projection.errors


def test_agent_handoff_projection_receipt_records_refusal(tmp_path: Path) -> None:
    payload = _valid_agent_handoff_payload()
    payload["next_agent"]["name"] = "unknown-lane-agent"
    receipt_path = tmp_path / "handoff-projection" / "receipt.json"

    projection = write_agent_handoff_projection_receipt(
        payload,
        receipt_path,
        active_goal_hash="sha256:active-goal",
    )
    receipt = json.loads(receipt_path.read_text())

    assert projection.ok is False
    assert receipt["schema"] == "tau.agent_handoff_projection_receipt.v1"
    assert receipt["ok"] is False
    assert receipt["dry_run"] is True
    assert receipt["comment"] is None
    assert "next_agent.name must be one of" in "\n".join(receipt["errors"])


def test_agent_handoff_projection_receipt_records_dry_run_success(tmp_path: Path) -> None:
    payload = _valid_agent_handoff_payload()
    receipt_path = tmp_path / "handoff-projection" / "receipt.json"

    projection = write_agent_handoff_projection_receipt(
        payload,
        receipt_path,
        active_goal_hash="sha256:active-goal",
    )
    receipt = json.loads(receipt_path.read_text())

    assert projection.ok is True
    assert receipt["ok"] is True
    assert receipt["dry_run"] is True
    assert receipt["target"]["target"] == "issue#123"
    assert receipt["labels"]["add"] == [
        "agent-work",
        "next:prompt-health-auditor",
        "executor:either",
    ]
    assert "<!-- tau-agent-handoff:v1 -->" in receipt["comment"]["body"]


def test_agent_handoff_chain_accepts_continuous_two_step_route() -> None:
    first = _valid_agent_handoff_payload()
    second = _valid_agent_handoff_payload()
    second["previous_subagent"] = "prompt-health-auditor"
    second["context"]["summary"] = "Prompt health approved the payload."
    second["result"] = {
        "status": "COMPLETED",
        "summary": "Prompt review produced approval evidence.",
        "evidence": ["/tmp/petey/prompt-health.json"],
    }
    second["next_agent"] = {
        "name": "qra-auditor",
        "executor": "either",
        "reason": "The QRA lane can continue after prompt approval.",
    }

    chain = project_agent_handoff_chain([first, second], active_goal_hash="sha256:active-goal")

    assert chain.ok is True
    assert chain.dry_run is True
    assert chain.handoff_count == 2
    assert len(chain.projections) == 2
    assert chain.projections[0]["next_agent"] == "prompt-health-auditor"
    assert chain.projections[1]["next_agent"] == "qra-auditor"


def test_agent_handoff_chain_refuses_route_discontinuity() -> None:
    first = _valid_agent_handoff_payload()
    second = _valid_agent_handoff_payload()
    second["previous_subagent"] = "qra-auditor"

    chain = project_agent_handoff_chain([first, second], active_goal_hash="sha256:active-goal")

    assert chain.ok is False
    assert chain.handoff_count == 2
    assert "previous_subagent must equal prior next_agent" in "\n".join(chain.errors)


def test_agent_handoff_chain_receipt_writes_per_handoff_artifacts(tmp_path: Path) -> None:
    first = _valid_agent_handoff_payload()
    second = _valid_agent_handoff_payload()
    second["previous_subagent"] = "prompt-health-auditor"
    second["next_agent"] = {
        "name": "qra-auditor",
        "executor": "either",
        "reason": "The QRA lane can continue after prompt approval.",
    }
    receipt_dir = tmp_path / "chain"

    chain = write_agent_handoff_chain_receipt(
        [first, second],
        receipt_dir,
        active_goal_hash="sha256:active-goal",
    )
    chain_receipt = json.loads((receipt_dir / "chain-receipt.json").read_text())

    assert chain.ok is True
    assert chain.receipt_dir == str(receipt_dir)
    assert chain.artifacts == (
        str(receipt_dir / "handoff-001.receipt.json"),
        str(receipt_dir / "handoff-002.receipt.json"),
    )
    assert chain_receipt["schema"] == "tau.agent_handoff_chain_receipt.v1"
    assert chain_receipt["ok"] is True
    assert chain_receipt["artifacts"] == list(chain.artifacts)
    assert (receipt_dir / "handoff-001.receipt.json").exists()
    assert (receipt_dir / "handoff-002.receipt.json").exists()


def test_agent_handoff_loop_follows_next_agent_response_map() -> None:
    start = _valid_agent_handoff_payload()
    reviewer = _valid_agent_handoff_payload()
    reviewer["previous_subagent"] = "prompt-health-auditor"
    reviewer["result"] = {
        "status": "PASS",
        "summary": "Prompt health accepted the evidence.",
        "evidence": ["/tmp/petey/review.json"],
    }
    reviewer["next_agent"] = {
        "name": "human",
        "executor": "human",
        "reason": "Human decides whether to continue after the dry-run proof.",
    }

    loop = run_agent_handoff_loop(
        start,
        {"prompt-health-auditor": reviewer},
        active_goal_hash="sha256:active-goal",
        max_steps=3,
    )

    assert loop.ok is True
    assert loop.status == "WAITING"
    assert loop.step_count == 2
    assert loop.terminal_agent == "human"
    assert loop.stop_reason == "next_agent_is_human"
    assert loop.projections[0]["next_agent"] == "prompt-health-auditor"
    assert loop.projections[1]["next_agent"] == "human"


def test_agent_handoff_loop_waits_when_response_is_missing() -> None:
    start = _valid_agent_handoff_payload()

    loop = run_agent_handoff_loop(
        start,
        {},
        active_goal_hash="sha256:active-goal",
        max_steps=3,
    )

    assert loop.ok is True
    assert loop.status == "WAITING"
    assert loop.step_count == 1
    assert loop.terminal_agent == "prompt-health-auditor"
    assert loop.stop_reason == "missing_agent_response"


def test_agent_handoff_loop_refuses_route_discontinuity() -> None:
    start = _valid_agent_handoff_payload()
    reviewer = _valid_agent_handoff_payload()
    reviewer["previous_subagent"] = "qra-auditor"

    loop = run_agent_handoff_loop(
        start,
        {"prompt-health-auditor": reviewer},
        active_goal_hash="sha256:active-goal",
        max_steps=3,
    )

    assert loop.ok is False
    assert loop.status == "BLOCKED"
    assert loop.stop_reason == "route_discontinuity"
    assert "previous_subagent must equal prior next_agent" in "\n".join(loop.errors)


def test_agent_handoff_loop_receipt_writes_step_artifacts(tmp_path: Path) -> None:
    start = _valid_agent_handoff_payload()
    reviewer = _valid_agent_handoff_payload()
    reviewer["previous_subagent"] = "prompt-health-auditor"
    reviewer["next_agent"] = {
        "name": "human",
        "executor": "human",
        "reason": "Human decision required.",
    }
    receipt_dir = tmp_path / "loop"

    loop = write_agent_handoff_loop_receipt(
        start,
        {"prompt-health-auditor": reviewer},
        receipt_dir,
        active_goal_hash="sha256:active-goal",
        max_steps=3,
    )
    loop_receipt = json.loads((receipt_dir / "loop-receipt.json").read_text())

    assert loop.ok is True
    assert loop.receipt_dir == str(receipt_dir)
    assert loop.artifacts == (
        str(receipt_dir / "loop-step-001.receipt.json"),
        str(receipt_dir / "loop-step-002.receipt.json"),
    )
    assert loop_receipt["schema"] == "tau.agent_handoff_loop_receipt.v1"
    assert loop_receipt["stop_reason"] == "next_agent_is_human"
    assert loop_receipt["artifacts"] == list(loop.artifacts)
    assert (receipt_dir / "loop-step-001.receipt.json").exists()
    assert (receipt_dir / "loop-step-002.receipt.json").exists()


def _valid_agent_handoff_payload() -> dict:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {
            "repo": "grahama1970/chatgpt-lab",
            "target": "issue#123",
        },
        "goal": {
            "goal_id": "goal-monitor-sparta",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "previous_subagent": "qra-auditor",
        "context": {
            "summary": "Qbert cannot continue without prompt-health approval.",
            "artifacts": ["/tmp/qbert/receipt.json"],
        },
        "result": {
            "status": "NEEDS_AGENT",
            "summary": "Prompt approval is missing.",
            "evidence": ["/tmp/qbert/prompt_approval_check.json"],
        },
        "rationale": "Petey must approve the prompt payload before Qbert runs create-qras.",
        "next_agent": {
            "name": "prompt-health-auditor",
            "executor": "either",
            "reason": "Prompt health precedes QRA generation.",
        },
        "required_evidence": ["review-prompt PASS receipt"],
        "stop_condition": "Petey writes a matching prompt-health approval row.",
    }
