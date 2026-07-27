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


def test_peer_queue_idle_drain_writes_only_to_scratch_root(tmp_path: Path) -> None:
    queue_path = tmp_path / "queue.json"
    scratch_root = tmp_path / "scratch"
    queue = DurablePeerQueue(queue_path, harness_id="tau-b")
    queue.enqueue(
        build_peer_envelope(
            envelope_id="../item-1",
            source_harness="tau-a",
            target_harness="tau-b",
            goal_hash="sha256:g",
            kind="work_order",
            payload={"patch": "diff --git a/demo.txt b/demo.txt\n"},
        )
    )

    drained = queue.drain_idle(idle=True, scratch_root=scratch_root)

    scratch = drained[0]["scratch_worktree"]
    scratch_path = Path(scratch["path"])
    assert scratch_path.is_relative_to(scratch_root.resolve())
    assert Path(scratch["artifacts"]["work_order"]).is_file()
    assert Path(scratch["artifacts"]["candidate_patch"]).read_text(encoding="utf-8").startswith(
        "diff --git"
    )
    assert not (tmp_path / "item-1").exists()
    restarted = DurablePeerQueue(queue_path, harness_id="tau-b")
    assert restarted.snapshot()["items"][0]["scratch_worktree"]["confined_to"] == str(
        scratch_root.resolve()
    )


def test_sse_event_serializes_json_payload() -> None:
    event = sse_event(
        event="peer-message",
        event_id="item-1",
        data={"schema": "tau.tui_peer_envelope.v1", "target_harness": "tau-b"},
    )

    assert event.startswith("id: item-1\nevent: peer-message\n")
    data_line = next(line for line in event.splitlines() if line.startswith("data: "))
    assert json.loads(data_line.removeprefix("data: "))["target_harness"] == "tau-b"
