Verdict

READY_FOR_EVAL_HARDENING

The viewer is sufficiently coherent and timeline-oriented to proceed directly into adversarial eval hardening. It does not need a broad UI rebuild or architecture review first.

This is not a closure verdict. The current packet remains INCOMPLETE; the agentic-evals manifest has not been executed, the previously reported frontend commands were not rerun for this packet, and the archive is not fully self-contained.

Prioritized findings
P0 — The archive does not contain the claimed proof manifest

The ZIP contains exactly five files:

browser-proof.json

browser-proof.png

cdp-screenshot.png

consolidated-review-bundle.md

project-state.json

It does not contain either of the referenced artifacts:

docs/review-bundles/tau-timeline-proof-bundle-20260813/manifest.json

evals/tau_timeline_viewer_agentic_eval.json

The Markdown summary reports their status and contents, but a summary is not independently validatable proof. Consequently, this review cannot verify:

the actual tau.viewer.proof_bundle.v1 schema;

manifest file hashes and required-artifact rules;

the four eval definitions or their exact assertions;

whether manifest status is computed or manually declared;

whether the manifest would detect this packet-content mismatch.

This is the most important next implementation slice. It is a bounded proof-contract hardening problem, not evidence that Tau needs a broad architecture review.

P0 — The browser artifact binding is partially sound, but the checks are not independently inspectable

Positive evidence:

browser-proof.json contains 40 boolean checks, all set to true.

The declared SHA-256 for browser-proof.png matches the included PNG exactly: 2c0d88ea7b044406c69eabc7bb71b2231a570f29afa7e9a6f622f34f96e8db0a.

The declared 1440×1000 viewport matches the image dimensions.

The proof correctly distinguishes live=true from provider_live=false.

It explicitly reports GET-only requests and read-only controls.

The bundle calls this bounded local UI proof rather than full Tau or model-provider proof.

What remains unproven is whether each browser check has an independent oracle. The JSON contains final booleans but no per-check selector, input action, expected observation, actual observation, screenshot region, browser-console result, or failure trace. A test can be falsely green when the UI and test derive their expectations from the same projection function.

The next proof format should make each important assertion inspectable and should include mutation probes that deliberately break the UI or fixture and demonstrate that the proof command fails.

P0 — The visible status hierarchy still permits a false-green interpretation

The screenshot simultaneously presents:

COMPLETE · PASS · PASS in the global top bar;

Run settled;

2 accepted;

No accepted final result;

BLOCKER · missing_required_evidence;

WARNING · completion_boundary_not_available.

The latter messages are honest, and the PROVES / DOES NOT PROVE pane is a strong design choice. However, the unlabeled COMPLETE · PASS · PASS tokens are more visually prominent than their scope. An operator could interpret them as overall closure rather than execution, creator, reviewer, or attempt-level statuses.

Before hardening closure claims, every success-like token must carry its scope. For example:

execution COMPLETE · reviewer PASS · admission ACCEPTED · final result ABSENT

Likewise, 2 accepted should say what is accepted—nodes, attempts, or receipts. This is a narrow semantic-label issue suitable for an eval-driven patch, not a reason for a UI-repair phase.

P1 — The UX still reads as a video-editor-style agentic timeline

The screenshot retains the important editor grammar:

timeline is the selected primary view, with topology as a sibling;

horizontal sequence ruler;

role swimlanes;

duration clips;

sequence zoom, fit, and detail controls;

live-head navigation and follow/pause controls;

left orchestration pool;

right inspector;

collapsible workspace panes;

lower event/journal region;

selected-clip-to-inspector relationship.

That is recognizably an editor workspace rather than a conventional KPI dashboard.

There is nevertheless visible dashboard drift:

the workflow/goal/current/result summary strip occupies prominent space;

active/accepted/control counters resemble dashboard KPIs;

bounded-transaction cards and the journal consume more vertical area than the swimlanes;

lane names such as artifact-transac... and dependent-con... are truncated in the primary timeline.

The drift is contained. The timeline is still above the fold and remains the semantic center. It should be guarded by geometry and data-extreme evals rather than redesigned now.

P1 — The current packet contains reported tests, not current execution proof

The bundle reports a prior result of 10 focused tests passing, plus successful typecheck and build. It explicitly says those commands were not rerun for this retry. They therefore cannot support current closure.

Similarly, project-state.json reports tests.total=3650 and collected=true. That proves collection scope only, not that 3,650 tests passed. The final proof schema should use distinct fields such as:

collected_count

executed_count

passed_count

failed_count

exit_code

command

source_commit

working_tree_state

P1 — The CDP screenshot is corroboration, not bound proof

The two screenshots appear to show the same viewer one journal event apart:

browser proof: sequence/journal 38;

CDP screenshot: sequence/journal 39.

That is plausible for a live-following viewer, but the ZIP does not include the referenced .codex/ui-verification/latest.json, its hash, command receipt, run identifier, or a manifest relationship tying the screenshot to the reviewed commit and browser session.

Treat cdp-screenshot.png as visual corroboration only until it is manifest-bound.

P2 — Generic project-state output should remain context-only

The bundle correctly warns that frontend.exists=false is a detector miss because the actual app is under web/dag-viewer. That result must never become a viewer acceptance assertion.

The 11 possible hardcoded-secret findings and documentation drift findings are also not adjudicated by this packet. They may require separate repository work, but they neither prove nor disprove timeline-viewer readiness. The proof manifest should explicitly classify project-state.json as context_only, not normative viewer evidence.

Acceptance criteria for the hardening phase
1. Self-contained proof-bundle contract

A generated review or closure archive must include the actual manifest.json, eval manifest, eval result, browser JSON, screenshots, and command receipts—not summaries pointing to local-only paths.

Every artifact must have:

bundle-relative path;

SHA-256 and byte size;

artifact role such as normative, supporting, advisory, or context_only;

producing command and exit code;

source commit and dirty-tree state;

run identifier and timestamp;

schema version where applicable.

An absolute /tmp/... path may be retained as origin metadata, but never as the only artifact locator.

A standalone validator must fail nonzero for missing files, hash substitution, duplicate logical artifacts, unsupported schema versions, stale commit associations, and COMPLETE status with unmet obligations.

2. Current, separately counted execution proof

At the exact source state being reviewed:

the focused timeline tests must be rerun;

typecheck must be rerun;

production build must be rerun;

the agentic-evals manifest must be executed;

collected and passed counts must be reported separately;

every declared trial must use a fresh temporary directory, port, and browser context;

reused screenshots or prior result files must cause failure.

The declared two trials are sufficient for the first hardening gate if they are isolated and deterministic. Additional repetitions can follow if browser instability appears.

3. Honest terminal and admission semantics

The UI and its accessible text must distinguish:

runtime completion;

creator result;

reviewer result;

receipt existence;

Tau admission;

dependent release;

run settlement;

accepted final result.

When a blocker exists or the accepted final result is absent, the run must not receive an unqualified green PASS, SUCCESS, or closure state.

Reviewer PASS alone must never release a dependent. Acceptance must require the authoritative admission event, and release must happen exactly once.

Missing goal, workflow, duration, or completion-boundary metadata must continue to render as unavailable or unknown rather than being inferred.

4. Timeline/editor geometry

At minimum, deterministic browser proof should cover 1440×1000 and a smaller supported viewport such as 1280×720.

At the default viewport:

Timeline is the default selected workspace.

The sequence ruler, at least two role lanes, one execution lane, one clip, zoom controls, and the selected-node relationship are visible without vertical scrolling.

No header, lane, clip, inspector, resize handle, or control has an unintended bounding-box intersection.

The timeline has independent horizontal scrolling.

Long role and node identifiers expose their complete identity through visible expansion, tooltip, or accessible name.

Collapsing and resizing the orchestration pool, inspector, and journal cannot make the timeline unusable.

Adding summary information must not move the timeline below the initial viewport.

5. Live, replay, and read-only behavior

Pause must freeze the viewed sequence while journal events continue. Resume must catch up exactly once. Reconnection and refresh must reconstruct the same authoritative state without duplicate clips, receipts, or releases.

GET-only network observation is useful but insufficient by itself. The proof should hash the underlying ledger or source data before and after all viewer interactions and require equality.

Duplicate and out-of-order events must be resolved by authoritative sequence identity, not arrival order.

6. Advisory-review boundary

This WebGPT response may be stored as an advisory artifact with its transport receipt and hash. Its verdict must not change local deterministic results from failing to passing.

The bundle may move from INCOMPLETE only after all locally required artifacts and executed eval results are present. WebGPT presence is not a substitute for them.

Additional agentic-evals to add
Priority	Proposed case	Required adversarial behavior
P0	proof-bundle-integrity-negative-matrix	Run valid and deliberately corrupted bundles. Missing manifest, missing eval report, hash mismatch, screenshot substitution, stale commit, absolute-only artifact path, and premature COMPLETE must fail.
P0	browser-proof-mutation-sensitivity	Deliberately introduce one overlap, one premature green admission, one duplicate selector identity, and one mutation request in separate variants. The browser-proof command must detect each and exit nonzero.
P0	terminal-status-scope-no-false-green	Use the state visible in this screenshot: execution complete and reviewer pass, but blocker present and no accepted final result. Assert that no unqualified run-level success is rendered and every status token is scope-labeled.
P0	journal-gap-reorder-duplicate-idempotence	Deliver sequences 37, 39, 38, 39 with the final event duplicated. The viewer must show authoritative order, expose any temporary gap, and create no duplicate clip, receipt, admission, or release.
P0	admission-revocation-and-release-exactly-once	Accept a receipt, invalidate or supersede it, then accept a correction. History must remain visible, current admission must be truthful, and each dependent may be released at most once.
P1	live-follow-pause-reconnect-replay	Pause off-head, append events, disconnect, reconnect, resume, and refresh. The playhead and selected node must behave predictably, and the final semantic projection must match a clean replay.
P1	extreme-ledger-layout-and-label-access	Exercise long Unicode identifiers, identical display labels, 50 lanes, 1,000 sequences, concurrent branches, zero/unknown durations, retries, and narrow viewport. Assert non-overlap and complete accessible identities.
P1	timeline-topology-semantic-parity	Select the same node in Timeline and Topology. State, blockers, receipts, admission, causal explanation, and source identity must match exactly; switching views must not alter authoritative state.
P1	redaction-and-cross-run-isolation	Switch rapidly between two runs containing distinct sensitive projections. The DOM, inspector, search results, and browser logs must never expose redacted fields or retain data from the previous run.
P2	context-artifact-claim-boundary	Feed tests.total=3650 with no execution results and frontend.exists=false alongside a valid viewer proof. Assert that neither becomes “3,650 tests passed” nor “no frontend exists.”

The mutation-sensitivity case is especially important. A long all-green checklist is not credible until the harness demonstrates that realistic defects turn it red.

Design and architecture warnings

Do not create a second truth model in the viewer. Admission, dependency release, blockers, and settlement should be projections of the journal or authoritative ledger. The UI may derive presentation state, but it should not independently recompute whether a run “really passed.”

Use more precise liveness fields. live=true, mocked=false, and provider_live=false are directionally honest but still ambiguous. Prefer explicit dimensions such as:

viewer_transport_live

ledger_origin

ledger_synthetic

runtime_live

provider_live

A non-mocked local fixture is not necessarily a real provider-backed run.

Keep proof generation independent from projection generation. If runTimelineModel.ts produces the UI state, the browser oracle should not simply import that same function and compare it with itself. Important semantic assertions should derive expectations independently from fixture journal events.

Do not let dashboard chrome grow opportunistically. Workflow summaries and counters are useful, but every new KPI competes with the timeline. The durable information architecture should remain orchestration pool → timeline canvas → inspector, with topology and raw journal as alternate or supporting views.

Treat screenshots as evidence, not as the oracle. Geometry should primarily use DOM bounding boxes, computed overflow, scroll extents, accessible names, and interaction results. Screenshots should corroborate those measurements and help human review.

Read-only UI requires state immutability proof. A GET-only request log and absent mutation buttons do not prove that server-side GET handlers are side-effect free. Compare authoritative source hashes before and after the browser run.

Required deterministic proof after the next change

The next proof packet should contain one validator-produced archive with:

The actual tau.viewer.proof_bundle.v1 manifest and its validation report.

Current focused-test, typecheck, and build receipts with exit codes and separate collected/passed counts.

The executed agentic-evals report for all cases and trials.

A browser proof at the reviewed commit, including per-check evidence and browser-console/network summaries.

Positive and negative proof-bundle validator fixtures.

At least one mutation-sensitivity run showing that an intentionally defective UI produces a failing proof.

Before/after authoritative-ledger hashes proving viewer interactions were read-only.

Bundle-relative screenshots whose dimensions and hashes match the manifest.

The WebGPT response recorded as advisory, without contributing to the deterministic pass decision.

Until those artifacts exist and validate, the honest status remains INCOMPLETE, even though the appropriate next phase is now eval hardening rather than UI or architecture repair.
