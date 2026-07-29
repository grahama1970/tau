# Tau #223 Current-Head Progress Proof

Ticket: `grahama1970/tau#223`

## Disposition

Progress only; do not close #223 from this artifact.

The Tau-side voice adapter now preserves richer run/turn lineage, records audio
identity and Chatterbox engine fields in Tau receipts, supersedes stale local
turns before a newer render, rejects wrong-run controls before network mutation,
and degrades explicitly on endpoint or response-schema failure.

## Commands Run

```bash
uv run pytest -q tests/test_tui_voice.py
```

Result:

```text
11 passed in 3.98s
```

```bash
uv run ruff check --select I,F,E501 src/tau_coding/tui/voice.py tests/test_tui_voice.py
```

Result:

```text
All checks passed!
```

```bash
uv run python - docs/proofs/tickets/issue-223-chatterbox-voice-render-20260728T2358Z/live-voice-contract-proof.json
```

Result:

```text
status PASS
health_live true
routine_turbo true
approval_weighted_base true
audio_identity_retained true
cancel_stale_skip true
duck_pass true
stop_pass true
superseded_old_turn true
wrong_run_blocked true
degraded_without_crash true
later_new_turn_succeeds true
health_after_live true
```

## Live Proof

`docs/proofs/tickets/issue-223-chatterbox-voice-render-20260728T2358Z/live-voice-contract-proof.json`
was run against the real Chatterbox service at `http://127.0.0.1:8018`.

It records:

- routine `/tau/voice-render` on `RUNNING` state with `engine=chatterbox_turbo`;
- approval-boundary weighted render with `engine=chatterbox_base`;
- retained `finished_response_audio` and `answer_text_sha256`;
- live cancel/duck/stop turn controls;
- `stale_chunks_should_skip=true` after cancel;
- Tau-side stale-turn supersession before the newer turn;
- local wrong-run control rejection with no network mutation;
- degraded unavailable-service receipt followed by a later successful new turn.

## Remaining Blocker

The exact requested destructive stop/restart proof is still missing. The shared
`:8018` Chatterbox service is root-owned, and `/proc/<pid>/cwd` is not readable
from this session, so killing it would risk leaving the shared service down.

Safe dedicated restart attempts failed before health:

```text
.venv/bin/python ... port 8028 -> ModuleNotFoundError: No module named 'perth'
/usr/bin/python3 ... port 8028 -> ModuleNotFoundError: No module named 'librosa'
```

This artifact proves Tau's transport, lineage, stale-turn suppression, controls,
degraded path, and later recovery against the live service. It does not prove a
destructive restart of the shared Chatterbox process.

`mocked`: no

`live`: yes

`provider_live`: true
