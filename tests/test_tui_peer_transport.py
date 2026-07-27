import json
from pathlib import Path

from tau_coding.tui.peer_queue import DurablePeerQueue, build_peer_envelope
from tau_coding.tui.peer_transport import PeerTransportServer, post_peer_message, read_sse_once


def test_peer_transport_delivers_bidirectional_sse_within_one_second(tmp_path: Path) -> None:
    server_a = PeerTransportServer(harness_id="tau-a", queue_path=tmp_path / "tau-a.json")
    server_b = PeerTransportServer(harness_id="tau-b", queue_path=tmp_path / "tau-b.json")
    server_a.start()
    server_b.start()
    try:
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

        post_peer_message(target_url=server_b.url, envelope=envelope_ab, timeout_seconds=1.0)
        post_peer_message(target_url=server_a.url, envelope=envelope_ba, timeout_seconds=1.0)

        sse_b = read_sse_once(source_url=server_b.url, timeout_seconds=1.0)
        sse_a = read_sse_once(source_url=server_a.url, timeout_seconds=1.0)
    finally:
        server_a.close()
        server_b.close()

    assert sse_b is not None
    assert sse_a is not None
    assert "event: peer-message" in sse_b
    assert "event: peer-message" in sse_a
    assert _sse_payload(sse_b)["target_harness"] == "tau-b"
    assert _sse_payload(sse_a)["target_harness"] == "tau-a"


def test_peer_transport_queue_drains_only_when_idle_and_persists(tmp_path: Path) -> None:
    queue_path = tmp_path / "tau-b.json"
    server = PeerTransportServer(harness_id="tau-b", queue_path=queue_path)
    server.start()
    try:
        post_peer_message(
            target_url=server.url,
            envelope=build_peer_envelope(
                envelope_id="a-to-b",
                source_harness="tau-a",
                target_harness="tau-b",
                goal_hash="sha256:g",
                kind="work_order",
                payload={"task": "patch bug"},
            ),
            timeout_seconds=1.0,
        )
        assert server.queue.drain_idle(idle=False) == []
        assert server.queue.snapshot()["items"][0]["state"] == "queued"

        drained = server.queue.drain_idle(idle=True)
    finally:
        server.close()

    assert drained[0]["state"] == "awaiting_approval"
    assert drained[0]["approval_gate"]["status"] == "BLOCKED"
    restarted = DurablePeerQueue(queue_path, harness_id="tau-b")
    assert restarted.snapshot()["items"][0]["state"] == "awaiting_approval"


def _sse_payload(frame: str) -> dict[str, object]:
    data_line = next(line for line in frame.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert isinstance(payload, dict)
    return payload
