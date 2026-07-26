# Issue #177 Proof: Graph Context Manifest Compaction

Ticket: https://github.com/grahama1970/tau/issues/177

## Result

Tau compaction now writes a replayable `tau.context_manifest.v1` payload instead
of asking a model to summarize active history. The session replay layer reads
manifest compactions back into the active provider context. Legacy compaction
summaries still replay with `Previous conversation summary:` for old sessions.

## Implementation

- `src/tau_coding/context_window.py`
  - Added Tier 0 pinned context, Tier 1 recency, Tier 2 Memory recall, and
    Tier 3 content-addressed by-reference manifest assembly.
  - Uses pooled `httpx.Client` with `httpx.Timeout(..., connect=2.0)`.
  - Calls Memory `/upsert`, then `/intent`, then `/recall` only when the intent
    action requires retrieval.
  - Reads Memory `items` and `confidence`; no AQL, local traversal, subprocess
    memory calls, or local reranking.
  - Labels retrieved Tier 2 content as untrusted data.
- `src/tau_coding/session.py`
  - Replaced model-generated compaction summaries with deterministic graph
    context manifest compaction.
- `src/tau_agent/session/memory.py`
  - Replays manifest compactions from the stored manifest message.

## Proof Commands

```bash
uv run ruff check src/tau_coding/context_window.py src/tau_agent/session/memory.py src/tau_coding/session.py tests/test_context_window.py tests/test_coding_session.py
```

Result: `All checks passed!`

```bash
uv run python -m py_compile src/tau_coding/context_window.py src/tau_agent/session/memory.py src/tau_coding/session.py tests/test_context_window.py tests/test_coding_session.py
```

Result: exit code `0`.

```bash
uv run pytest -q tests/test_context_window.py tests/test_session.py::test_session_state_replays_compaction_as_context_summary tests/test_session.py::test_session_state_inserts_partial_compaction_before_retained_messages tests/test_coding_session.py::test_context_usage_recalculates_after_prompt_and_compaction tests/test_coding_session.py::test_session_compact_persists_summary_and_rebuilds_context tests/test_coding_session.py::test_session_auto_compacts_after_response_when_threshold_is_exceeded tests/test_coding_session.py::test_session_auto_compacts_with_pi_style_default_threshold tests/test_coding_session.py::test_session_compacts_and_retries_once_after_context_overflow
```

Result: `18 passed in 34.55s`.

```bash
git diff --check
```

Result: exit code `0`.

## Live Memory Receipt

Durable receipt:

```text
docs/proofs/tickets/issue-177-live-memory-context-receipt-20260726.json
```

Receipt summary:

- mocked: `false`
- live: `true`
- real Memory daemon: `true`
- Memory health: HTTP `200`
- Memory call path: `/upsert`, `/intent`, `/recall`
- manifest schema: `tau.context_manifest.v1`
- Tier 0 goal hash present: `true`
- retrieved content marked untrusted: `true`
- sentinel recalled: `true`
- sentinel: `issue-177-live-sentinel-silver-1785087620`

## Proof Boundary

- mocked: no
- live: yes
- provider/model calls: no
- exercised: deterministic context manifest assembly, real Memory HTTP daemon,
  `/upsert`, `/intent`, `/recall`, session compaction replay, manifest readback
  from session storage, Tier 0 retrieval-failure survival, and untrusted
  retrieved-content labeling
- not exercised: provider/model semantic quality, Memory fact truth, or any
  guarantee that every future Memory query will retrieve the desired item

