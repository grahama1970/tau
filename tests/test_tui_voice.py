import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from tau_coding.tui.voice import (
    VOICE_RENDER_REQUEST_SCHEMA,
    VoiceRunSnapshot,
    VoiceSurface,
    build_voice_render_request,
    interpret_spoken_command,
)


def test_voice_render_posts_versioned_envelope_to_tau_voice_render(tmp_path: Path) -> None:
    server = StubVoiceServer(tmp_path, next_text="what is blocked")
    server.start()
    try:
        surface = VoiceSurface(chatterbox_url=server.url, stt_url=server.url)
        receipt = surface.announce_state_change(
            _approval_snapshot(), conversation_id="conv-1", turn_id="turn-1"
        )
    finally:
        server.close()

    assert receipt["status"] == "PASS"
    assert receipt["approval_gate_satisfied"] is False
    assert receipt["request_schema"] == VOICE_RENDER_REQUEST_SCHEMA
    env = server.posts["/tau/voice-render"][0]
    assert env["schema"] == VOICE_RENDER_REQUEST_SCHEMA
    assert env["conversation_id"] == "conv-1" and env["turn_id"] == "turn-1"
    assert env["run_id"] == "run-1"
    assert env["speakable_chunks"][0]["interruptible"] is True
    # raw-hex sha256 (no prefix) to match the Chatterbox sha256_matches gates
    chunk = env["speakable_chunks"][0]
    assert chunk["text_sha256"] == hashlib.sha256(chunk["text"].encode()).hexdigest()
    assert env["question_text_sha256"] == hashlib.sha256(env["question_text"].encode()).hexdigest()


def test_build_envelope_carries_lineage_and_no_approval_authority() -> None:
    env = build_voice_render_request(
        _approval_snapshot(), conversation_id="c", turn_id="t", tone="calm"
    )
    assert env["memory_route_decision"]["workflow"] == "Publish qualification"
    assert env["external_evidence"]["tau_run_id"] == "run-1"
    assert env["interruptible"] is True
    assert env["voice_delivery"]["approval_required"] is True
    # nothing in the envelope authorizes a side effect
    assert "side_effect_authorized" not in json.dumps(env)


def test_voice_spoken_query_matches_tui_snapshot(tmp_path: Path) -> None:
    server = StubVoiceServer(tmp_path, next_text="what is blocked")
    server.start()
    try:
        surface = VoiceSurface(chatterbox_url=server.url, stt_url=server.url)
        result = surface.poll_spoken_query(_approval_snapshot())
    finally:
        server.close()

    assert result.status == "PASS"
    assert result.response_text == _approval_snapshot().spoken_summary()
    assert result.side_effect_authorized is False
    assert result.approval_gate_satisfied is False


def test_voice_turn_controls_hit_real_endpoints(tmp_path: Path) -> None:
    server = StubVoiceServer(tmp_path, next_text="")
    server.start()
    try:
        surface = VoiceSurface(chatterbox_url=server.url, stt_url=server.url)
        cancel = surface.cancel_turn("turn-9", reason="user interrupted")
        surface.duck_playback("turn-9")
        surface.stop_playback("turn-9")
        surface.interrupt_output("turn-9")  # back-compat alias -> cancel
    finally:
        server.close()

    assert cancel["status"] == "PASS"
    assert server.posts["/turn/turn-9/cancel"][0]["reason"] == "user interrupted"
    assert "/playback/turn-9/duck" in server.posts
    assert "/playback/turn-9/stop" in server.posts
    assert len(server.posts["/turn/turn-9/cancel"]) == 2  # explicit + alias


def test_voice_startup_degrades_when_services_absent() -> None:
    surface = VoiceSurface(chatterbox_url=None, stt_url=None)

    receipt = surface.startup_receipt()

    assert receipt["status"] == "DEGRADED"
    assert receipt["voice_enabled"] is False
    assert receipt["degraded_reasons"] == [
        "chatterbox_url_missing",
        "realtimestt_url_missing",
    ]


def test_voice_approval_phrase_never_satisfies_gate() -> None:
    result = interpret_spoken_command("yes approve this now", _approval_snapshot())

    assert result.status == "BLOCKED"
    assert result.command == "approval_rejected"
    assert result.side_effect_authorized is False
    assert result.approval_gate_satisfied is False


def _approval_snapshot() -> VoiceRunSnapshot:
    return VoiceRunSnapshot(
        workflow="Publish qualification",
        run_id="run-1",
        state="WAITING_ON_APPROVAL",
        active_node="release-gate",
        blocker="operator decision",
        approval_required=True,
        required_decision="release approval",
    )


class StubVoiceServer:
    def __init__(self, tmp_path: Path, *, next_text: str) -> None:
        self.posts: dict[str, list[dict[str, Any]]] = {}
        self.next_text = next_text
        handler = self._handler()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.stub = self  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=2)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                stub: StubVoiceServer = self.server.stub  # type: ignore[attr-defined]
                if self.path != "/events/next":
                    self.send_error(404)
                    return
                self._write_json({"text": stub.next_text})

            def do_POST(self) -> None:  # noqa: N802
                stub: StubVoiceServer = self.server.stub  # type: ignore[attr-defined]
                length = int(self.headers.get("content-length", "0") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                stub.posts.setdefault(self.path, []).append(payload)
                self._write_json({"ok": True})

            def log_message(self, format: str, *args: object) -> None:
                return

            def _write_json(self, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, sort_keys=True).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler
