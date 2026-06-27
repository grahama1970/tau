# Sparta Chat Interface Contract For Tau Loop Experiments

This contract defines the experiment-local boundary between the Tau loop, the
Memory-first harness, and a future Sparta Chat / Sparta Explorer chat surface.

It is intentionally receipt-first. UI text is not proof; branch receipts and
stage events are the source of truth.

## Required Flow

1. Tau receives a user turn.
2. Harness calls Memory `/intent` before branch execution.
3. Harness preserves Memory intent, entity packet, and recall packet.
4. Harness selects exactly one primary branch.
5. Branch writes an explicit branch receipt.
6. Harness emits TUI stage metadata as structured event data.
7. TUI renders stage/progress and content embeds from receipt fields.

## Stage Events

Tau TUI consumes stage metadata through `ToolExecutionUpdateEvent.data`.

Accepted keys:

- `memory_stage`
- `pipeline_stage`
- `stage`

Canonical stage values and visible labels:

| Stage | Label |
| --- | --- |
| `intent` | `Getting Intent...` |
| `extract_entities` | `Extracting Entities...` |
| `memory` / `recall` | `Accessing Memory...` |
| `evidence_case` | `Creating Evidence Case...` |
| `brave_search` | `Searching Web...` |
| `figure` | `Creating Figure...` |
| `personaplex` | `Preparing Persona Voice...` |
| `answer` | `Answering...` |
| `clarify` | `Clarifying...` |
| `deflect` | `Deflecting...` |

The TUI may hide raw thinking tokens. When hidden, it should still display the
current stage label.

## Branches

### Memory

Memory owns routing:

- `/intent` classifies the turn and exposes entities, slots, profiles, and route.
- `/answer` produces grounded text only when evidence is sufficient.
- `/clarify` produces deterministic clarification.
- `/deflect` handles no-match, off-topic, and unsafe turns.

Harness receipts must preserve:

- `intent.action`
- `intent.confidence`
- `intent.entities`
- `intent.frameworks`
- `intent.recall_profile`
- `recall.items`
- `recall.confidence`
- `recall.should_scan`
- `stage_trace[]` entries with `stage`, `label`, `status`, and `source`
- `current_stage`, equal to the last `stage_trace[]` item

### Create Evidence Case

`create-evidence-case` is a judge product for compliance/SPARTA turns. It does
not own final conversational style.

Required receipt fields for Sparta Chat:

- `can_answer`
- `answerability`
- `technique_coherence`
- `crosswalk_chains_authoritative`
- `crosswalk_chains_candidate`
- `failure_codes`
- `entity_context`
- `evidence_case.spans` when available
- `cae_tree` when requested

If `can_answer=false`, the harness must call Memory `/clarify` with the
evidence-case packet. It must not invent follow-up copy.

### Brave Search

Brave search runs outside Memory when Memory intent or recall requires external
research. Memory does not execute Brave.

Required receipt fields:

- `query`
- `result_count`
- `payload.results`
- `status`
- `returncode`

### Create Figure

`create-figure` may render charts, diagrams, or content embeds only from
grounded data. The harness must not synthesize real data.

Required receipt fields:

- `source`
- `figure_type`
- `backend`
- `output_paths`
- `data_resolution`
- `sample_data: true|false`

If the user did not provide data and did not request sample/demo data, route to
clarification rather than creating a fake figure.

### PersonaPlex Voice

Persona text/persona memory and PersonaPlex audio are separate gates.

Harness receipts use:

```json
{
  "schema": "tau.sparta_chat_persona_voice.v1",
  "persona_id": "embry",
  "voice_engine": "personaplex",
  "voice_requested": true,
  "voice_status": "REQUESTED_NO_PERSONAPLEX_RECEIPT",
  "personaplex_receipt": null,
  "publication_status": "UNVERIFIED",
  "live_full_duplex": false
}
```

Allowed voice statuses:

- `NOT_REQUESTED`
- `REQUESTED_NO_PERSONAPLEX_RECEIPT`
- `PERSONAPLEX_RECEIPT_UNREADABLE`
- `PERSONAPLEX_RECEIPT_INVALID`
- `CACHE_REPLAY_PASS`
- `FAIL`

`CACHE_REPLAY_PASS` proves only offline PersonaPlex cache replay. It does not
prove publication approval or live full-duplex WebSocket readiness.

## Content Embeds

Sparta Chat content embeds are rendered from branch receipts, not inferred from
assistant prose.

Supported embed kinds for this experiment:

- `image`
- `video_link`
- `evidence_case_panel`
- `figure`
- `search_results`
- `persona_voice`

Each embed must include:

- `kind`
- `source_branch`
- `artifact_path` or `payload`
- `mocked`
- `live`
- `claims.proves`
- `claims.does_not_prove`

## Proof Rules

Mocked tests prove wiring only.

Live proof requires:

- Memory daemon HTTP responses for Memory branches.
- Brave command receipt for search branches.
- create-evidence-case command receipt for compliance branches.
- create-figure artifact paths for figure branches.
- PersonaPlex `personaplex.publish_receipt.v1` for voice cache replay.

Do not claim final Sparta Chat readiness until the browser UI consumes these
receipts and passes visual/CDP verification against a real URL.
