# tau#288 Final Cross-Repo Closure Proof

Date: 2026-08-02

## Scope

This closes the remaining acceptance boundary on `grahama1970/tau#288`.

Tau-side `tau.voice_render_request.v2` schema/model/fixtures and validation
hardening are committed on Tau `main`. The previously blocking Chatterbox
consumer ticket is now closed:

```text
grahama1970/chatterbox#11 CLOSED COMPLETED at 2026-08-02T20:31:37Z
```

## Tau-Side Proof

```text
uv run pytest -q tests/test_voice_contract.py tests/test_tui_voice.py
```

Result:

```text
33 passed in 4.12s
```

```text
uv run ruff check src/tau_coding/voice_contract.py tests/test_voice_contract.py scripts/generate-voice-contract-v2-fixtures.py
```

Result:

```text
All checks passed!
```

## Paired Chatterbox Live Proof

```text
cd /home/graham/workspace/experiments/chatterbox
PYTHONPATH=src uv run --no-sync python scripts/smoke_tau_voice_render_v2.py --allow-live
```

Result:

```json
{"ok": true, "receipt": "/home/graham/workspace/experiments/chatterbox/docs/proofs/tickets/issue-11-v2-consumer-20260802/live-smoke/receipt.json", "failed_gates": []}
```

Read-back invariants from the receipt:

- `mocked: false`
- `live: true`
- `http_status: 200`
- `failed_gates: []`
- Chatterbox consumed Tau's canonical v2 fixture through the existing
  `/tau/voice-render` route.
- `request_lineage_digest` equals
  `10242ccd97287926fbb0692163429ee95427e692dc63daf88f3a63b161b0e95b`.
- `consumer_digest_matches: true`.
- `finished_response_audio` exists and is non-empty.
- stale wrong `response_id` cancel is rejected with `stale_response_id`.
- current cancel is accepted with `current_response`.
- duplicate cancel is accepted idempotently with `already_cancelled`.

## Does Not Prove

- perceptual correctness of the selected delivery tone;
- GPU model-load health on this host;
- every future Chatterbox streaming terminal case beyond the existing stream
  manifest tests.
