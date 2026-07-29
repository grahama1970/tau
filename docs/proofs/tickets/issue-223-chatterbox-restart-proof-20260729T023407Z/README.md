# Tau #223 Chatterbox Restart Proof

Ticket: `grahama1970/tau#223`

This proof covers the remaining stop/restart acceptance gap after the earlier
live voice contract proof.

## Command

```bash
uv run python scripts/run-tui-voice-restart-proof.py \
  --url http://127.0.0.1:8018 \
  --container e47338c7d28e \
  --output docs/proofs/tickets/issue-223-chatterbox-restart-proof-20260729T023407Z/restart-proof.json
```

## Result

```text
status PASS
health_before_live True
before_render_live_pass True
docker_stop_succeeded True
health_down_unavailable True
down_receipt_degraded True
docker_start_succeeded True
health_after_restart_live True
after_render_live_pass True
down_turn_not_rendered True
after_turn_rendered True
after_turn_did_not_reuse_old_turn_id True
```

## Receipt

`restart-proof.json` records:

- `mocked: false`, `live: true`;
- live health before stop and after restart;
- live `/tau/voice-render` receipt before stop;
- explicit `DEGRADED` Tau receipt while Chatterbox is stopped;
- Docker stop/start command results;
- live fresh-turn render after restart;
- lineage showing `tau-223-while-stopped` was not rendered and
  `tau-223-after-restart` was rendered.

## Boundary

This proves Tau's transport, degradation, restart recovery, and no-replay
behavior at the Tau/Chatterbox service boundary on this local Docker service.
It does not prove perceptual emotion quality, speaker authentication, voice
approval authority, or arbitrary host audio-device compatibility.
