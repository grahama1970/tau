"""tau.voice_render_request.v2 contract tests (tau#288).

Runs the canonical hash-bound fixtures under docs/contracts/voice through the
strict parser, the control fence, and the deterministic delivery decision.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tau_coding.tui.voice import (
    VoiceRunSnapshot,
    VoiceSurface,
    build_voice_render_request_v2,
    interpret_spoken_command,
)
from tau_coding.voice_contract import (
    ControlTarget,
    DeliverySettings,
    ResponseControlRegistry,
    ResponseIdentity,
    VoiceContractError,
    decide_delivery,
    parse_voice_render_request_v2,
    request_lineage_digest,
    voice_render_request_v2_json_schema,
)

CONTRACT_DIR = Path(__file__).resolve().parent.parent / "docs" / "contracts" / "voice"
FIXTURES = CONTRACT_DIR / "fixtures"

SNAPSHOT = VoiceRunSnapshot(
    workflow="issue-288-voice-v2",
    run_id="run-288-test",
    state="BLOCKED",
    active_node="review",
    blocker="waiting on reviewer verdict",
    state_digest="digest-288",
)

V2_KWARGS = dict(
    request_id="req-1",
    conversation_id="conv-1",
    turn_id="turn-1",
    turn_revision=0,
    response_id="resp-1",
    cancel_epoch=0,
)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_manifest_hash_binds_every_fixture() -> None:
    manifest = (CONTRACT_DIR / "MANIFEST.sha256").read_text(encoding="utf-8").strip().splitlines()
    assert manifest, "manifest must not be empty"
    listed = {}
    for line in manifest:
        digest, _, rel = line.partition("  ")
        listed[rel] = digest
    on_disk = {
        path.relative_to(CONTRACT_DIR).as_posix()
        for path in CONTRACT_DIR.rglob("*.json")
    }
    assert set(listed) == on_disk
    for rel, digest in listed.items():
        assert hashlib.sha256((CONTRACT_DIR / rel).read_bytes()).hexdigest() == digest, rel


def test_committed_schema_matches_model() -> None:
    committed = json.loads(
        (CONTRACT_DIR / "tau.voice_render_request.v2.schema.json").read_text(encoding="utf-8")
    )
    assert committed == voice_render_request_v2_json_schema()


def test_v2_positive_fixture_round_trips_with_stable_digest() -> None:
    fixture = _load("v2-positive.json")
    parsed = parse_voice_render_request_v2(fixture["envelope"])
    assert parsed.identity.response_id == "resp-288-0001"
    assert request_lineage_digest(fixture["envelope"]) == fixture["request_lineage_digest"]


def test_v1_fixture_still_builds_and_is_not_silently_treated_as_v2() -> None:
    fixture = _load("v1-compat-positive.json")
    envelope = fixture["envelope"]
    assert envelope["schema"] == "tau.voice_render_request.v1"
    assert envelope["speakable_chunks"], "v1 envelope remains intact during migration"
    with pytest.raises(VoiceContractError) as excinfo:
        parse_voice_render_request_v2(envelope)
    assert excinfo.value.reason == fixture["v2_parse_rejection"]


def test_unknown_schema_version_fails_visibly() -> None:
    fixture = _load("negative-unknown-version.json")
    with pytest.raises(VoiceContractError) as excinfo:
        parse_voice_render_request_v2(fixture["envelope"])
    assert excinfo.value.reason == fixture["reason"]
    assert "tau.voice_render_request.v3" in str(excinfo.value)


def test_misspelled_required_field_fails_visibly() -> None:
    fixture = _load("negative-misspelled-required.json")
    with pytest.raises(VoiceContractError) as excinfo:
        parse_voice_render_request_v2(fixture["envelope"])
    assert excinfo.value.reason == fixture["reason"]
    assert "respons_id" in str(excinfo.value)
    assert "response_id" in str(excinfo.value)


def test_extensions_object_isolates_future_data() -> None:
    fixture = _load("extensions-isolation.json")
    with pytest.raises(VoiceContractError):
        parse_voice_render_request_v2(fixture["rejected_envelope"])
    parsed = parse_voice_render_request_v2(fixture["accepted_envelope"])
    assert parsed.extensions == {"future_field": {"anything": True}}


def test_control_fixture_cases() -> None:
    fixture = _load("control-cases.json")
    registry = ResponseControlRegistry()
    registration = registry.register_response(
        ResponseIdentity.model_validate(fixture["registered_response"])
    )
    assert registration["accepted"]
    for case in fixture["cases"]:
        verdict = registry.evaluate_control(
            ControlTarget.model_validate(case["target"]), action=case["action"]
        )
        for key, expected in case["expect"].items():
            assert verdict[key] == expected, f"{case['name']}: {key}"


def test_delivery_decision_fixture_cases_are_deterministic() -> None:
    fixture = _load("delivery-decision-cases.json")
    for case in fixture["cases"]:
        params = dict(case["input"])
        requested = params.pop("requested")
        params["requested"] = (
            DeliverySettings.model_validate(requested) if requested is not None else None
        )
        first = decide_delivery(**params)
        second = decide_delivery(**params)
        assert first == second, case["name"]
        if case["name"] == "approval_override_visible":
            assert first.effective_delivery.tone == "firm_boundary"
            assert set(first.overridden_fields) >= {"tone", "intensity", "valence"}
            assert all(
                reason == "approval_required_policy_profile"
                for reason in first.override_reasons.values()
            )
        if case["name"] == "caller_hint_kept_when_unconstrained":
            assert first.effective_delivery.tone == "cheerful"
            assert first.overridden_fields == ()
            assert first.profile_validation_status == "undeclared_profile"
        if case["name"] == "policy_fills_missing_fields":
            assert first.effective_delivery.tone == "relieved"
            assert first.profile_validation_status == "declared_profile"
        if case["name"] == "out_of_range_clamped_visibly":
            assert first.effective_delivery.intensity == 1.0
            assert first.effective_delivery.valence == -1.0
            assert {"intensity", "valence"} <= set(first.overridden_fields)


def test_builder_response_id_flows_into_segments_and_control_target() -> None:
    envelope = build_voice_render_request_v2(SNAPSHOT, **V2_KWARGS)
    block = envelope["v2"]
    assert block["identity"]["response_id"] == "resp-1"
    assert block["segments"][0]["segment_id"].startswith("resp-1")
    assert block["control_target"]["response_id"] == "resp-1"
    assert block["control_target"]["expected_cancel_epoch"] == 0
    assert envelope["turn_id"] == "turn-1", "v1 flat fields preserved on the same route"


def test_segment_hash_mismatch_is_rejected() -> None:
    envelope = build_voice_render_request_v2(SNAPSHOT, **V2_KWARGS)
    tampered = json.loads(json.dumps(envelope))
    tampered["v2"]["segments"][0]["text"] = "tampered announcement"
    with pytest.raises(VoiceContractError) as excinfo:
        parse_voice_render_request_v2(tampered)
    assert "text_sha256" in str(excinfo.value)


def test_spoken_approval_phrases_still_cannot_satisfy_a_gate() -> None:
    for phrase in ("approve", "authorized", "yes do it"):
        result = interpret_spoken_command(phrase, SNAPSHOT)
        assert result.status == "BLOCKED"
        assert result.side_effect_authorized is False
        assert result.approval_gate_satisfied is False


class _RecordingSurface(VoiceSurface):
    def __init__(self, response: dict) -> None:
        super().__init__(chatterbox_url="http://chatterbox.test", stt_url=None)
        self._canned = response
        self.posted: list[tuple[str, dict]] = []

    def _post_json(self, base_url: str, path: str, payload: dict) -> dict:
        self.posted.append((path, payload))
        return self._canned


def test_surface_receipt_retains_lineage_digest_and_matches_consumer_echo() -> None:
    surface = _RecordingSurface({"ok": True, "mocked": True, "live": False})
    receipt = surface.announce_response_v2(SNAPSHOT, **V2_KWARGS)
    path, payload = surface.posted[0]
    assert path == "tau/voice-render", "v2 extends the existing route"
    digest = request_lineage_digest(payload)
    assert receipt["request_lineage_digest"] == digest
    assert surface.response_lineage_digest("resp-1") == digest
    assert receipt["consumer_digest_matches"] is False, "no echo until chatterbox#11 lands"

    echoing = _RecordingSurface(
        {"ok": True, "mocked": True, "live": False, "request_lineage_digest": digest}
    )
    echoed = echoing.announce_response_v2(SNAPSHOT, **V2_KWARGS)
    assert echoed["consumer_digest_matches"] is True


def test_surface_control_fences_stale_epoch_and_is_idempotent_on_duplicate_cancel() -> None:
    surface = _RecordingSurface({"ok": True, "control": {"stale_chunks_should_skip": True}})
    surface.announce_response_v2(SNAPSHOT, **V2_KWARGS)
    stale = ControlTarget(
        conversation_id="conv-1",
        turn_id="turn-1",
        turn_revision=0,
        response_id="resp-1",
        expected_cancel_epoch=7,
    )
    blocked = surface.control_response_v2(stale, action="cancel")
    assert blocked["status"] == "BLOCKED"
    assert blocked["control_verdict"]["reason"] == "stale_cancel_epoch"
    assert len(surface.posted) == 1, "stale control never reached the wire"

    current = ControlTarget(
        conversation_id="conv-1",
        turn_id="turn-1",
        turn_revision=0,
        response_id="resp-1",
        expected_cancel_epoch=0,
    )
    first = surface.control_response_v2(current, action="cancel")
    assert first["control_verdict"]["accepted"] is True
    assert len(surface.posted) == 2, "accepted cancel reached the wire once"

    duplicate = ControlTarget(
        conversation_id="conv-1",
        turn_id="turn-1",
        turn_revision=0,
        response_id="resp-1",
        expected_cancel_epoch=1,
    )
    second = surface.control_response_v2(duplicate, action="cancel")
    assert second["status"] == "PASS"
    assert second["control_verdict"]["idempotent"] is True
    assert len(surface.posted) == 2, "duplicate cancel produced no second wire call"
