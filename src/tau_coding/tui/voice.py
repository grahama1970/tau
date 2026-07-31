"""Optional voice surface over Tau TUI state.

The delivery path is the real Chatterbox agent-server contract
(``POST /tau/voice-render`` with a ``tau.voice_render_request.v1`` envelope, and
turn-scoped ``/turn/{id}/cancel`` + ``/playback/{id}/duck|stop`` controls), not
a generic ``/speak`` stub. Tau projects authoritative run/turn state and
lineage into the envelope; voice is never a source of workflow truth and never
an approval authority (spoken approval phrases are refused).
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from tau_coding.voice_contract import (
    VOICE_RENDER_REQUEST_SCHEMA_V2,
    ControlTarget,
    DeliverySettings,
    ResponseControlRegistry,
    ResponseIdentity,
    decide_delivery,
    parse_voice_render_request_v2,
    request_lineage_digest,
)

VOICE_RECEIPT_SCHEMA = "tau.tui_voice_surface_receipt.v1"
VOICE_RENDER_REQUEST_SCHEMA = "tau.voice_render_request.v1"


def _sha256_text(text: str) -> str:
    # Raw hex digest (no prefix) to match the Chatterbox /tau/voice-render
    # sha256_matches gates, which compare against hashlib.sha256(text).hexdigest().
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_voice_render_request(
    snapshot: VoiceRunSnapshot,
    *,
    conversation_id: str,
    turn_id: str,
    superseded_turn_id: str | None = None,
    tone: str | None = None,
    intensity: float | None = None,
    valence: float | None = None,
    delivery_stage: str | None = None,
) -> dict[str, Any]:
    """Project authoritative run state into a tau.voice_render_request.v1 envelope.

    The envelope carries lineage (run id, question text + hash, memory-route
    metadata, external evidence) so Chatterbox delivery is auditable. It never
    carries approval authority: ``interruptible`` stays true and no field can
    authorize a side effect.
    """

    text = _announcement_text(snapshot)
    chunk = {
        "chunk_id": f"{turn_id}-000",
        "text": text,
        "text_sha256": _sha256_text(text),
        "interruptible": True,
    }
    delivery = _delivery_policy_for(
        snapshot,
        tone=tone,
        intensity=intensity,
        valence=valence,
        delivery_stage=delivery_stage,
    )
    return {
        "schema": VOICE_RENDER_REQUEST_SCHEMA,
        "workflow": snapshot.workflow,
        "run_id": snapshot.run_id,
        "node_id": snapshot.active_node,
        "attempt_id": snapshot.attempt_id,
        "scheduler_journal_sequence": snapshot.scheduler_journal_sequence,
        "state_digest": snapshot.state_digest,
        "event_type": snapshot.event_type,
        "state_transition": snapshot.state_transition,
        "goal_hash": snapshot.goal_hash,
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "superseded_turn_id": superseded_turn_id,
        "route": "tau_voice_render",
        "question_text": text,
        "question_text_sha256": _sha256_text(text),
        "memory_route_decision": {
            "source": snapshot.memory_route_source,
            "workflow": snapshot.workflow,
            "state": snapshot.state,
            "active_node": snapshot.active_node,
        },
        "voice_delivery": {
            "notable": _should_announce(snapshot),
            "approval_required": snapshot.approval_required,
            **delivery,
        },
        "speakable_chunks": [chunk],
        "tone": delivery["tone"],
        "intensity": delivery["intensity"],
        "valence": delivery["valence"],
        "interruptible": True,
        "turn_controls": {
            "cancel": f"/turn/{turn_id}/cancel",
            "duck": f"/playback/{turn_id}/duck",
            "stop": f"/playback/{turn_id}/stop",
        },
        "external_evidence": {
            "tau_run_id": snapshot.run_id,
            "blocker": snapshot.blocker,
            "required_decision": snapshot.required_decision,
            "delivery_policy_source": snapshot.delivery_policy_source,
        },
    }


def build_voice_render_request_v2(
    snapshot: VoiceRunSnapshot,
    *,
    request_id: str,
    conversation_id: str,
    turn_id: str,
    turn_revision: int,
    response_id: str,
    cancel_epoch: int,
    supersedes_response_id: str | None = None,
    tone: str | None = None,
    intensity: float | None = None,
    valence: float | None = None,
    delivery_stage: str | None = None,
) -> dict[str, Any]:
    """Project run state into a tau.voice_render_request.v2 wire envelope.

    The envelope keeps every v1 flat field (same route, permissive v1
    consumers keep working during migration) and adds a strict ``v2`` block
    carrying response identity, source lineage, the requested-vs-effective
    delivery decision, hash-bound segments, and the control target template.
    The result round-trips through :func:`parse_voice_render_request_v2`.
    """

    envelope = build_voice_render_request(
        snapshot,
        conversation_id=conversation_id,
        turn_id=turn_id,
        superseded_turn_id=None,
        tone=tone,
        intensity=intensity,
        valence=valence,
        delivery_stage=delivery_stage,
    )
    text = _announcement_text(snapshot)
    requested = DeliverySettings(
        tone=tone, intensity=intensity, valence=valence, stage=delivery_stage
    )
    decision = decide_delivery(
        state=snapshot.state,
        approval_required=snapshot.approval_required,
        requested=requested,
        run_id=snapshot.run_id,
        state_digest=snapshot.state_digest,
    )
    identity = ResponseIdentity(
        request_id=request_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        turn_revision=turn_revision,
        response_id=response_id,
        cancel_epoch=cancel_epoch,
        supersedes_response_id=supersedes_response_id,
    )
    control_target = ControlTarget(
        conversation_id=conversation_id,
        turn_id=turn_id,
        turn_revision=turn_revision,
        response_id=response_id,
        expected_cancel_epoch=cancel_epoch,
    )
    envelope["schema"] = VOICE_RENDER_REQUEST_SCHEMA_V2
    envelope["v2"] = {
        "identity": identity.model_dump(),
        "lineage": {
            "workflow": snapshot.workflow,
            "run_id": snapshot.run_id,
            "node_id": snapshot.active_node,
            "attempt_id": snapshot.attempt_id,
            "scheduler_journal_sequence": snapshot.scheduler_journal_sequence,
            "state_digest": snapshot.state_digest,
            "goal_hash": snapshot.goal_hash,
            "event_type": snapshot.event_type,
            "state_transition": snapshot.state_transition,
        },
        "delivery_decision": decision.model_dump(),
        "segments": [
            {
                "segment_id": f"{response_id}-000",
                "text": text,
                "text_sha256": _sha256_text(text),
                "delivery": decision.effective_delivery.model_dump(),
                "interruptible": True,
            }
        ],
        "control_target": control_target.model_dump(),
        "extensions": {},
    }
    # Fail-closed self check: the producer never emits an envelope its own
    # strict parser would reject.
    parse_voice_render_request_v2(envelope)
    return envelope


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
    attempt_id: str | None = None
    scheduler_journal_sequence: int | None = None
    state_digest: str | None = None
    event_type: str = "state_change"
    state_transition: str | None = None
    goal_hash: str | None = None
    memory_route_source: str = "tau_run_state"
    delivery_policy_source: str = "tau_voice_default_policy"

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


@dataclass(slots=True)
class VoiceTurnState:
    """Tau-side ownership record for one audible turn."""

    conversation_id: str
    run_id: str
    workflow: str
    turn_id: str
    status: str
    superseded_by: str | None = None
    control_receipts: list[dict[str, Any]] = field(default_factory=list)
    audio_identity: dict[str, Any] = field(default_factory=dict)


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
        self._active_by_conversation: dict[str, VoiceTurnState] = {}
        self._turns: dict[str, VoiceTurnState] = {}
        self._response_registry = ResponseControlRegistry()
        self._response_digests: dict[str, str] = {}

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

    def announce_state_change(
        self,
        snapshot: VoiceRunSnapshot,
        *,
        conversation_id: str,
        turn_id: str,
        tone: str | None = None,
        intensity: float | None = None,
        valence: float | None = None,
        delivery_stage: str | None = None,
    ) -> dict[str, Any]:
        """Render one turn through the real /tau/voice-render contract."""

        if self.chatterbox_url is None:
            return self._degraded("chatterbox_url_missing")
        if not _should_announce(snapshot):
            return {"schema": VOICE_RECEIPT_SCHEMA, "status": "SKIPPED", "reason": "not_notable"}
        superseded_turn_id = self._supersede_active_turn(conversation_id, turn_id)
        envelope = build_voice_render_request(
            snapshot,
            conversation_id=conversation_id,
            turn_id=turn_id,
            superseded_turn_id=superseded_turn_id,
            tone=tone,
            intensity=intensity,
            valence=valence,
            delivery_stage=delivery_stage,
        )
        try:
            response = self._post_json(self.chatterbox_url, "tau/voice-render", envelope)
        except RuntimeError as exc:
            return self._degraded("voice_render_request_failed", detail=str(exc))
        # The service verdict is derived from its own gates, not asserted by Tau.
        service_ok = bool(response.get("ok")) and isinstance(response.get("mocked", False), bool)
        invalid_schema = "ok" not in response
        audio_identity = _audio_identity_from_response(response)
        receipt = {
            "schema": VOICE_RECEIPT_SCHEMA,
            "status": "PASS" if service_ok and not invalid_schema else "DEGRADED",
            "action": "voice_render",
            "turn_id": turn_id,
            "superseded_turn_id": superseded_turn_id,
            "request_schema": VOICE_RENDER_REQUEST_SCHEMA,
            "service_ok": service_ok,
            "service_live": bool(response.get("live")),
            "service_mocked": response.get("mocked"),
            "service_engine": response.get("engine"),
            "effective_emotion": response.get("effective_emotion"),
            "failed_gates": response.get("failed_gates", []),
            "audio_identity": audio_identity,
            "response_identity": _response_identity(response),
            "approval_gate_satisfied": False,
        }
        if invalid_schema:
            receipt["degraded_reasons"] = ["voice_render_response_invalid"]
        state = VoiceTurnState(
            conversation_id=conversation_id,
            run_id=snapshot.run_id,
            workflow=snapshot.workflow,
            turn_id=turn_id,
            status=str(receipt["status"]),
            superseded_by=None,
            audio_identity=audio_identity,
        )
        self._turns[turn_id] = state
        self._active_by_conversation[conversation_id] = state
        return receipt

    def announce_response_v2(
        self,
        snapshot: VoiceRunSnapshot,
        *,
        request_id: str,
        conversation_id: str,
        turn_id: str,
        turn_revision: int,
        response_id: str,
        cancel_epoch: int = 0,
        supersedes_response_id: str | None = None,
        tone: str | None = None,
        intensity: float | None = None,
        valence: float | None = None,
        delivery_stage: str | None = None,
    ) -> dict[str, Any]:
        """Render one response through the v2 contract on the same route.

        The receipt retains ``request_lineage_digest`` (the digest the
        Chatterbox consumer proof must echo, chatterbox#11) and the response
        identity registered for control fencing.
        """

        if self.chatterbox_url is None:
            return self._degraded("chatterbox_url_missing")
        if not _should_announce(snapshot):
            return {"schema": VOICE_RECEIPT_SCHEMA, "status": "SKIPPED", "reason": "not_notable"}
        envelope = build_voice_render_request_v2(
            snapshot,
            request_id=request_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            turn_revision=turn_revision,
            response_id=response_id,
            cancel_epoch=cancel_epoch,
            supersedes_response_id=supersedes_response_id,
            tone=tone,
            intensity=intensity,
            valence=valence,
            delivery_stage=delivery_stage,
        )
        identity = ResponseIdentity.model_validate(envelope["v2"]["identity"])
        registration = self._response_registry.register_response(identity)
        if not registration["accepted"]:
            return {
                "schema": VOICE_RECEIPT_SCHEMA,
                "status": "BLOCKED",
                "action": "voice_render_v2",
                "reason": registration["reason"],
                "response_id": response_id,
            }
        digest = request_lineage_digest(envelope)
        self._response_digests[response_id] = digest
        try:
            response = self._post_json(self.chatterbox_url, "tau/voice-render", envelope)
        except RuntimeError as exc:
            return self._degraded("voice_render_request_failed", detail=str(exc))
        service_ok = bool(response.get("ok")) and isinstance(response.get("mocked", False), bool)
        invalid_schema = "ok" not in response
        receipt = {
            "schema": VOICE_RECEIPT_SCHEMA,
            "status": "PASS" if service_ok and not invalid_schema else "DEGRADED",
            "action": "voice_render_v2",
            "request_schema": VOICE_RENDER_REQUEST_SCHEMA_V2,
            "request_id": request_id,
            "response_id": response_id,
            "turn_id": turn_id,
            "turn_revision": turn_revision,
            "cancel_epoch": cancel_epoch,
            "request_lineage_digest": digest,
            "consumer_lineage_digest": response.get("request_lineage_digest"),
            "consumer_digest_matches": response.get("request_lineage_digest") == digest,
            "service_ok": service_ok,
            "service_live": bool(response.get("live")),
            "service_mocked": response.get("mocked"),
            "delivery_decision": envelope["v2"]["delivery_decision"],
            "approval_gate_satisfied": False,
        }
        if invalid_schema:
            receipt["degraded_reasons"] = ["voice_render_response_invalid"]
        return receipt

    def control_response_v2(
        self, target: ControlTarget, *, action: str, reason: str | None = None
    ) -> dict[str, Any]:
        """Apply a fenced v2 turn control; stale identity never reaches the wire."""

        verdict = self._response_registry.evaluate_control(target, action=action)
        receipt = {
            "schema": VOICE_RECEIPT_SCHEMA,
            "action": f"{action}_v2",
            "control_verdict": verdict,
            "approval_gate_satisfied": False,
        }
        if not verdict["accepted"]:
            return {**receipt, "status": "BLOCKED"}
        if verdict.get("idempotent"):
            return {**receipt, "status": "PASS"}
        wire = self._turn_control(action, target.turn_id, reason)
        return {**receipt, "status": wire.get("status", "DEGRADED"), "wire_receipt": wire}

    def response_lineage_digest(self, response_id: str) -> str | None:
        return self._response_digests.get(response_id)

    def _turn_control(
        self,
        action: str,
        turn_id: str,
        reason: str | None,
        *,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        if self.chatterbox_url is None:
            return self._degraded("chatterbox_url_missing")
        known = self._turns.get(turn_id)
        if run_id is not None and known is not None and known.run_id != run_id:
            return {
                "schema": VOICE_RECEIPT_SCHEMA,
                "status": "BLOCKED",
                "action": action,
                "turn_id": turn_id,
                "run_id": run_id,
                "known_run_id": known.run_id,
                "reason": "wrong_run_for_turn",
            }
        path = f"turn/{turn_id}/cancel" if action == "cancel" else f"playback/{turn_id}/{action}"
        try:
            response = self._post_json(
                self.chatterbox_url, path, {"reason": reason or f"tau_{action}"}
            )
        except RuntimeError as exc:
            return self._degraded(f"{action}_request_failed", detail=str(exc))
        receipt = {
            "schema": VOICE_RECEIPT_SCHEMA,
            "status": "PASS" if response.get("ok", True) else "DEGRADED",
            "action": action,
            "turn_id": turn_id,
            "service_response": response,
            "stale_chunks_should_skip": bool(
                isinstance(response.get("control"), dict)
                and response["control"].get("stale_chunks_should_skip")
            ),
        }
        if known is not None:
            known.control_receipts.append(receipt)
        return receipt

    def cancel_turn(
        self, turn_id: str, *, reason: str | None = None, run_id: str | None = None
    ) -> dict[str, Any]:
        return self._turn_control("cancel", turn_id, reason, run_id=run_id)

    def duck_playback(
        self, turn_id: str, *, reason: str | None = None, run_id: str | None = None
    ) -> dict[str, Any]:
        return self._turn_control("duck", turn_id, reason, run_id=run_id)

    def stop_playback(
        self, turn_id: str, *, reason: str | None = None, run_id: str | None = None
    ) -> dict[str, Any]:
        return self._turn_control("stop", turn_id, reason, run_id=run_id)

    def interrupt_output(self, turn_id: str, *, reason: str | None = None) -> dict[str, Any]:
        """Back-compat alias: interruption maps to a turn cancel."""

        return self.cancel_turn(turn_id, reason=reason)

    def turn_lineage_receipt(self) -> dict[str, Any]:
        return {
            "schema": VOICE_RECEIPT_SCHEMA,
            "status": "PASS",
            "action": "turn_lineage",
            "turns": [
                {
                    "conversation_id": turn.conversation_id,
                    "run_id": turn.run_id,
                    "workflow": turn.workflow,
                    "turn_id": turn.turn_id,
                    "status": turn.status,
                    "superseded_by": turn.superseded_by,
                    "audio_identity": turn.audio_identity,
                    "control_receipt_count": len(turn.control_receipts),
                }
                for turn in self._turns.values()
            ],
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

    def _supersede_active_turn(self, conversation_id: str, new_turn_id: str) -> str | None:
        previous = self._active_by_conversation.get(conversation_id)
        if previous is None or previous.turn_id == new_turn_id:
            return None
        previous.superseded_by = new_turn_id
        cancel = self.cancel_turn(
            previous.turn_id,
            reason=f"superseded_by:{new_turn_id}",
            run_id=previous.run_id,
        )
        previous.control_receipts.append(cancel)
        return previous.turn_id

    def _degraded(self, reason: str, *, detail: str | None = None) -> dict[str, Any]:
        receipt = {
            "schema": VOICE_RECEIPT_SCHEMA,
            "status": "DEGRADED",
            "voice_enabled": False,
            "degraded_reasons": [reason],
        }
        if detail:
            receipt["detail"] = detail
        return receipt


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
    return snapshot.approval_required or snapshot.state in {
        "RUNNING",
        "BLOCKED",
        "COMPLETED",
        "FAILED",
    }


def _delivery_policy_for(
    snapshot: VoiceRunSnapshot,
    *,
    tone: str | None,
    intensity: float | None,
    valence: float | None,
    delivery_stage: str | None,
) -> dict[str, Any]:
    if tone is not None or intensity is not None or valence is not None:
        return {
            "source": snapshot.delivery_policy_source,
            "stage": delivery_stage or "caller_requested",
            "tone": tone,
            "intensity": intensity,
            "valence": valence,
        }
    if snapshot.approval_required:
        return {
            "source": snapshot.delivery_policy_source,
            "stage": delivery_stage or "human_approval_required",
            "tone": "firm_boundary",
            "intensity": 0.55,
            "valence": -0.20,
        }
    if snapshot.state == "COMPLETED":
        return {
            "source": snapshot.delivery_policy_source,
            "stage": delivery_stage or "accepted_completion",
            "tone": "relieved",
            "intensity": 0.50,
            "valence": 0.45,
        }
    if snapshot.state in {"BLOCKED", "FAILED"}:
        return {
            "source": snapshot.delivery_policy_source,
            "stage": delivery_stage or "recoverable_blocker",
            "tone": "careful_concerned",
            "intensity": 0.45,
            "valence": -0.25,
        }
    return {
        "source": snapshot.delivery_policy_source,
        "stage": delivery_stage or "routine_progress",
        "tone": None,
        "intensity": None,
        "valence": None,
    }


def _audio_identity_from_response(response: dict[str, Any]) -> dict[str, Any]:
    identity: dict[str, Any] = {}
    for key in ("finished_response_audio", "answer_text_sha256", "audio_path", "stream_id"):
        if key in response:
            identity[key] = response[key]
    return identity


def _response_identity(response: dict[str, Any]) -> dict[str, Any]:
    return {
        "sha256": _sha256_text(json.dumps(response, sort_keys=True)),
        "keys": sorted(response.keys()),
    }


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
