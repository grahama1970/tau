# tau#288 — `tau.voice_render_request.v2` (Tau producer side)

Date: 2026-07-31. Branch: `main` (worked and pushed directly on main).

## What landed

- `src/tau_coding/voice_contract.py` — typed pydantic v2 model (strict,
  `extra="forbid"`), exported versioned JSON Schema, deterministic
  requested-vs-effective delivery decision (`decide_delivery`), canonical
  request/lineage digest (`request_lineage_digest`), and the
  `ResponseControlRegistry` fence (stale epoch / revision / turn / response /
  conversation rejected; duplicate cancel idempotent).
- `src/tau_coding/tui/voice.py` — `build_voice_render_request_v2` (same
  `POST /tau/voice-render` route; v1 flat fields preserved; strict `v2` block
  added; producer self-validates through the strict parser), plus
  `VoiceSurface.announce_response_v2` / `control_response_v2` retaining the
  lineage digest in the Tau receipt and fencing controls before the wire.
- `docs/contracts/voice/` — committed JSON Schema + canonical hash-bound
  fixtures (`MANIFEST.sha256`) shared with `grahama1970/chatterbox#11`:
  v1-compat positive, v2 positive, unknown-version rejection, misspelled
  required-field rejection, extensions isolation, control-fence cases
  (stale epoch/revision/turn/response, wrong conversation, idempotent
  duplicate cancel), delivery-override decision cases.
- `scripts/generate-voice-contract-v2-fixtures.py` — deterministic generator.
- `tests/test_voice_contract.py` — 14 tests running the fixtures through the
  real parser/fence/policy, including consumer digest echo match/mismatch and
  spoken-approval refusal.

## Proof commands (all run 2026-07-31 on main)

```text
uv run pytest tests/test_voice_contract.py tests/test_tui_voice.py -q   -> 25 passed
uv run pytest tests/ -q                                                 -> 3455 passed (+5 airgap)
uv run ruff check <changed files>                                       -> All checks passed
uv run mypy src/tau_coding/voice_contract.py src/tau_coding/tui/voice.py -> no issues
uv run python scripts/generate-voice-contract-v2-fixtures.py            -> 8 artifacts, hash-bound
```

## Acceptance mapping

| Criterion | Status |
| --- | --- |
| Versioned JSON Schema + typed model committed | DONE |
| Canonical positive/negative v1/v2 fixtures, hash-bound | DONE (`MANIFEST.sha256`) |
| V1 remains supported | DONE (v1 builder/tests untouched, 11 v1 tests pass) |
| `response_id` stable request → receipts | DONE on Tau side (receipt + registry) |
| Stale epoch/revision/conversation/response fenced | DONE (`ResponseControlRegistry`, fixture cases) |
| Duplicate cancellation idempotent | DONE (no second wire call, test-proven) |
| Deterministic requested/effective + override reasons | DONE (`decide_delivery`, determinism test) |
| Unknown version / misspelled field fails visibly | DONE (`VoiceContractError` names the field) |
| `extensions` isolation | DONE |
| Spoken approval phrases cannot satisfy a gate | DONE (unchanged, re-proven) |
| Request/lineage digest retained in Tau receipt | DONE (`request_lineage_digest` in receipt) |
| Digest matched by Chatterbox consumer proof | **BLOCKED on `grahama1970/chatterbox#11`** |
| Live v2 request to Chatterbox after consumer lands | **BLOCKED on `grahama1970/chatterbox#11`** |

## Boundary statement

Everything above is non-mocked with respect to the contract code itself but is
NOT the final live proof: the closing criterion requires a live v2 request whose
`request_lineage_digest` is echoed by the strict Chatterbox v2 consumer, which
does not exist until `grahama1970/chatterbox#11` lands. The ticket is therefore
blocked (not closed) with a machine-readable `blocked-by` reference.
