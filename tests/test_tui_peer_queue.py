import json
from pathlib import Path

from tau_coding.tui.peer_queue import DurablePeerQueue, build_peer_envelope, sse_event


def test_peer_envelopes_are_addressed_bidirectionally(tmp_path: Path) -> None:
    queue_a = DurablePeerQueue(tmp_path / "a.json", harness_id="tau-a")
    queue_b = DurablePeerQueue(tmp_path / "b.json", harness_id="tau-b")
    envelope_ab = build_peer_envelope(
        envelope_id="a-to-b",
        source_harness="tau-a",
        target_harness="tau-b",
        goal_hash="sha256:g",
        kind="work_order",
        payload={"task": "review patch"},
    )
    envelope_ba = build_peer_envelope(
        envelope_id="b-to-a",
        source_harness="tau-b",
        target_harness="tau-a",
        goal_hash="sha256:g",
        kind="work_order",
        payload={"task": "apply critique"},
    )

    queue_b.enqueue(envelope_ab)
    queue_a.enqueue(envelope_ba)

    assert queue_a.snapshot()["items"][0]["envelope"]["source_harness"] == "tau-b"
    assert queue_b.snapshot()["items"][0]["envelope"]["source_harness"] == "tau-a"


def test_peer_queue_drains_only_when_idle_and_blocks_on_approval(tmp_path: Path) -> None:
    queue = DurablePeerQueue(tmp_path / "queue.json", harness_id="tau-b")
    queue.enqueue(
        build_peer_envelope(
            envelope_id="item-1",
            source_harness="tau-a",
            target_harness="tau-b",
            goal_hash="sha256:g",
            kind="work_order",
            payload={"task": "patch bug"},
        )
    )

    assert queue.drain_idle(idle=False) == []
    busy_snapshot = queue.snapshot()
    assert busy_snapshot["items"][0]["state"] == "queued"

    drained = queue.drain_idle(idle=True)

    assert len(drained) == 1
    item = drained[0]
    assert item["state"] == "awaiting_approval"
    assert item["approval_gate"]["status"] == "BLOCKED"
    assert item["approval_gate"]["required"] is True
    assert queue.snapshot()["items"][0]["state"] == "awaiting_approval"


def test_peer_queue_persists_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "queue.json"
    queue = DurablePeerQueue(path, harness_id="tau-b")
    queue.enqueue(
        build_peer_envelope(
            envelope_id="item-1",
            source_harness="tau-a",
            target_harness="tau-b",
            goal_hash="sha256:g",
            kind="work_order",
            payload={"task": "patch bug"},
        )
    )
    queue.drain_idle(idle=True)

    restarted = DurablePeerQueue(path, harness_id="tau-b")

    assert restarted.snapshot()["items"][0]["state"] == "awaiting_approval"


def test_sse_event_serializes_json_payload() -> None:
    event = sse_event(
        event="peer-message",
        event_id="item-1",
        data={"schema": "tau.tui_peer_envelope.v1", "target_harness": "tau-b"},
    )

    assert event.startswith("id: item-1\nevent: peer-message\n")
    data_line = next(line for line in event.splitlines() if line.startswith("data: "))
    assert json.loads(data_line.removeprefix("data: "))["target_harness"] == "tau-b"
