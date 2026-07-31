#!/usr/bin/env python3
"""Generate canonical tau.voice_render_request.v2 schema + fixtures (tau#288).

Deterministic: same code always emits byte-identical files, and the
MANIFEST.sha256 hash-binds every artifact so tau and chatterbox#11 can share
one fixture corpus instead of retyping the contract.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from tau_coding.tui.voice import (  # noqa: E402
    VoiceRunSnapshot,
    build_voice_render_request,
    build_voice_render_request_v2,
)
from tau_coding.voice_contract import (  # noqa: E402
    request_lineage_digest,
    voice_render_request_v2_json_schema,
)

OUT = REPO / "docs" / "contracts" / "voice"

SNAPSHOT = VoiceRunSnapshot(
    workflow="issue-288-voice-v2",
    run_id="run-288-fixture",
    state="BLOCKED",
    active_node="review",
    blocker="waiting on reviewer verdict",
    approval_required=False,
    attempt_id="attempt-1",
    scheduler_journal_sequence=42,
    state_digest="d1e5f0c0a288",
    goal_hash="goalhash288",
    state_transition="RUNNING->BLOCKED",
)

IDENTITY_KWARGS = dict(
    request_id="req-288-0001",
    conversation_id="conv-288",
    turn_id="turn-288-01",
    turn_revision=1,
    response_id="resp-288-0001",
    cancel_epoch=0,
    supersedes_response_id=None,
)


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    fixtures = OUT / "fixtures"

    _write(OUT / "tau.voice_render_request.v2.schema.json", voice_render_request_v2_json_schema())

    v2_positive = build_voice_render_request_v2(SNAPSHOT, **IDENTITY_KWARGS)
    _write(
        fixtures / "v2-positive.json",
        {
            "expect": "accepted",
            "request_lineage_digest": request_lineage_digest(v2_positive),
            "envelope": v2_positive,
        },
    )

    v1_positive = build_voice_render_request(
        SNAPSHOT, conversation_id="conv-288", turn_id="turn-288-01"
    )
    _write(
        fixtures / "v1-compat-positive.json",
        {
            "expect": "v1_supported_not_v2",
            "v2_parse_rejection": "v1_envelope_not_v2",
            "envelope": v1_positive,
        },
    )

    unknown = dict(v2_positive)
    unknown["schema"] = "tau.voice_render_request.v3"
    _write(
        fixtures / "negative-unknown-version.json",
        {"expect": "rejected", "reason": "unsupported_schema_version", "envelope": unknown},
    )

    misspelled = json.loads(json.dumps(v2_positive))
    misspelled["v2"]["identity"]["respons_id"] = misspelled["v2"]["identity"].pop("response_id")
    _write(
        fixtures / "negative-misspelled-required.json",
        {"expect": "rejected", "reason": "invalid_v2_block", "envelope": misspelled},
    )

    undeclared_extra = json.loads(json.dumps(v2_positive))
    undeclared_extra["v2"]["future_field"] = {"anything": True}
    extension_ok = json.loads(json.dumps(v2_positive))
    extension_ok["v2"]["extensions"] = {"future_field": {"anything": True}}
    _write(
        fixtures / "extensions-isolation.json",
        {
            "rejected_envelope": undeclared_extra,
            "rejected_reason": "invalid_v2_block",
            "accepted_envelope": extension_ok,
        },
    )

    current = {
        "request_id": "req-288-0002",
        "conversation_id": "conv-288",
        "turn_id": "turn-288-02",
        "turn_revision": 2,
        "response_id": "resp-288-0002",
        "cancel_epoch": 3,
        "supersedes_response_id": None,
    }

    def target(**overrides: object) -> dict[str, object]:
        base = {
            "conversation_id": current["conversation_id"],
            "turn_id": current["turn_id"],
            "turn_revision": current["turn_revision"],
            "response_id": current["response_id"],
            "expected_cancel_epoch": current["cancel_epoch"],
        }
        base.update(overrides)
        return base

    _write(
        fixtures / "control-cases.json",
        {
            "registered_response": current,
            "cases": [
                {"name": "stale_cancel_epoch", "action": "cancel",
                 "target": target(expected_cancel_epoch=2),
                 "expect": {"accepted": False, "reason": "stale_cancel_epoch"}},
                {"name": "stale_turn_revision", "action": "cancel",
                 "target": target(turn_revision=1),
                 "expect": {"accepted": False, "reason": "stale_turn_revision"}},
                {"name": "wrong_conversation", "action": "cancel",
                 "target": target(conversation_id="conv-other"),
                 "expect": {"accepted": False, "reason": "unknown_conversation"}},
                {"name": "stale_response_id", "action": "cancel",
                 "target": target(response_id="resp-288-0001"),
                 "expect": {"accepted": False, "reason": "stale_response_id"}},
                {"name": "stale_turn", "action": "cancel",
                 "target": target(turn_id="turn-288-01"),
                 "expect": {"accepted": False, "reason": "stale_turn"}},
                {"name": "current_cancel_accepted", "action": "cancel",
                 "target": target(),
                 "expect": {"accepted": True, "reason": "current_response"}},
                {"name": "duplicate_cancel_idempotent", "action": "cancel",
                 "target": target(expected_cancel_epoch=4),
                 "expect": {"accepted": True, "reason": "already_cancelled", "idempotent": True}},
            ],
        },
    )

    _write(
        fixtures / "delivery-decision-cases.json",
        {
            "cases": [
                {
                    "name": "approval_override_visible",
                    "input": {
                        "state": "RUNNING", "approval_required": True,
                        "requested": {"tone": "cheerful", "intensity": 0.9, "valence": 0.8},
                        "run_id": "run-288-fixture", "state_digest": "d1e5f0c0a288",
                    },
                },
                {
                    "name": "caller_hint_kept_when_unconstrained",
                    "input": {
                        "state": "RUNNING", "approval_required": False,
                        "requested": {"tone": "cheerful", "intensity": 0.3, "valence": 0.1},
                        "run_id": "run-288-fixture", "state_digest": "d1e5f0c0a288",
                    },
                },
                {
                    "name": "policy_fills_missing_fields",
                    "input": {
                        "state": "COMPLETED", "approval_required": False,
                        "requested": None,
                        "run_id": "run-288-fixture", "state_digest": None,
                    },
                },
                {
                    "name": "out_of_range_clamped_visibly",
                    "input": {
                        "state": "RUNNING", "approval_required": False,
                        "requested": {"tone": "relieved", "intensity": 1.7, "valence": -3.0},
                        "run_id": "run-288-fixture", "state_digest": "d1e5f0c0a288",
                    },
                },
            ]
        },
    )

    manifest_lines = []
    for path in sorted(OUT.rglob("*.json")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_lines.append(f"{digest}  {path.relative_to(OUT).as_posix()}")
    (OUT / "MANIFEST.sha256").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    print(f"wrote {len(manifest_lines)} artifacts under {OUT}")


if __name__ == "__main__":
    main()
