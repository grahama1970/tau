"""Optional voice surface over Tau TUI state."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

VOICE_RECEIPT_SCHEMA = "tau.tui_voice_surface_receipt.v1"


@dataclass(frozen=True, slots=True)
class VoiceRunSnapshot:
    """Authoritative run state projected from the TUI/run-status layer."""

    workflow: str
    run_id: str
    state: str
    active_node: str
    blocker: str | None = None
    approval_required: bool = False
    required_decision: str | None = None

    def spoken_summary(self) -> str:
        parts = [
            f"Workflow {self.workflow}",
            f"run {self.run_id}",
            f"is {self.state}",
            f"at node {self.active_node}",
        ]
        if self.blocker:
            parts.append(f"blocked by {self.blocker}")
        if self.required_decision:
            parts.append(f"decision needed: {self.required_decision}")
        return ", ".join(parts) + "."


@dataclass(frozen=True, slots=True)
class VoiceCommandResult:
    """Result of interpreting one recognized utterance."""

    status: str
    response_text: str
    side_effect_authorized: bool
    approval_gate_satisfied: bool
    command: str

    def to_receipt(self) -> dict[str, Any]:
        return {
            "schema": VOICE_RECEIPT_SCHEMA,
            "status": self.status,
            "command": self.command,
            "response_text": self.response_text,
            "side_effect_authorized": self.side_effect_authorized,
            "approval_gate_satisfied": self.approval_gate_satisfied,
        }


class VoiceSurface:
    """Optional Chatterbox/RealtimeSTT bridge that never owns Tau state."""

    def __init__(
        self,
        *,
        chatterbox_url: str | None,
        stt_url: str | None,
        timeout_seconds: float = 1.0,
    ) -> None:
        self.chatterbox_url = _normalize_url(chatterbox_url)
        self.stt_url = _normalize_url(stt_url)
        self.timeout_seconds = timeout_seconds

    def startup_receipt(self) -> dict[str, Any]:
        degraded = []
        if self.chatterbox_url is None:
            degraded.append("chatterbox_url_missing")
        if self.stt_url is None:
            degraded.append("realtimestt_url_missing")
        return {
            "schema": VOICE_RECEIPT_SCHEMA,
            "status": "DEGRADED" if degraded else "PASS",
            "voice_enabled": not degraded,
            "degraded_reasons": degraded,
            "claims": {
                "proves": [
                    "Tau voice startup degrades explicitly when optional services are absent."
                ],
                "does_not_prove": [
                    "Chatterbox or RealtimeSTT service health.",
                    "Voice authentication or human approval.",
                ],
            },
        }

    def announce_state_change(self, snapshot: VoiceRunSnapshot) -> dict[str, Any]:
        if self.chatterbox_url is None:
            return self._degraded("chatterbox_url_missing")
        if not _should_announce(snapshot):
            return {"schema": VOICE_RECEIPT_SCHEMA, "status": "SKIPPED", "reason": "not_notable"}
        text = _announcement_text(snapshot)
        response = self._post_json(
            self.chatterbox_url,
            "speak",
            {
                "text": text,
                "interruptible": True,
                "source": "tau",
                "run_id": snapshot.run_id,
            },
        )
        return {
            "schema": VOICE_RECEIPT_SCHEMA,
            "status": "PASS",
            "action": "announce",
            "text": text,
            "service_response": response,
            "approval_gate_satisfied": False,
        }

    def interrupt_output(self) -> dict[str, Any]:
        if self.chatterbox_url is None:
            return self._degraded("chatterbox_url_missing")
        response = self._post_json(self.chatterbox_url, "turn/interrupt", {"source": "tau"})
        return {
            "schema": VOICE_RECEIPT_SCHEMA,
            "status": "PASS",
            "action": "interrupt",
            "service_response": response,
        }

    def poll_spoken_query(self, snapshot: VoiceRunSnapshot) -> VoiceCommandResult:
        if self.stt_url is None:
            return VoiceCommandResult(
                status="DEGRADED",
                response_text="Voice input is disabled because RealtimeSTT is unavailable.",
                side_effect_authorized=False,
                approval_gate_satisfied=False,
                command="degraded",
            )
        payload = self._get_json(self.stt_url, "events/next")
        text = str(payload.get("text") or "")
        return interpret_spoken_command(text, snapshot)

    def _post_json(self, base_url: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/{path}",
            data=body,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                return _read_json_response(response.read())
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"voice service request failed: {exc}") from exc

    def _get_json(self, base_url: str, path: str) -> dict[str, Any]:
        request = urllib.request.Request(f"{base_url}/{path}", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                return _read_json_response(response.read())
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"voice service request failed: {exc}") from exc

    def _degraded(self, reason: str) -> dict[str, Any]:
        return {
            "schema": VOICE_RECEIPT_SCHEMA,
            "status": "DEGRADED",
            "voice_enabled": False,
            "degraded_reasons": [reason],
        }


def interpret_spoken_command(text: str, snapshot: VoiceRunSnapshot) -> VoiceCommandResult:
    normalized = " ".join(text.casefold().split())
    if any(word in normalized for word in ("approve", "approved", "authorize", "yes")):
        return VoiceCommandResult(
            status="BLOCKED",
            response_text=(
                "Voice cannot approve this action. Use the authenticated approval path."
            ),
            side_effect_authorized=False,
            approval_gate_satisfied=False,
            command="approval_rejected",
        )
    if "what" in normalized or "status" in normalized or "blocked" in normalized:
        return VoiceCommandResult(
            status="PASS",
            response_text=snapshot.spoken_summary(),
            side_effect_authorized=False,
            approval_gate_satisfied=False,
            command="query_status",
        )
    return VoiceCommandResult(
        status="IGNORED",
        response_text="Voice command not recognized.",
        side_effect_authorized=False,
        approval_gate_satisfied=False,
        command="ignored",
    )


def _announcement_text(snapshot: VoiceRunSnapshot) -> str:
    if snapshot.approval_required:
        decision = snapshot.required_decision or "human approval"
        return f"Tau needs {decision} for {snapshot.workflow}, node {snapshot.active_node}."
    return snapshot.spoken_summary()


def _should_announce(snapshot: VoiceRunSnapshot) -> bool:
    return snapshot.approval_required or snapshot.state in {"BLOCKED", "COMPLETED", "FAILED"}


def _normalize_url(url: str | None) -> str | None:
    if url is None or not url.strip():
        return None
    return url.rstrip("/")


def _read_json_response(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("voice service response must be a JSON object")
    return payload
