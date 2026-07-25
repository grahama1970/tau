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
| Prompt editor | `custom-editor`, `custom-entry`, `keybinding-hints`, `skill-invocation-message` | `PromptInput`, extension input hooks, skill transcript blocks | `PARTIAL` | Keep Pi-like keybindings; skill invocations now render with Pi-style `[skill] name` collapsed labels and labeled expanded bodies; avoid replacing Tau extension input plumbing. |
| Model selector | `model-selector`, `scoped-models-selector` | `ModelPickerScreen` | `MATCHED` | Search, tabs, scoped membership, provider toggles, and reorder are present. |
| Session selector | `session-selector`, `session-selector-search` | `SessionPickerScreen` | `MATCHED` | Search, current/all, named-only, path toggle, sort, rename, delete are present. |
| Branch/trust/tool selectors | `user-message-selector`, `trust-selector`, selector keybindings | `UserMessagePickerScreen`, `TrustPickerScreen`, `ToolsReferenceScreen` | `MATCHED` | `/fork`, `/trust`, and `/tools` preserve Tau's backing flows and now accept Pi-style `j/k` movement as well as configured selector keys. |
| Settings selector | `settings-selector`, related selectors | `SettingsPickerScreen` and picker screens | `PARTIAL` | Tau backs most daily settings, exposes the external editor command, and now shows visible no-match search rows; do not add dead Pi toggles without backing behavior. |
| Config selector | `config-selector` | `ConfigMapScreen` | `PARTIAL` | Scope tabs exist, resource rows expose scope/state/action, resource toggles update in-place, and no-match searches show visible empty rows; package/write-scope editing still missing. |
| Login/OAuth | `login-dialog`, `oauth-selector` | login provider/method/OAuth screens | `PARTIAL` | Good enough for API/OAuth login; provider picker now shows visible navigation help, empty filter states, and fail-closed empty-row selection. |
| Tool execution | `tool-execution`, `bash-execution`, `diff` | transcript renderers in `state.py` and `widgets.py` | `MUST/PARTIAL` | Tau renders shell/tool output, colorizes embedded unified diffs, accepts Pi-style extension tool call/result render hooks including simple component-like render objects, summarizes permission/approval receipts, surfaces bash exit/duration/timeout/cancel/truncation/full-output metadata from existing tool result data, preserves multiple Pi-style image blocks from one tool result, and now shows input-bar terminal command exit codes; full JS Pi component runtime embedding remains out of scope. |
| Status/footer | `footer`, `status-indicator`, `countdown-timer` | Tau footer data provider, prompt chrome, and retry countdown | `PARTIAL` | Footer extensibility exists; compact first-screen readiness exposes auth/memory/DAG/SciLLM/queue when the sidebar is hidden, and prompt chrome now names active compaction/branch/reload/share/terminal operations from real worker state. |
| Extension UI | `extension-selector`, `extension-input`, `extension-editor`, custom UI | Tau extension screens, chrome hooks, extension tool provenance, and extension tool/custom-entry renderers in live/restored transcripts | `MUST/PARTIAL` | Selector now advertises Pi-style `J/K` navigation and supports tool-output toggle while open; editor now uses Pi-style Enter submit and Shift+Enter newline; custom entries now re-render on tool-output expansion and accept simple component-like render objects; preserve current Tau extension API; full JS Pi component runtime embedding remains out of scope. |
| Images | `show-images-selector`, image component | Tau image visibility setting and image payload rendering | `MATCHED` | Tau has terminal-safe image controls, Kitty/iTerm2/fallback rendering, and multiple image payload rendering for figure/graph tool results. |
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

Port the next highest-value daily-use gap that is still local and bounded.
Current candidates:

- `Config write-scope/package overrides`: still partial because Pi can write
  global/project package resource overrides directly from the selector; Tau
  currently has backed disabled-resource toggles, in-place toggle refresh, and
  scope tabs, but not full package override editing.
- `Extension custom component objects`: still partial because Pi can mount
  arbitrary custom TUI components; Tau supports extension selection/input/
  editor/custom screens plus expansion-aware string/JSON/component-like custom
  entries.
- `Cache-miss notices`: defer until Tau assistant/session entries carry the
  provider, model, and timestamp fields needed for Pi's cache-miss algorithm.
  Do not add a fake setting or heuristic notice from aggregate stats.

Latest slice evidence:

- Source inspected: Pi `tool-execution.ts` image block handling and
  `terminal-image.ts`; Tau `ToolImagePayload`, `TuiState.record_tool_result`,
  `TerminalImage`, and transcript renderers.
- Destination preserved: existing single-image `ChatItem.tool_image`
  compatibility, Tau Kitty/iTerm2/fallback terminal image renderer, tool-result
  expansion gate, and TUI image visibility/width settings.
- Changed: Tau now stores `ChatItem.tool_images` as a tuple and extracts
  multiple Pi-style image blocks from `AgentToolResult.data.images` or
  `AgentToolResult.data.content` using `data`/`image_base64` plus
  `mimeType`/`mime_type`. Expanded tool results render every image with
  spacing; collapsed tool rows still hide image payloads.
- Mocked: no.
- Live: local Rich/Textual transcript render path only; no provider-live call.
- Proof:
  `uv run pytest tests/test_tui_app.py tests/test_tui_terminal_image.py -q -k
  'image_payload or terminal_image or multiple_pi_style_image_blocks'`
  reported `19 passed, 447 deselected`; `uv run ruff check
  src/tau_coding/tui/state.py src/tau_coding/tui/widgets.py
  tests/test_tui_app.py` reported all checks passed; `uv run python -m
  py_compile src/tau_coding/tui/state.py src/tau_coding/tui/widgets.py
  tests/test_tui_app.py` exited with no output.
- Render proof:
  `/tmp/tau-pi-tui-multi-image-proof-1785016174/proof.json` with screenshots
  `/tmp/tau-pi-tui-multi-image-proof-1785016174/tau-tool-multi-image-collapsed.svg`
  and
  `/tmp/tau-pi-tui-multi-image-proof-1785016174/tau-tool-multi-image-expanded.svg`.
- Remaining gap: richer artifact-pane UX for generated graphs/figures remains
  open; this slice covers multiple terminal transcript images, not a browser or
  React artifact mirror.

Latest slice evidence:

- Source inspected: Pi `user-message-selector.ts` and `trust-selector.ts`;
  Tau `UserMessagePickerScreen`, `TrustPickerScreen`, `ToolsReferenceScreen`,
  and `ToolsReferenceSearchInput`.
- Destination preserved: Tau `SessionTreeChoice` filtering and `tree_branch`
  flow, project trust store/session trust state, searchable tools reference,
  extension source labels, and configured `TuiKeybindings`.
- Changed: `/fork`, `/trust`, and `/tools` selectors now accept Pi-style `j/k`
  movement in addition to configured up/down keys. The `/tools` search input
  routes `j/k` as movement instead of typing those characters into the filter.
- Mocked: no.
- Live: local Textual UI interaction only; no provider-live call.
- Proof:
  `uv run pytest tests/test_tui_app.py -q -k 'fork_picker or trust_picker or tools_reference_wraps_navigation_like_pi'`
  reported `9 passed, 443 deselected`; `uv run ruff check
  src/tau_coding/tui/app.py tests/test_tui_app.py` reported all checks passed;
  `uv run python -m py_compile src/tau_coding/tui/app.py
  tests/test_tui_app.py` exited with no output.
- Render proof:
  `/tmp/tau-pi-tui-selector-jk-proof-1785015922/proof.json` with screenshots
  `/tmp/tau-pi-tui-selector-jk-proof-1785015922/tau-fork-picker-jk.svg`,
  `/tmp/tau-pi-tui-selector-jk-proof-1785015922/tau-trust-picker-jk.svg`, and
  `/tmp/tau-pi-tui-selector-jk-proof-1785015922/tau-tools-reference-jk.svg`.
- Remaining gap: richer artifact/media rendering is still the next
  user-visible harness gap; Textual remains the frontend, with Tau-owned
  terminal image and Markdown/table renderers carrying the rich media contract.

Latest slice evidence:

- Source inspected: Pi `tool-execution.ts`, `custom-entry.ts`, Tau
  `TuiEventAdapter`, extension tool renderer wiring, and shared custom-renderer
  normalization in `src/tau_coding/tui/state.py`.
- Destination preserved: Tau's extension tool source labels, call/result
  renderer maps, transcript collapse/expand behavior, and existing tool result
  summary metadata.
- Changed: no additional code after the custom-entry component slice; the shared
  renderer normalizer now also covers extension tool call/result renderers that
  return simple Pi-style `render(width)` objects.
- Mocked: no.
- Live: no provider-live or live tool subprocess call; Textual UI interaction
  used a local `FakeSession` plus adapter-applied tool start/end events.
- Render proof: `/tmp/tau-pi-tui-tool-component-proof-1785015240/proof.json`
  with screenshots
  `/tmp/tau-pi-tui-tool-component-proof-1785015240/tau-tool-component-collapsed.svg`
  and
  `/tmp/tau-pi-tui-tool-component-proof-1785015240/tau-tool-component-expanded.svg`.
- Remaining gap: full JavaScript Pi component runtime embedding is not ported;
  Tau supports transcript text projection of simple component-like renderables.

Latest slice evidence:

- Source inspected: Pi `custom-entry.ts` and Tau custom-entry renderer
  normalization in `src/tau_coding/tui/state.py`.
- Destination preserved: Tau's extension entry/message renderer maps,
  expansion-aware rerender path, durable custom entries, and Textual transcript
  renderer.
- Changed: extension custom-entry renderers may now return simple Pi-style
  component-like objects exposing `render(width)` or `lines`; Tau converts
  those to transcript text without importing Pi's component runtime or changing
  existing string/mapping/sequence handling.
- Mocked: no.
- Live: no provider-live or live extension subprocess call; Textual UI
  interaction used a local `FakeSession` with a component-like renderer.
- Render proof: `/tmp/tau-pi-tui-custom-component-proof-1785015055/proof.json`
  with screenshots
  `/tmp/tau-pi-tui-custom-component-proof-1785015055/tau-custom-component-collapsed.svg`
  and
  `/tmp/tau-pi-tui-custom-component-proof-1785015055/tau-custom-component-expanded.svg`.
- Remaining gap: full JavaScript Pi component runtime embedding is not ported;
  Tau supports text projection of simple component-like renderables.

Latest slice evidence:

- Source inspected: Pi `config-selector.ts` `ResourceList.handleInput()`,
  `toggleResource()`, `updateItem()`, and `onToggle`; Tau `ConfigMapScreen`,
  config-map item builders, durable TUI settings, and config-map tests.
- Destination preserved: Tau's command/path/diagnostic config rows, durable
  disabled-resource settings, session `set_disabled_resource_paths()` adapter,
  resource reload worker, scope tabs, and Tau-only resource surfaces.
- Changed: selecting a loaded resource toggle inside `/config` now updates the
  open modal in place and keeps the user in the config map, matching Pi's
  selector affordance while still persisting through Tau's real settings path.
- Mocked: no.
- Live: no provider-live or external service call; Textual UI interaction used
  a `FakeSession` resource/settings adapter.
- Render proof:
  `/tmp/tau-pi-tui-config-inplace-toggle-proof-1785014874/proof.json` with
  screenshot
  `/tmp/tau-pi-tui-config-inplace-toggle-proof-1785014874/tau-config-inplace-resource-toggle.svg`.
- Remaining gap: Pi's project/global package write-scope override editor is
  still not implemented in Tau.

Latest slice evidence:

- Source inspected: Pi `status-indicator.ts` and `bordered-loader.ts`; Tau
  prompt chrome, activity worker predicates, compaction, branch summary, reload,
  share, and terminal command workers.
- Destination preserved: Tau's existing worker lifecycle, retry countdown,
  extension working indicator, terminal title/progress indicator, and transcript
  status rows.
- Changed: prompt chrome now names active compaction, branch, reload, share,
  and terminal operations from real worker state. Retry countdown remains the
  highest-priority prompt-chrome message; extension-provided working messages
  remain next priority.
- Mocked: Textual worker/screenshot proof is fixture-backed.
- Live: no provider-live or external service call.
- Render proof: `/tmp/tau-pi-tui-operation-status-proof-1785014486/proof.json`
  with screenshot
  `/tmp/tau-pi-tui-operation-status-proof-1785014486/tau-operation-status-compaction.svg`.
- Remaining gap: Pi's exact status component hierarchy is not copied; Tau uses
  its Textual prompt-chrome surface backed by the same operation state.

Latest slice evidence:

- Source inspected: Pi `skill-invocation-message.ts`; Tau parsed skill
  invocation state and transcript renderer.
- Destination preserved: Tau's skill parser, memory/resource-driven skill
  loading, extension plumbing, and tool-output expansion key.
- Changed: skill invocations now render collapsed as `[skill] <name>
  (Ctrl+O to expand)` and expanded as a labeled skill body without repeating
  the collapsed sentence.
- Mocked: Rich render proof is fixture-backed.
- Live: no provider-live or live skill subprocess call.
- Render proof: `/tmp/tau-pi-tui-skill-block-proof-1785014321/proof.json`
  with screenshots
  `/tmp/tau-pi-tui-skill-block-proof-1785014321/tau-skill-block-collapsed.svg`
  and
  `/tmp/tau-pi-tui-skill-block-proof-1785014321/tau-skill-block-expanded.svg`.
- Remaining gap: arbitrary Pi custom component embedding remains partial.

Latest slice evidence:

- Source inspected: Pi `bash-execution.ts` and `tool-execution.ts`; Tau
  terminal command result formatter and TUI terminal command worker.
- Destination preserved: Tau input-bar terminal command routing, output
  streaming, transcript collapse/expand behavior, and workflow receipt summary
  formatting.
- Changed: failed input-bar terminal command results now display the concrete
  exit code in the visible bash status line.
- Mocked: Rich render proof is fixture-backed.
- Live: no provider-live or external service call.
- Render proof: `/tmp/tau-pi-tui-terminal-exit-proof-1785014090/proof.json`
  with screenshot
  `/tmp/tau-pi-tui-terminal-exit-proof-1785014090/tau-terminal-command-exit-code.svg`.
- Remaining gap: richer interactive component object rendering for extension
  tools is still partial.

Latest slice evidence:

- Source inspected: Pi `oauth-selector.ts`; Tau `LoginProviderPickerScreen`,
  provider empty-state test, and previous provider empty-state proof.
- Destination preserved: Tau's provider catalog, API-key/subscription method
  routing, credential store boundaries, and visible empty-row state.
- Changed: selecting the synthetic no-match provider row now returns without
  dismissing the provider picker or indexing into an empty provider list.
- Mocked: Textual render/interaction proof is fixture-backed.
- Live: no OAuth callback, API-key credential write, or provider-live
  authentication.
- Render proof:
  `/tmp/tau-pi-tui-login-empty-select-proof-1785013814/proof.json` with
  screenshot
  `/tmp/tau-pi-tui-login-empty-select-proof-1785013814/tau-login-provider-empty-row-safe-select.svg`.
- Remaining gap: full Pi login dialog progress/callback display parity remains
  partial.

Previous slice evidence:

- Source inspected: Pi `custom-entry.ts` and `interactive-mode.ts`
  `addCustomEntryToChat`; Tau `TuiState.add_custom_entry`,
  `TauTuiApp.action_toggle_tool_results`, custom-entry tests, and transcript
  renderer state.
- Destination preserved: Tau's Python extension entry/message renderers,
  string/JSON transcript rendering, tool-output expansion key, and restored
  durable custom entries.
- Changed: custom entry transcript items now retain their source entry and
  renderer metadata, and they re-render when tool-output expansion changes.
  This ports Pi's `CustomEntryComponent.setExpanded()` behavior through Tau's
  existing `Ctrl+O` expansion model.
- Mocked: Textual render proof is fixture-backed.
- Live: no live extension subprocess or JavaScript custom component embedding.
- Render proof: `/tmp/tau-pi-tui-custom-entry-proof-1785013546/proof.json`
  with screenshots
  `/tmp/tau-pi-tui-custom-entry-proof-1785013546/tau-custom-entry-collapsed.svg`
  and
  `/tmp/tau-pi-tui-custom-entry-proof-1785013546/tau-custom-entry-expanded.svg`.
- Remaining gap: Pi can mount arbitrary custom component objects; Tau now
  supports expansion-aware string/JSON custom entry renderers but not arbitrary
  component embedding.

Previous slice evidence:

- Source inspected: Pi `config-selector.ts`; Tau `SettingsPickerScreen`,
  `ConfigMapScreen`, settings/config tests, and resource row helpers.
- Destination preserved: Tau's durable TUI settings, config map command/path/
  diagnostic rows, resource toggles, scope tabs, and custom resource surfaces.
- Changed: settings and config map no-match searches now render explicit visible
  empty rows in the list, not only footer help text. This mirrors Pi's config
  selector fail-closed empty feedback without inventing unbacked write-scope
  behavior.
- Mocked: Textual render proof is fixture-backed.
- Live: no provider-live, resource package mutation, or filesystem
  write-scope operation.
- Render proof: `/tmp/tau-pi-tui-empty-state-proof-1785013218/proof.json`
  with screenshots
  `/tmp/tau-pi-tui-empty-state-proof-1785013218/tau-settings-empty-filter.svg`
  and
  `/tmp/tau-pi-tui-empty-state-proof-1785013218/tau-config-empty-filter.svg`.
- Remaining gap: Pi's project/global package write-scope override editor is
  still not implemented in Tau.

Previous slice evidence:

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
