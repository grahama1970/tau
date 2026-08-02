# tau#288 Tau-Side Validation Hardening

Date: 2026-08-02

## Scope

This proof covers the 2026-08-02 Tau-side validation-hardening amendment on
`grahama1970/tau#288`.

It does not claim final closure of tau#288. The remaining cross-repository
criterion is the live Chatterbox consumer digest echo, tracked by
`grahama1970/chatterbox#11`, which is still open.

## Changed Contract Behavior

- `tau.voice_render_request.v2` external models now use Pydantic strict mode
  with `strict=True`, `allow_inf_nan=False`, `validate_default=True`, and
  `revalidate_instances="always"`.
- Identity and lineage strings are non-empty.
- Numeric strings, booleans in integer fields, and NaN floats fail visibly.
- JSON array inputs are accepted at the wire boundary and frozen internally as
  tuples.
- `extensions` and `override_reasons` are converted to immutable JSON
  containers so caller aliases cannot mutate a parsed contract.
- `overridden_fields` must exactly match `override_reasons` keys.
- `segment_id` values must be unique and bound to `identity.response_id`.

## Local Proof

mocked: yes
live: no

What was exercised:

- strict `tau.voice_render_request.v2` parser behavior;
- canonical schema/fixture round trip;
- v1 compatibility rejection through the v2 parser;
- unknown schema and misspelled field rejection;
- stale control fencing and duplicate cancel behavior through Tau-side voice
  registry tests;
- TUI voice producer compatibility.

Commands:

```text
uv run python scripts/generate-voice-contract-v2-fixtures.py
```

Result:

```text
wrote 8 artifacts under /home/graham/workspace/experiments/tau/docs/contracts/voice
```

```text
uv run pytest -q tests/test_voice_contract.py tests/test_tui_voice.py
```

Result:

```text
33 passed in 4.03s
```

```text
uv run ruff check src/tau_coding/voice_contract.py tests/test_voice_contract.py scripts/generate-voice-contract-v2-fixtures.py
```

Result:

```text
All checks passed!
```

```text
git diff --check
```

Result: exit code 0, no whitespace errors.

## Remaining Closure Boundary

Final tau#288 closure still requires a non-mocked live request through
`VoiceSurface.announce_response_v2` where Chatterbox returns the same
`request_lineage_digest` that Tau retained in its receipt. That cannot be
produced inside Tau until `grahama1970/chatterbox#11` implements the strict v2
consumer and digest readback.
