"""``tau.voice_render_request.v2`` contract (tau#288).

Extends the proven v1 voice boundary (``POST /tau/voice-render``, tau#223) with
response-level identity, cancellation-epoch fencing, and an inspectable
requested-vs-effective delivery decision. Same route as v1 — the ``schema``
field selects the version; v1 remains supported during the migration period.

Authority rules (unchanged from v1): Tau projects authoritative run state;
no field in this envelope can satisfy approval, repair, or release authority;
requested and effective delivery are separate, with policy overrides visible.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

VOICE_RENDER_REQUEST_SCHEMA_V1 = "tau.voice_render_request.v1"
VOICE_RENDER_REQUEST_SCHEMA_V2 = "tau.voice_render_request.v2"
SUPPORTED_SCHEMAS = (VOICE_RENDER_REQUEST_SCHEMA_V1, VOICE_RENDER_REQUEST_SCHEMA_V2)
DELIVERY_POLICY_VERSION = "tau.voice_delivery_policy.v1"

# Tone names the delivery policy may emit. "Validated" means declared in this
# profile set, NOT perceptually validated (an explicit non-goal of tau#288).
DECLARED_TONE_PROFILES = frozenset(
    {"firm_boundary", "relieved", "careful_concerned"}
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DeliverySettings(_StrictModel):
    """One tone/intensity/valence/stage tuple (requested or effective)."""

    tone: str | None = None
    intensity: float | None = None
    valence: float | None = None
    stage: str | None = None


class DeliveryDecision(_StrictModel):
    """Requested vs effective delivery with every override visible."""

    policy_version: str
    requested_delivery: DeliverySettings
    effective_delivery: DeliverySettings
    overridden_fields: tuple[str, ...] = ()
    override_reasons: dict[str, str] = Field(default_factory=dict)
    evidence_references: tuple[str, ...] = ()
    profile_validation_status: Literal["declared_profile", "undeclared_profile", "no_tone"]


class SourceLineage(_StrictModel):
    """Where in the authoritative Tau run this audible response came from."""

    workflow: str
    run_id: str
    node_id: str
    attempt_id: str | None = None
    scheduler_journal_sequence: int | None = None
    state_digest: str | None = None
    goal_hash: str | None = None
    event_type: str = "state_change"
    state_transition: str | None = None


class ResponseIdentity(_StrictModel):
    """Complete identity for one audible response and its cancellation epoch."""

    request_id: str
    conversation_id: str
    turn_id: str
    turn_revision: int = Field(ge=0)
    response_id: str
    cancel_epoch: int = Field(ge=0)
    supersedes_response_id: str | None = None


class Segment(_StrictModel):
    """One speakable segment; hash-bound text, always interruptible."""

    segment_id: str
    text: str
    text_sha256: str
    delivery: DeliverySettings | None = None
    interruptible: Literal[True] = True

    @field_validator("text_sha256")
    @classmethod
    def _hash_matches(cls, value: str, info: Any) -> str:
        text = info.data.get("text")
        if text is not None and hashlib.sha256(text.encode("utf-8")).hexdigest() != value:
            raise ValueError("text_sha256 does not match text")
        return value


class ControlTarget(_StrictModel):
    """Full identity a turn control must present to touch audible work."""

    conversation_id: str
    turn_id: str
    turn_revision: int = Field(ge=0)
    response_id: str
    expected_cancel_epoch: int = Field(ge=0)


class VoiceRenderRequestV2(_StrictModel):
    """Typed ``tau.voice_render_request.v2`` block.

    Carried inside the wire envelope next to the v1-compatible flat fields;
    ``extensions`` is the only place optional future data may live.
    """

    schema_id: str = Field(alias="schema")
    identity: ResponseIdentity
    lineage: SourceLineage
    delivery_decision: DeliveryDecision
    segments: tuple[Segment, ...] = Field(min_length=1)
    control_target: ControlTarget
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_id")
    @classmethod
    def _supported(cls, value: str) -> str:
        if value != VOICE_RENDER_REQUEST_SCHEMA_V2:
            raise ValueError(
                f"unsupported schema {value!r}; this model accepts only "
                f"{VOICE_RENDER_REQUEST_SCHEMA_V2!r}"
            )
        return value

    @field_validator("control_target")
    @classmethod
    def _target_matches_identity(cls, value: ControlTarget, info: Any) -> ControlTarget:
        identity = info.data.get("identity")
        if identity is not None and (
            value.conversation_id != identity.conversation_id
            or value.turn_id != identity.turn_id
            or value.turn_revision != identity.turn_revision
            or value.response_id != identity.response_id
            or value.expected_cancel_epoch != identity.cancel_epoch
        ):
            raise ValueError("control_target must match the response identity")
        return value


class VoiceContractError(ValueError):
    """Visible, structured contract failure (never silently dropped)."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def parse_voice_render_request_v2(payload: dict[str, Any]) -> VoiceRenderRequestV2:
    """Strictly parse a wire payload's v2 block, failing visibly.

    Unsupported schema versions and unknown/misspelled fields raise
    :class:`VoiceContractError` naming the offending field; optional future
    data must live under ``extensions``.
    """

    schema = payload.get("schema")
    if schema not in SUPPORTED_SCHEMAS:
        raise VoiceContractError(
            "unsupported_schema_version",
            f"schema {schema!r} is not one of {list(SUPPORTED_SCHEMAS)}",
        )
    if schema == VOICE_RENDER_REQUEST_SCHEMA_V1:
        raise VoiceContractError(
            "v1_envelope_not_v2",
            "payload declares tau.voice_render_request.v1; parse it with the v1 path",
        )
    block = payload.get("v2")
    if not isinstance(block, dict):
        raise VoiceContractError("missing_v2_block", "v2 payload must carry a 'v2' object")
    try:
        return VoiceRenderRequestV2.model_validate({"schema": schema, **block})
    except ValidationError as exc:
        raise VoiceContractError("invalid_v2_block", _summarize_validation(exc)) from exc


def _summarize_validation(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"]) or "<root>"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(sorted(parts))


def voice_render_request_v2_json_schema() -> dict[str, Any]:
    """Versioned JSON Schema for the v2 block, for cross-repo fixture sharing."""

    schema = VoiceRenderRequestV2.model_json_schema()
    schema["$id"] = f"https://github.com/grahama1970/tau/contracts/{VOICE_RENDER_REQUEST_SCHEMA_V2}.schema.json"
    schema["x-contract-version"] = VOICE_RENDER_REQUEST_SCHEMA_V2
    return schema


def decide_delivery(
    *,
    state: str,
    approval_required: bool,
    requested: DeliverySettings | None,
    run_id: str,
    state_digest: str | None,
    policy_version: str = DELIVERY_POLICY_VERSION,
) -> DeliveryDecision:
    """Deterministically derive effective delivery from authoritative state.

    Same authoritative input + policy version always yields the same decision.
    Caller hints are advisory: policy overrides replace them VISIBLY via
    ``overridden_fields`` + ``override_reasons`` instead of silently.
    """

    if policy_version != DELIVERY_POLICY_VERSION:
        raise VoiceContractError(
            "unsupported_policy_version",
            f"policy {policy_version!r} is not {DELIVERY_POLICY_VERSION!r}",
        )
    requested = requested or DeliverySettings()
    policy = _policy_delivery(state=state, approval_required=approval_required)

    overridden: list[str] = []
    reasons: dict[str, str] = {}
    effective: dict[str, Any] = {
        "tone": requested.tone,
        "intensity": requested.intensity,
        "valence": requested.valence,
        "stage": requested.stage or policy.stage,
    }
    if approval_required:
        # Approval announcements always use the boundary profile; caller hints
        # for tone/valence are advisory and visibly overridden.
        for field_name in ("tone", "intensity", "valence"):
            requested_value = getattr(requested, field_name)
            policy_value = getattr(policy, field_name)
            if requested_value is not None and requested_value != policy_value:
                overridden.append(field_name)
                reasons[field_name] = "approval_required_policy_profile"
            effective[field_name] = policy_value
        if requested.stage is not None and requested.stage != policy.stage:
            overridden.append("stage")
            reasons["stage"] = "approval_required_policy_profile"
        effective["stage"] = policy.stage
    else:
        for field_name in ("tone", "intensity", "valence"):
            if getattr(requested, field_name) is None:
                effective[field_name] = getattr(policy, field_name)
    if effective["intensity"] is not None and not 0.0 <= effective["intensity"] <= 1.0:
        clamped = min(max(effective["intensity"], 0.0), 1.0)
        overridden.append("intensity")
        reasons["intensity"] = f"clamped_from_{effective['intensity']}_to_range_0_1"
        effective["intensity"] = clamped
    if effective["valence"] is not None and not -1.0 <= effective["valence"] <= 1.0:
        clamped = min(max(effective["valence"], -1.0), 1.0)
        overridden.append("valence")
        reasons["valence"] = f"clamped_from_{effective['valence']}_to_range_-1_1"
        effective["valence"] = clamped

    tone = effective["tone"]
    if tone is None:
        profile_status: str = "no_tone"
    elif tone in DECLARED_TONE_PROFILES:
        profile_status = "declared_profile"
    else:
        profile_status = "undeclared_profile"
    return DeliveryDecision(
        policy_version=policy_version,
        requested_delivery=requested,
        effective_delivery=DeliverySettings(**effective),
        overridden_fields=tuple(dict.fromkeys(overridden)),
        override_reasons=reasons,
        evidence_references=tuple(
            reference
            for reference in (
                f"tau_run:{run_id}",
                f"state_digest:{state_digest}" if state_digest else None,
                f"authoritative_state:{state}",
            )
            if reference
        ),
        profile_validation_status=profile_status,  # type: ignore[arg-type]
    )


def _policy_delivery(*, state: str, approval_required: bool) -> DeliverySettings:
    if approval_required:
        return DeliverySettings(
            tone="firm_boundary", intensity=0.55, valence=-0.20, stage="human_approval_required"
        )
    if state == "COMPLETED":
        return DeliverySettings(
            tone="relieved", intensity=0.50, valence=0.45, stage="accepted_completion"
        )
    if state in {"BLOCKED", "FAILED"}:
        return DeliverySettings(
            tone="careful_concerned", intensity=0.45, valence=-0.25, stage="recoverable_blocker"
        )
    return DeliverySettings(stage="routine_progress")


def request_lineage_digest(envelope: dict[str, Any]) -> str:
    """Canonical digest Tau retains and the Chatterbox consumer must echo.

    Covers schema, identity, lineage, and segment text hashes — the fields
    whose silent loss the v1 permissive parsing allowed.
    """

    block = envelope.get("v2", {})
    material = {
        "schema": envelope.get("schema"),
        "identity": block.get("identity"),
        "lineage": block.get("lineage"),
        "segment_text_sha256": [
            segment.get("text_sha256") for segment in block.get("segments", [])
        ],
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ResponseControlRegistry:
    """Tau-side fence: stale controls cannot target newer audible work.

    Tracks the current response identity per conversation. A control is
    accepted only when conversation, turn, revision, response id, and cancel
    epoch ALL match the current audible response. Duplicate cancellation of the
    same response is idempotent (accepted, no epoch bump, no second effect).
    """

    def __init__(self) -> None:
        self._current: dict[str, ResponseIdentity] = {}
        self._cancelled: set[str] = set()

    def register_response(self, identity: ResponseIdentity) -> dict[str, Any]:
        previous = self._current.get(identity.conversation_id)
        if previous is not None and identity.supersedes_response_id not in (
            None,
            previous.response_id,
        ):
            return {
                "accepted": False,
                "reason": "supersedes_mismatch",
                "current_response_id": previous.response_id,
            }
        self._current[identity.conversation_id] = identity
        return {"accepted": True, "response_id": identity.response_id}

    def evaluate_control(self, target: ControlTarget, *, action: str) -> dict[str, Any]:
        current = self._current.get(target.conversation_id)
        receipt: dict[str, Any] = {
            "action": action,
            "target_response_id": target.response_id,
            "idempotent": False,
        }
        if current is None:
            return {**receipt, "accepted": False, "reason": "unknown_conversation"}
        if target.turn_id != current.turn_id:
            return {**receipt, "accepted": False, "reason": "stale_turn"}
        if target.turn_revision != current.turn_revision:
            return {**receipt, "accepted": False, "reason": "stale_turn_revision"}
        if target.response_id != current.response_id:
            return {**receipt, "accepted": False, "reason": "stale_response_id"}
        if target.expected_cancel_epoch != current.cancel_epoch:
            return {**receipt, "accepted": False, "reason": "stale_cancel_epoch"}
        if action == "cancel":
            if current.response_id in self._cancelled:
                return {
                    **receipt,
                    "accepted": True,
                    "idempotent": True,
                    "reason": "already_cancelled",
                }
            self._cancelled.add(current.response_id)
            self._current[target.conversation_id] = current.model_copy(
                update={"cancel_epoch": current.cancel_epoch + 1}
            )
        return {**receipt, "accepted": True, "reason": "current_response"}

    def current_identity(self, conversation_id: str) -> ResponseIdentity | None:
        return self._current.get(conversation_id)
