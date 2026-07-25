# Pi/Tau TUI Parity Matrix

This matrix tracks Tau's TUI against Pi's interactive coding-agent harness so
the port can move toward daily-use replacement value without erasing Tau-only
capabilities.

## Source Evidence

- Pi built-in slash commands:
  `/tmp/pi/packages/coding-agent/src/core/slash-commands.ts`
- Pi interactive components:
  `/tmp/pi/packages/coding-agent/src/modes/interactive/components/`
- Tau slash commands:
  `src/tau_coding/commands.py`
- Tau TUI implementation:
  `src/tau_coding/tui/app.py`
- Tau TUI render state:
  `src/tau_coding/tui/state.py`

## Labels

- `MATCHED`: Tau covers the Pi behavior closely enough for daily use.
- `PARTIAL`: Tau has the surface, but a Pi behavior or daily-use affordance is
  still missing.
- `MUST`: Missing or partial behavior blocks using Tau as a practical harness.
- `TAU-ONLY`: Tau-specific feature that must be preserved.
- `DEFER`: Do not port yet because Tau lacks a real backing subsystem or the
  feature is not required for tomorrow migration.

## Command Surface

| Area | Pi | Tau | Status | Notes |
| --- | --- | --- | --- | --- |
| Session lifecycle | `/new`, `/resume`, `/clone`, `/fork`, `/tree`, `/name`, `/session`, `/quit` | Same command family | `MATCHED` | Tau also exposes richer tree and branch-summary screens. |
| Context control | `/compact`, `/reload` | Same command family | `MATCHED` | Tau preserves local resource reload behavior. |
| Provider/model | `/model`, `/scoped-models`, `/login`, `/logout` | Same command family | `MATCHED` | Tau routes through local provider config and credential storage. |
| Export/import/share | `/export`, `/import`, `/share`, `/copy` | Same command family | `MATCHED` | Useful enough for migration. |
| Help/config | `/settings`, `/changelog`, `/hotkeys` | Same plus `/config` | `MATCHED` | `/config` is richer in Tau and now has scope tabs. |
| Tau workflows | None | `/workflows` plus sidebar `dag` cue | `TAU-ONLY` | Canonical Tau DAG launcher; must not be replaced by Pi code. |
| Tau provider internals | None | `/scillm` plus sidebar readiness cue | `TAU-ONLY` | SciLLM remains Tau's local LLM proxy surface. |
| Tau evidence gates | None | `/permissions`, approval receipts | `TAU-ONLY` | Preserve fail-closed permission and receipt model. |
| Tau resources/skills | Extension commands only | `/resources`, `/skills`, `/skill`, `/tools`, `/prompts`, sidebar `memory` cue | `TAU-ONLY` | Required for memory-first and skill-driven operation. |

## Interactive Components

| Area | Pi component(s) | Tau surface | Status | Next action |
| --- | --- | --- | --- | --- |
| Prompt editor | `custom-editor`, `custom-entry`, `keybinding-hints` | `PromptInput`, extension input hooks | `PARTIAL` | Keep Pi-like keybindings; avoid replacing Tau extension input plumbing. |
| Model selector | `model-selector`, `scoped-models-selector` | `ModelPickerScreen` | `MATCHED` | Search, tabs, scoped membership, provider toggles, and reorder are present. |
| Session selector | `session-selector`, `session-selector-search` | `SessionPickerScreen` | `MATCHED` | Search, current/all, named-only, path toggle, sort, rename, delete are present. |
| Settings selector | `settings-selector`, related selectors | `SettingsPickerScreen` and picker screens | `PARTIAL` | Tau backs most daily settings and now exposes the external editor command; do not add dead Pi toggles without backing behavior. |
| Config selector | `config-selector` | `ConfigMapScreen` | `PARTIAL` | Scope tabs exist and resource rows now expose scope/state/action; package/write-scope editing still missing. |
| Login/OAuth | `login-dialog`, `oauth-selector` | login provider/method/OAuth screens | `PARTIAL` | Good enough for API/OAuth login; provider picker now shows visible navigation help and empty filter states. |
| Tool execution | `tool-execution`, `bash-execution`, `diff` | transcript renderers in `state.py` and `widgets.py` | `MUST/PARTIAL` | Tau renders shell/tool output, colorizes embedded unified diffs, accepts Pi-style extension tool call/result render hooks, summarizes permission/approval receipts, and now surfaces bash exit/duration/timeout/cancel/truncation/full-output metadata from existing tool result data; richer interactive component objects remain pending. |
| Status/footer | `footer`, `status-indicator`, `countdown-timer` | Tau footer data provider and retry countdown | `PARTIAL` | Footer extensibility exists; compact first-screen readiness now exposes auth/memory/DAG/SciLLM/queue when the sidebar is hidden. |
| Extension UI | `extension-selector`, `extension-input`, `extension-editor`, custom UI | Tau extension screens, chrome hooks, extension tool provenance, and extension tool renderers in live/restored transcripts | `MUST/PARTIAL` | Selector now advertises Pi-style `J/K` navigation and supports tool-output toggle while open; editor now uses Pi-style Enter submit and Shift+Enter newline; preserve current Tau extension API; full Pi-style custom component objects remain pending beyond plain transcript rendering. |
| Images | `show-images-selector`, image component | Tau image visibility setting and image payload rendering | `MATCHED` | Retain current terminal-safe image controls. |
| Workflow/DAG progress | None in Pi | `WorkflowPickerScreen`, DAG/workflow receipts | `TAU-ONLY/MUST` | This is Tau's differentiator and must remain first-class in the TUI. |

## Tomorrow-Usability Ranking

1. `MUST`: Live harness readiness in the TUI. A user should see provider,
   model, auth/readiness, cwd, context, queued commands, memory-first, SciLLM,
   and Tau DAG/workflow entry points without archaeology.
2. `MUST`: Tool execution readability. Shell, diff, file, permission, and
   custom tool activity must be easy to inspect while a session runs.
3. `MUST`: Extension behavior gap closure. Keep Tau's current extension hooks
   and add Pi-compatible behavior where it affects daily use.
4. `PARTIAL`: Config and settings editing. Preserve Tau config/resource rows;
   add real backing behavior before adding more toggles.
5. `DEFER`: Pi toggles without Tau backing such as cache-miss notices or install
   telemetry. Do not create UI switches that imply nonexistent runtime behavior.

## Preservation Rules

- Do not replace Tau's SciLLM proxy path with direct provider calls.
- Do not replace Tau's memory-first and skill/resource surfaces with Pi-only
  extension assumptions.
- Do not replace Tau's DAG/workflow receipt model with generic session UI.
- Do not hide fail-closed approval, permission, and evidence states behind
  optimistic status text.

## Next Slice

Port the next highest-value daily-use gap that is still local and bounded:

`Settings/config editing`: close the next daily-use settings/config gap backed
by real Tau behavior, without adding dead Pi toggles or replacing Tau's
resource/DAG/SciLLM surfaces.

Latest slice evidence:

- Source inspected: Pi `oauth-selector.ts` and `login-dialog.ts`; Tau
  `LoginProviderPickerScreen`, login picker tests, and provider status helpers.
- Destination preserved: Tau credential storage, OAuth/API-key routing,
  provider catalog, login method picker, and provider status/source labels.
- Changed: login provider picker now renders an explicit empty-state row when a
  filter has no matches, and its help line advertises navigation plus
  select/cancel keys.
- Mocked: Textual render proof is fixture-backed.
- Live: no OAuth callback, provider authentication, or credential write.
- Render proof: `/tmp/tau-pi-tui-login-provider-proof-1785012834/proof.json`
  with screenshot
  `/tmp/tau-pi-tui-login-provider-proof-1785012834/tau-login-provider-empty-filter.svg`.
- Remaining gap: full Pi login dialog progress/callback display parity remains
  partial, but the daily provider-selection state is visible and fail-closed.

Previous slice evidence:

- Source inspected: Pi `extension-editor.ts`; Tau `ExtensionEditorScreen`.
- Destination preserved: Tau's extension editor modal, external editor path,
  extension UI request handler, and current custom extension hooks.
- Changed: extension editor now advertises and handles Pi-style `Enter` submit
  plus `Shift+Enter` newline while retaining `Ctrl+Enter` as a compatibility
  submit path.
- Mocked: Textual render proof is fixture-backed.
- Live: no provider-live or live extension backend command.
- Render proof: `/tmp/tau-pi-tui-extension-editor-proof-1785012545/proof.json`
  with screenshot
  `/tmp/tau-pi-tui-extension-editor-proof-1785012545/tau-extension-editor-help.svg`.
- Remaining gap: full Pi-style extension custom component parity remains
  partial.

Earlier slice evidence:

- Source inspected: Pi `extension-selector.ts`; Tau `ExtensionSelectScreen`.
- Destination preserved: Tau's extension UI request handler, extension chrome,
  custom component hooks, transcript rendering, and tool-output state model.
- Changed: extension selector help now advertises `Up/Down/J/K` navigation and
  the configured tool-output toggle; pressing the tool-output key while the
  modal is open toggles Tau's existing transcript tool-result expansion without
  closing the selector.
- Mocked: Textual render proof is fixture-backed.
- Live: no provider-live or live extension backend command.
- Render proof: `/tmp/tau-pi-tui-extension-select-proof-1785012344/proof.json`
  with screenshot
  `/tmp/tau-pi-tui-extension-select-proof-1785012344/tau-extension-select-help.svg`.
- Remaining gap: full Pi-style extension custom component parity remains
  partial.

Earlier slice evidence:

- Source inspected: Pi `footer.ts`, `status-indicator.ts`, and
  `countdown-timer.ts`; Tau `render_compact_session_info` and sidebar helpers.
- Destination preserved: Tau's sidebar, Textual footer keybindings,
  extension-footer API, Memory/SciLLM/DAG surfaces, and retry countdown.
- Changed: compact session info now renders prioritized rows for identity,
  readiness, and metrics; the readiness row exposes `auth`, `mem`, `dag`,
  `llm`, and `q` from existing session data so narrow/sidebar-hidden layouts
  keep daily-use cues visible.
- Mocked: Textual render proof is fixture-backed.
- Live: no provider-live, SciLLM-live, Memory-live, or DAG-live call.
- Render proof: `/tmp/tau-pi-tui-compact-readiness-proof-1785012223/proof.json`
  with screenshot
  `/tmp/tau-pi-tui-compact-readiness-proof-1785012223/tau-compact-readiness-narrow.svg`.
- Remaining gap: Pi's footer still has richer provider/session usage internals
  and custom extension status rendering; Tau has the practical first-screen cues
  but not full Pi component parity.

Earlier slice evidence:

- Source inspected: Pi `config-selector.ts`; Tau `ConfigMapScreen` and
  config-map tests.
- Destination preserved: Tau's existing config map command/path/diagnostic
  rows, durable TUI `disabled_resource_paths`, and resource reload behavior.
- Changed: resource toggle rows now show visible scope/state/action labels such
  as `[project enabled] [disable]`, and the help line names the current state
  plus next action.
- Mocked: Textual render proof is fixture-backed.
- Live: no provider-live or resource-package write-scope call.
- Render proof: `/tmp/tau-pi-tui-config-map-proof-1785011920/proof.json`
  with screenshot
  `/tmp/tau-pi-tui-config-map-proof-1785011920/tau-config-map-scope-state-filtered.svg`.
- Remaining gap: Pi's project/global package write-scope override editor is
  still not implemented in Tau.

Earlier slice evidence:

- Source inspected: Pi `tool-execution.ts`, `bash-execution.ts`, and `diff.ts`.
- Destination preserved: Tau `state.py`/`widgets.py` transcript renderer,
  Memory/SciLLM/DAG/approval/receipt surfaces, and Textual architecture.
- Changed: bash result transcript blocks now expose existing execution metadata:
  exit code, duration, timeout, cancellation, truncation, and full-output path.
- Mocked: no provider mocking. Fixture-backed Textual proof only.
- Live: local render/proof only; no provider-live or SciLLM-live call.
- Remaining gap: richer interactive component object rendering for extension
  tools is still partial.
