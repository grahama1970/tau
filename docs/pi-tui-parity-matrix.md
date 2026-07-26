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
| Help/config | `/settings`, `/changelog`, `/hotkeys` | Same plus `/config` | `MATCHED` | `/config` is richer in Tau and now has scope tabs; `/hotkeys` and `/changelog` render through the themed Markdown command-output modal. |
| Tau workflows | None | `/workflows` plus sidebar `dag` cue | `TAU-ONLY` | Canonical Tau DAG launcher; must not be replaced by Pi code. |
| Tau provider internals | None | `/scillm` plus sidebar readiness cue | `TAU-ONLY` | SciLLM remains Tau's local LLM proxy surface. |
| Tau evidence gates | None | `/permissions`, approval receipts | `TAU-ONLY` | Preserve fail-closed permission and receipt model. |
| Tau resources/skills | Extension commands only | `/resources`, `/skills`, `/skill`, `/tools`, `/prompts`, sidebar `memory` cue | `TAU-ONLY` | Required for memory-first and skill-driven operation. |

## Interactive Components

| Area | Pi component(s) | Tau surface | Status | Next action |
| --- | --- | --- | --- | --- |
| Prompt editor | `custom-editor`, `custom-entry`, `keybinding-hints`, `skill-invocation-message` | `PromptInput`, extension input hooks, skill transcript blocks | `MATCHED` | Tau has a multiline TextArea prompt, Pi-style submit/newline keybindings, external editor, clipboard text/image paste, prompt history/editing, extension shortcut hooks, user Markdown table/image rendering, and Pi-style skill invocation blocks; avoid replacing Tau extension input plumbing. |
| Model selector | `model-selector`, `scoped-models-selector` | `ModelPickerScreen` | `MATCHED` | Search, tabs, scoped membership, provider toggles, and reorder are present. |
| Session selector | `session-selector`, `session-selector-search` | `SessionPickerScreen` | `MATCHED` | Search, current/all, named-only, path toggle, sort, rename, delete are present. |
| Branch/trust/tool selectors | `user-message-selector`, `trust-selector`, selector keybindings | `UserMessagePickerScreen`, `TrustPickerScreen`, `ToolsReferenceScreen` | `MATCHED` | `/fork` and `/trust` preserve Tau's backing flows and accept Pi-style `j/k` movement; `/tools` preserves searchable text input while the list accepts `j/k` movement when focused. |
| Settings selector | `settings-selector`, related selectors | `SettingsPickerScreen` and picker screens | `PARTIAL` | Tau backs most daily settings, exposes the external editor command, and now shows visible no-match search rows; do not add dead Pi toggles without backing behavior. |
| Config selector | `config-selector` | `ConfigMapScreen` | `PARTIAL` | Scope tabs exist, resource rows expose scope/state/action, resource toggles update in-place, backed user and project TUI settings write targets are visible, project resources can be disabled through `<cwd>/.tau/tui.json`, and no-match searches show visible empty rows; Pi package-source filter editing still missing. |
| Login/OAuth | `login-dialog`, `oauth-selector` | login provider/method/OAuth screens | `PARTIAL` | Good enough for API/OAuth login; provider picker now shows visible navigation help, empty filter states, and fail-closed empty-row selection. |
| Tool execution | `tool-execution`, `bash-execution`, `diff` | transcript renderers in `state.py` and `widgets.py` | `MUST/PARTIAL` | Tau renders shell/tool output, colorizes embedded unified diffs, accepts Pi-style extension tool call/result render hooks including simple component-like render objects, summarizes permission/approval receipts, surfaces bash exit/duration/timeout/cancel/truncation/full-output metadata from existing tool result data, preserves multiple Pi-style image blocks from one tool result, and now shows input-bar terminal command exit codes; full JS Pi component runtime embedding remains out of scope. |
| Export/artifact viewing | `/export`, `exportToHtml`, RPC `export_html` | Tau `/export`, `/artifacts`, `session_export.py`, TUI command output | `MATCHED` | Tau writes real HTML/JSONL session artifacts, opens a persistent TUI result modal with the artifact path and `file://` URI, supports explicit `/export --open`, renders assistant Markdown tables plus embedded local image links and fenced DOT graph artifacts in HTML exports, attempts Mermaid fail-closed when the local CLI/browser runtime works, makes embedded figures/graphs openable full-size in the browser, and now has a searchable `/artifacts` browser with kind tabs and selected previews for current-transcript image, graph, Markdown report, JSON receipt, and HTML export artifacts. |
| Status/footer | `footer`, `status-indicator`, `countdown-timer` | Tau footer data provider, prompt chrome, compact readiness, sidebar, and retry countdown | `MATCHED` | Tau exposes cwd/session title, provider/model/thinking, context and usage/cost stats, auth/memory/DAG/SciLLM/queue readiness, loop monitor state, queued-message controls, Textual footer keybindings, retry/compaction/branch/share/reload operation labels, and Pi-style extension footer/status hooks. |
| Extension UI | `extension-selector`, `extension-input`, `extension-editor`, custom UI | Tau extension screens, chrome hooks, extension tool provenance, and extension tool/custom-entry renderers in live/restored transcripts | `MUST/PARTIAL` | Selector now advertises Pi-style `J/K` navigation and supports tool-output toggle while open; editor now uses Pi-style Enter submit and Shift+Enter newline; custom entries now re-render on tool-output expansion, accept simple component-like render objects, and render Pi-style text content as Markdown by default; preserve current Tau extension API; full JS Pi component runtime embedding remains out of scope. |
| Images, figures, graphs, tables, receipts, HTML | `show-images-selector`, image component, Markdown renderer, HTML export | Tau image visibility setting, Markdown renderer, artifact preview rendering | `MATCHED` | Tau has terminal-safe image controls, Kitty/iTerm2/fallback rendering, non-PNG-to-PNG conversion for Kitty, multiple image payload rendering for figure/graph tool results, local Markdown image links in user/assistant/custom transcript output, rendered Markdown tables in transcript and artifact previews, JSON receipt previews with schema/status/run fields, HTML export previews with extracted title/headings/table/text, proven local-tool rendering for fenced DOT graph source, searchable `/artifacts` selected-preview rendering, and fail-closed Mermaid rendering when the local CLI/browser runtime is unavailable. |
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

- `Artifact report inspection`: continue sharpening `/artifacts` around common
  Tau report bundles only when backed by real linked files or generated
  artifacts. Search, kind tabs, and selected previews are now present; do not
  create static dashboard inventory.
- `Config write-scope/package overrides`: still partial because Pi can write
  global/project package resource overrides directly from the selector; Tau
  currently has backed user/project disabled-resource toggles, visible write
  targets, in-place toggle refresh, and scope tabs, but not full Pi package
  filter editing.
- `Extension custom component objects`: still partial because Pi can mount
  arbitrary custom TUI components; Tau supports extension selection/input/
  editor/custom screens plus expansion-aware string/JSON/component-like custom
  entries and Markdown fallback for Pi-style text content.
- `Cache-miss notices`: defer until Tau assistant/session entries carry the
  provider, model, and timestamp fields needed for Pi's cache-miss algorithm.
  Do not add a fake setting or heuristic notice from aggregate stats.

Latest slice evidence:

- Source inspected: Pi `model-selector.ts`, `config-selector.ts`,
  `session-selector.ts`, `tree-selector.ts`; Tau `ArtifactBrowserScreen`,
  `_visual_artifacts_from_state`, existing artifact preview helpers, and
  artifact-browser tests.
- Destination preserved: Tau's `/artifacts` open/copy workflow, terminal image
  renderer, Markdown report preview, JSON receipt preview, HTML export preview,
  Memory/SciLLM/DAG/workflow surfaces, receipt model, and fail-closed
  missing/invalid-artifact behavior.
- Changed: `/artifacts` now has a focused search input and Pi-style artifact
  kind tabs (`all`, `image`, `markdown`, `json`, `html`). The list, selected
  preview, copy/open actions, no-match row, and help text all operate on the
  filtered artifact set. The selected artifact preview is now a scrollable pane
  so longer Markdown tables, JSON receipts, and HTML summaries are not clipped
  to a fixed static block. The modal exposes Tab/Ctrl+I as the kind switch and
  keeps the visible summary counts across real local PNG, Markdown, JSON, and
  HTML artifacts.
- Mocked: yes for the session/provider fixture used by the proof; no fake
  artifact files or provider responses.
- Live: local Textual `/artifacts` modal rendering with real temporary PNG,
  Markdown, JSON receipt, and HTML artifact files; no provider-live or
  SciLLM-live call.
- Proof: `uv run pytest tests/test_tui_app.py -q -k 'artifacts_command or
  artifact_search or hotkeys'` reported `7 passed, 460 deselected`; `uv run
  ruff check src/tau_coding/tui/app.py tests/test_tui_app.py` reported all
  checks passed; `uv run python -m py_compile src/tau_coding/tui/app.py
  tests/test_tui_app.py` produced no errors; render proof
  `/tmp/tau-pi-tui-artifact-search-scroll-proof-vsvchceh/proof.json` with
  screenshot
  `/tmp/tau-pi-tui-artifact-search-scroll-proof-vsvchceh/tau-artifact-search-scroll-preview.svg`;
  CDP marker `.codex/ui-verification/latest.json` points to screenshot
  `/tmp/codex-ui-verification/tau-pi-tui-parity-20260724T1556/tau-artifact-search-scroll-preview/20260726T000813Z.png`.
- Remaining gap: `/artifacts` is now navigable for core artifact types, but
  Tau-native bundle aggregation should wait for actual bundle artifacts and
  source contracts.

Earlier slice evidence:

- Source inspected: Pi `core/export-html/index.ts`, Pi export command handling,
  Tau `session_export.py`, `ArtifactBrowserScreen`, `_visual_artifacts_from_state`,
  and artifact-browser tests.
- Destination preserved: Tau's `/artifacts` open/copy workflow, Markdown report
  preview, JSON receipt preview, terminal image renderer,
  Memory/SciLLM/DAG/workflow surfaces, receipt model, and fail-closed
  missing/invalid-artifact behavior.
- Changed: `/artifacts` now discovers linked `.html` and `.htm` artifacts from
  visible transcript text and tool-result text. HTML artifacts show in the
  artifact list with `text/html` and preview extracted visible content in the
  TUI: file/type/size/title summary, headings, text, and table rows. Browser
  opening remains the authoritative full-render path.
- Mocked: no.
- Live: local Textual `/artifacts` modal with a real Tau `render_session_html`
  export artifact linked from transcript text; no provider-live or SciLLM-live
  call.
- Proof: `uv run pytest tests/test_tui_app.py -q -k 'artifacts_command or
  markdown_reports or json_receipts or html_exports or hotkeys'` reported `6
  passed, 460 deselected`; `uv run ruff check src/tau_coding/tui/app.py
  tests/test_tui_app.py` reported all checks passed; `uv run python -m
  py_compile src/tau_coding/tui/app.py tests/test_tui_app.py` produced no
  errors; render proof `/tmp/tau-pi-tui-html-artifact-proof-jy9nejpu/proof.json`
  with screenshot
  `/tmp/tau-pi-tui-html-artifact-proof-jy9nejpu/tau-html-artifact-preview.svg`.
- Remaining gap: `/artifacts` now covers visual outputs, Markdown reports, JSON
  receipts, and HTML exports; richer multi-artifact navigation and Tau-native
  bundle views should be added only when backed by actual artifacts.

Earlier slice evidence:

- Source inspected: Pi `core/export-html/index.ts`,
  `core/export-html/tool-renderer.ts`, Pi Markdown/image renderer references,
  Tau `ArtifactBrowserScreen`, `_visual_artifacts_from_state`, workflow receipt
  summarization tests, and artifact-browser tests.
- Destination preserved: Tau's `/artifacts` open/copy workflow, terminal image
  renderer, Markdown report preview, Memory/SciLLM/DAG/workflow surfaces,
  receipt model, and fail-closed missing/invalid-artifact behavior.
- Changed: `/artifacts` now discovers linked `.json` artifacts from visible
  transcript text and tool-result text. JSON artifacts show in the artifact list
  with `application/json`, preview a compact summary table for common Tau
  receipt fields (`schema`, `status`, `workflow_id`, `run_id`, `node_id`,
  `run_receipt_path`), and include syntax-highlighted pretty JSON below the
  summary.
- Mocked: no.
- Live: local Textual `/artifacts` modal with a real temporary Tau-style JSON
  receipt file linked from transcript text; no provider-live or SciLLM-live
  call.
- Proof: `uv run pytest tests/test_tui_app.py -q -k 'artifacts_command or
  markdown_reports or json_receipts or hotkeys'` reported `5 passed, 460
  deselected`; `uv run ruff check src/tau_coding/tui/app.py
  tests/test_tui_app.py` reported all checks passed; `uv run python -m
  py_compile src/tau_coding/tui/app.py tests/test_tui_app.py` produced no
  errors; render proof `/tmp/tau-pi-tui-json-artifact-proof-8lb18cv3/proof.json`
  with screenshot
  `/tmp/tau-pi-tui-json-artifact-proof-8lb18cv3/tau-json-artifact-preview.svg`.
- Remaining gap: `/artifacts` now covers visual outputs, Markdown reports, and
  JSON receipts; HTML previews and richer multi-artifact navigation should be
  added only when backed by actual Tau artifacts.

Earlier slice evidence:

- Source inspected: Pi `assistant-message.ts`, `custom-message.ts`,
  `tool-execution.ts`, and Markdown/image rendering references; Tau
  `ArtifactBrowserScreen`, `_visual_artifacts_from_state`,
  `markdown_visual_payloads`, and artifact-browser tests.
- Destination preserved: Tau's `/artifacts` open/copy workflow, terminal image
  renderer, transcript visual discovery, Memory/SciLLM/DAG/workflow surfaces,
  receipt model, and fail-closed missing-artifact behavior.
- Changed: `/artifacts` now discovers local Markdown report links from visible
  transcript text and tool-result text. Markdown artifacts show in the artifact
  list with `text/markdown`, preview as rendered Markdown tables in the TUI, and
  render any local image/graph links inside the report through Tau's existing
  terminal-image fallback path.
- Mocked: no.
- Live: local Textual `/artifacts` modal with a real temporary Markdown report
  file and real linked PNG image file; no provider-live or SciLLM-live call.
- Proof: `uv run pytest tests/test_tui_app.py -q -k 'artifacts_command or
  markdown_reports'` reported `2 passed, 462 deselected`; `uv run ruff check
  src/tau_coding/tui/app.py tests/test_tui_app.py` reported all checks passed;
  `uv run python -m py_compile src/tau_coding/tui/app.py
  tests/test_tui_app.py` produced no errors; render proof
  `/tmp/tau-pi-tui-markdown-artifact-proof-hdl0ta0j/proof.json` with screenshot
  `/tmp/tau-pi-tui-markdown-artifact-proof-hdl0ta0j/tau-markdown-artifact-preview.svg`.
- Remaining gap: `/artifacts` now covers visual outputs and Markdown reports,
  but common receipt JSON, HTML previews, and richer report navigation should be
  added only when backed by actual Tau artifacts.

Earlier slice evidence:

- Source inspected: Pi `config-selector.ts`; Tau `ConfigMapScreen`,
  `TuiSettings`, CLI/TUI session startup, resource disabled filtering, and
  config-map tests.
- Destination preserved: Tau's user settings file, project trust file,
  resource reload path, Memory/SciLLM/DAG/workflow surfaces, receipt model, and
  fail-closed resource diagnostics.
- Changed: Tau now has project-local TUI settings backed by
  `<cwd>/.tau/tui.json`. `/config` shows both user and project write targets;
  project-scoped resource rows show `[project disable]` or `[project enable]`
  and write to the project file, while user-scoped rows continue to write to
  `~/.tau/tui.json`. TUI and print-mode startup merge user and project
  disabled-resource paths before resource discovery.
- Mocked: no.
- Live: local Textual `/config` modal action with a real temporary project
  directory and real project `.tau/tui.json` write; no provider-live or
  SciLLM-live call.
- Proof: `uv run pytest tests/test_tui_config.py tests/test_tui_app.py -q -k
  'project_tui_settings or config_map'` reported `9 passed, 533 deselected`;
  `uv run ruff check src/tau_coding/tui/config.py src/tau_coding/tui/app.py
  src/tau_coding/tui/__init__.py src/tau_coding/cli.py tests/test_tui_config.py
  tests/test_tui_app.py` reported all checks passed; `uv run python -m
  py_compile src/tau_coding/tui/config.py src/tau_coding/tui/app.py
  src/tau_coding/tui/__init__.py src/tau_coding/cli.py tests/test_tui_config.py
  tests/test_tui_app.py` produced no errors; render proof
  `/tmp/tau-pi-tui-project-config-proof-1785021907/proof.json` with screenshot
  `/tmp/tau-pi-tui-project-config-proof-1785021907/tau-project-config-override.svg`.
- Remaining gap: Pi can edit package-source include/exclude filters from the
  selector; Tau now has real project-local resource disables, but not package
  filter editing.

Earlier slice evidence:

- Source inspected: Pi `custom-message.ts`; Tau `_default_custom_entry_text`,
  `_render_custom_message_entry`, custom-entry renderer normalization, and
  custom-entry TUI tests.
- Destination preserved: Tau's extension renderer callbacks, component-like
  object normalization, JSON fallback for non-content custom data, custom modal
  API, Memory/SciLLM/DAG/workflow surfaces, and receipt model.
- Changed: renderer-less custom entries that carry Pi-style `content` text now
  display that text as Markdown instead of dumping the whole JSON payload.
  Markdown tables and local image links inside extension custom content mount
  the same table/image widgets used by transcript messages.
- Mocked: no.
- Live: local Textual render path with a renderer-less custom entry, Markdown
  table, and real PNG image link; no provider-live or SciLLM-live call.
- Proof: `uv run pytest tests/test_tui_app.py -q -k 'custom_entries or
  custom_message_content or custom_entry_component or textual_markdown'`
  reported `8 passed, 455 deselected`; `uv run ruff check
  src/tau_coding/tui/state.py tests/test_tui_app.py` reported all checks
  passed; `uv run python -m py_compile src/tau_coding/tui/state.py
  tests/test_tui_app.py` produced no errors; render proof
  `/tmp/tau-pi-tui-custom-content-proof-1785021116/proof.json` with
  screenshot
  `/tmp/tau-pi-tui-custom-content-proof-1785021116/tau-custom-content-markdown.svg`.
- Remaining gap: arbitrary Pi JavaScript component embedding is still not
  ported; Tau covers the common text/Markdown fallback path and simple
  component-like Python render objects.

Earlier slice evidence:

- Source inspected: Pi `user-message.ts` and `assistant-message.ts`; Tau
  `TranscriptMessageWidget`, `_use_plain_transcript_body`,
  `_transcript_item_markdown`, `_render_chat_body`, and Markdown image tests.
- Destination preserved: Tau's selectable tool/skill/error transcript paths,
  assistant/custom/status Markdown rendering, terminal image settings,
  artifact discovery, Memory/SciLLM/DAG/workflow surfaces, and receipt model.
- Changed: user transcript rows now use the Markdown render path instead of
  escaped plain text. User-authored Markdown tables render as tables, and local
  image links mount the same terminal-image preview widgets as assistant
  messages.
- Mocked: no.
- Live: local Textual transcript render path with a real PNG file linked from
  a user message; no provider-live or SciLLM-live call.
- Proof: `uv run pytest tests/test_tui_app.py -q -k 'textual_markdown or
  user_markdown or tool_image_payload'` reported `10 passed, 452 deselected`;
  `uv run ruff check src/tau_coding/tui/widgets.py tests/test_tui_app.py`
  reported all checks passed; `uv run python -m py_compile
  src/tau_coding/tui/widgets.py tests/test_tui_app.py` produced no errors;
  render proof `/tmp/tau-pi-tui-user-markdown-proof-1785020902/proof.json`
  with screenshot
  `/tmp/tau-pi-tui-user-markdown-proof-1785020902/tau-user-markdown-visuals.svg`.
- Remaining gap: exact pixel inline display still depends on terminal image
  protocol support; user-message selection behavior should be watched during
  daily use because Markdown widgets replace the previous plain-row renderer.

Earlier slice evidence:

- Source inspected: Pi `tool-execution.ts`, `assistant-message.ts`, and
  Markdown/image settings references; Tau `ArtifactBrowserScreen`,
  `_visual_artifacts_from_state`, `markdown_visual_payloads`, `TerminalImage`,
  and existing `/artifacts` tests.
- Destination preserved: Tau's transcript visual payload discovery, Graphviz
  artifact rendering, `show_images`/`image_width_cells` settings, artifact open
  and copy actions, Memory/SciLLM/DAG/workflow surfaces, and receipt model.
- Changed: `/artifacts` now shows a selected-artifact preview under the
  visual-artifact list. The preview uses Tau's TerminalImage renderer, so
  capable Kitty/iTerm2 terminals can inline-render the selected image/figure
  and other terminals get a visible fallback instead of a buried path.
- Mocked: no.
- Live: local Textual `/artifacts` modal render path with a real PNG file and
  local Graphviz artifact generated from transcript Markdown; no provider-live
  or SciLLM-live call.
- Proof: `uv run pytest tests/test_tui_app.py tests/test_commands.py -q -k
  'artifacts or tool_image_payload'` reported `7 passed, 511 deselected`; `uv
  run ruff check src/tau_coding/tui/app.py tests/test_tui_app.py` reported all
  checks passed; `uv run python -m py_compile src/tau_coding/tui/app.py
  tests/test_tui_app.py` produced no errors; render proof
  `/tmp/tau-pi-tui-artifact-preview-proof-1785020650/proof.json` with
  screenshot
  `/tmp/tau-pi-tui-artifact-preview-proof-1785020650/tau-artifact-preview.svg`.
- Remaining gap: actual pixel inline display still depends on the operator's
  terminal image protocol support; richer artifact detail panes and native
  Markdown-table navigation remain possible polish.

Earlier slice evidence:

- Source inspected: Pi `config-selector.ts`; Tau `ConfigMapScreen`,
  `_config_map_item_label`, `_config_map_resource_state`, and config-map tests.
- Destination preserved: Tau's config rows, command/path/diagnostic actions,
  durable user `disabled_resource_paths`, session reload after toggles, Memory,
  SciLLM, DAG/workflow, receipt, and approval surfaces.
- Changed: `/config` now shows the real backed write target as
  `Write target: User TUI settings ([user] ...)`; resource toggle rows now
  show `[user disable]` or `[user enable]`, and the selected-row help says
  toggles write to user TUI settings.
- Mocked: no.
- Live: local Textual config-map render path and real Tau user-settings toggle
  backend in focused tests; no provider-live, SciLLM-live, or project-package
  write-scope call.
- Proof: `uv run pytest tests/test_tui_app.py -q -k 'config_map'` reported
  `5 passed, 456 deselected`; `uv run ruff check src/tau_coding/tui/app.py
  tests/test_tui_app.py` reported all checks passed; `uv run python -m
  py_compile src/tau_coding/tui/app.py tests/test_tui_app.py` produced no
  errors; render proof
  `/tmp/tau-pi-tui-config-write-target-proof-1785020353/proof.json` with
  screenshot
  `/tmp/tau-pi-tui-config-write-target-proof-1785020353/tau-config-write-target.svg`.
- Remaining gap: Pi's project-local package override editor is still not
  implemented in Tau.

Earlier slice evidence:

- Source inspected: Pi `interactive-mode.ts` hotkey Markdown output and Tau
  `CommandOutputScreen`, `_render_tui_hotkeys_message`, and
  `ThemedMarkdownWidget`.
- Destination preserved: Tau's literal command-output mode for plain diagnostic
  screens, configured-key hotkey rendering, existing transcript Markdown
  renderer, and Tau-specific shortcut details.
- Changed: `CommandOutputScreen` now has an explicit Markdown-rendering mode for
  `/hotkeys` and `/changelog`. `/hotkeys` starts with a Pi-style Markdown table
  for the most important daily-use keys, including paste image/text, drop file
  attachment, and `/artifacts` visual browsing, while retaining the detailed
  Tau shortcut sections below.
- Mocked: no.
- Live: local Textual command-output render path; no provider-live call.
- Proof: `uv run pytest tests/test_tui_app.py -q -k 'hotkeys or changelog or
  command_modal_renders_literal_markup_text'` reported `4 passed, 457
  deselected`; `uv run ruff check src/tau_coding/tui/app.py
  tests/test_tui_app.py` reported all checks passed; `uv run python -m
  py_compile src/tau_coding/tui/app.py tests/test_tui_app.py` exited with no
  output. Render proof:
  `/tmp/tau-pi-tui-command-markdown-proof-1785019770/proof.json` with
  screenshot
  `/tmp/tau-pi-tui-command-markdown-proof-1785019770/tau-hotkeys-markdown-table.svg`.
- Remaining gap: config write-scope/package override parity remains open;
  arbitrary Pi custom component mounting remains out of scope unless backed by
  Tau extension primitives.

Latest slice evidence:

- Source inspected: Pi `core/export-html/index.ts`,
  `core/export-html/tool-renderer.ts`, `components/image.ts`, and Tau
  `state.py`, `widgets.py`, `session_export.py`, and `/export` TUI handling.
- Destination preserved: Tau's Textual transcript state, terminal image
  renderer, rich HTML export, Memory/SciLLM/DAG surfaces, and fail-closed
  artifact handling.
- Changed: `/artifacts` opens a TUI visual artifact browser backed by the
  current transcript. It lists real local Markdown image links, rendered
  DOT/Graphviz/Mermaid artifacts when local renderers succeed, and tool-result
  image payloads. Enter opens the selected artifact through the OS/browser
  handler; `c` copies the artifact path. In-memory graph/tool images are
  materialized under `/tmp/tau-tui-artifacts`; absent artifacts render an empty
  state instead of sample data.
- Mocked: no.
- Live: local Textual command path with real local image bytes and Graphviz
  rendering when `dot` is installed; no provider-live call.
- Proof: `uv run pytest tests/test_commands.py tests/test_tui_app.py -q -k
  'artifacts or export_open or export_command'` reported `6 passed, 512
  deselected`; `uv run ruff check src/tau_coding/commands.py
  src/tau_coding/tui/widgets.py src/tau_coding/tui/app.py tests/test_commands.py
  tests/test_tui_app.py` reported all checks passed; `uv run python -m
  py_compile src/tau_coding/commands.py src/tau_coding/tui/widgets.py
  src/tau_coding/tui/app.py tests/test_commands.py tests/test_tui_app.py`
  exited with no output. Render proof:
  `/tmp/tau-pi-tui-artifacts-browser-proof-1785019349/proof.json` with
  screenshot
  `/tmp/tau-pi-tui-artifacts-browser-proof-1785019349/tau-artifacts-browser.svg`.
- Remaining gap: config write-scope/package override parity remains open; rich
  artifact inspection is now backed enough for tomorrow migration.

Latest slice evidence:

- Source inspected: Pi `core/export-html/index.ts`,
  `core/export-html/tool-renderer.ts`, Tau `session_export.py`, and
  `tests/test_session_export.py`.
- Destination preserved: Tau's branch/tree export structure, escaped raw message
  source, HTML/JSONL export entrypoints, and session storage/export command
  plumbing.
- Changed: assistant, compaction, and branch-summary text in HTML exports now
  gets a safe rendered Markdown view with tables, while raw Markdown remains in
  a collapsible disclosure. Local Markdown image links for supported image
  files are embedded as data URI figures with source-path captions and
  full-size browser links. Remote/data image links and
  missing/oversized/non-image files are not embedded.
- Mocked: no.
- Live: local HTML export writer with real local image bytes; no provider-live
  call.
- Proof: `uv run pytest tests/test_session_export.py tests/test_coding_session.py
  -q -k 'session_export or render_session_html or export_session'` reported
  `5 passed, 98 deselected`; `uv run ruff check
  src/tau_coding/session_export.py tests/test_session_export.py` reported all
  checks passed; `uv run python -m py_compile src/tau_coding/session_export.py
  tests/test_session_export.py` exited with no output.
- Render proof:
  `/tmp/tau-pi-tui-html-export-rich-proof-1785017738/proof.json` with HTML
  artifact
  `/tmp/tau-pi-tui-html-export-rich-proof-1785017738/session-rich-export.html`;
  openable-image proof
  `/tmp/tau-pi-tui-html-export-open-image-proof-1785017850/proof.json` with
  HTML artifact
  `/tmp/tau-pi-tui-html-export-open-image-proof-1785017850/session-openable-image-export.html`.
- Remaining gap: browser auto-open and deeper artifact gallery controls remain
  open; this slice makes the exported browser artifact itself rich enough for
  Markdown tables and linked local figures.

Latest slice evidence:

- Source inspected: Pi `interactive-mode.ts::handleExportCommand`,
  Pi slash-command `/export`, Pi RPC `export_html`, Tau `/export` command
  result handling, and `session_export.py`.
- Destination preserved: Tau's existing `session.export(...)` implementation,
  HTML/JSONL exporter, notification path, and `CommandOutputScreen` modal
  styling/keybindings.
- Changed: successful TUI `/export` now keeps the existing notification and
  additionally opens a persistent `Session export` modal containing the actual
  artifact format, path, and `file://` URI for browser inspection.
- Mocked: no.
- Live: local Textual `/export` command path with a real local HTML artifact;
  no provider-live call.
- Proof: `uv run pytest tests/test_tui_app.py tests/test_commands.py
  tests/test_coding_session.py -q -k 'export_command or parse_export or
  session_export'` reported `5 passed, 608 deselected`; `uv run ruff check
  src/tau_coding/tui/app.py tests/test_tui_app.py` reported all checks passed;
  `uv run python -m py_compile src/tau_coding/tui/app.py tests/test_tui_app.py`
  exited with no output.
- Render proof:
  `/tmp/tau-pi-tui-export-modal-proof-1785017459/proof.json` with screenshot
  `/tmp/tau-pi-tui-export-modal-proof-1785017459/tau-export-modal.svg` and
  artifact `/tmp/tau-pi-tui-export-modal-proof-1785017459/session.html`.
- Remaining gap: browser auto-open and a zoomable artifact gallery remain open;
  this slice makes the exported browser artifact durable and visible from the
  TUI.

Latest slice evidence:

- Source inspected: Pi `components/markdown.ts`, `components/image.ts`,
  `tool-execution.ts`, Tau `TranscriptMessageWidget`, `ThemedMarkdownWidget`,
  and `TerminalImage`.
- Destination preserved: Tau Textual transcript layout, Rich fallback renderer,
  Markdown/table styling, terminal image capability settings, and Tau's
  tool-result image payload path.
- Changed: assistant/custom/status Markdown that links to a local
  `png`, `jpg`, `gif`, `webp`, or `svg` file now mounts Tau `TerminalImage`
  renderables below the Markdown body. Remote/data URLs and missing/non-image
  files are ignored fail-closed. Normal Markdown tables still render through
  Textual/Rich Markdown.
- Mocked: no.
- Live: local Textual/Rich render paths with real image bytes read from disk;
  no provider-live call.
- Proof: `uv run pytest tests/test_tui_app.py -q -k
  'markdown_image or markdown_tables or textual_markdown_widget'` reported
  `5 passed, 452 deselected`; `uv run ruff check
  src/tau_coding/tui/widgets.py tests/test_tui_app.py` reported all checks
  passed; `uv run python -m py_compile src/tau_coding/tui/widgets.py
  tests/test_tui_app.py` exited with no output.
- Render proof:
  `/tmp/tau-pi-tui-markdown-image-proof-1785017225/proof.json` with screenshot
  `/tmp/tau-pi-tui-markdown-image-proof-1785017225/tau-markdown-table-image.svg`.
- Remaining gap: a browser/artifact sidecar is still needed for large,
  zoomable, or non-terminal-native figures; this slice covers local Markdown
  image links inside the terminal transcript.

Latest slice evidence:

- Source inspected: Pi `utils/image-convert.ts`,
  `tool-execution.ts::maybeConvertImagesForKitty()`, and
  `@earendil-works/pi-tui` `components/image.ts`; Tau `TerminalImage` and
  `terminal_image.py`.
- Destination preserved: Tau `TerminalImage` cache, Kitty image id allocation,
  iTerm2 rendering path, visible fallback when conversion is unavailable, and
  read-tool image preprocessing as a separate layer.
- Changed: when Kitty graphics are active and a `TerminalImage` payload is not
  `image/png`, Tau converts the base64 image to PNG with ImageMagick before
  emitting Kitty `f=100`. If conversion cannot run, Tau shows the visible image
  fallback instead of sending mismatched bytes through a PNG-only Kitty escape.
- Mocked: no.
- Live: local ImageMagick conversion and Tau terminal-image render path; no
  provider-live call.
- Proof: `uv run pytest tests/test_tui_terminal_image.py -q` reported
  `13 passed`; `uv run ruff check src/tau_coding/tui/terminal_image.py
  tests/test_tui_terminal_image.py` reported all checks passed; `uv run python
  -m py_compile src/tau_coding/tui/terminal_image.py
  tests/test_tui_terminal_image.py` exited with no output.
- Render/protocol proof:
  `/tmp/tau-pi-tui-kitty-jpeg-conversion-proof-1785016494/proof.json` with
  generated JPEG
  `/tmp/tau-pi-tui-kitty-jpeg-conversion-proof-1785016494/source-figure.jpg`
  and decoded emitted Kitty payload
  `/tmp/tau-pi-tui-kitty-jpeg-conversion-proof-1785016494/converted-kitty-payload.png`.
- Remaining gap: this proves the encoded Kitty payload contract, not actual
  terminal pixel display in every terminal emulator or a browser artifact
  mirror.

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

- Source inspected: Pi `user-message-selector.ts`, `trust-selector.ts`,
  `extension-selector.ts`, `config-selector.ts`, and default
  `tui.select.up/down` keybindings; Tau `UserMessagePickerScreen`,
  `TrustPickerScreen`, `ToolsReferenceScreen`, and
  `ToolsReferenceSearchInput`.
- Destination preserved: Tau `SessionTreeChoice` filtering and `tree_branch`
  flow, project trust store/session trust state, searchable tools reference,
  extension source labels, and configured `TuiKeybindings`.
- Changed: `/fork` and `/trust` accept Pi-style `j/k` movement in addition to
  configured up/down keys. `/tools` keeps printable `j/k` searchable in the
  focused search input while preserving `j/k` movement when the list itself is
  focused.
- Mocked: no.
- Live: local Textual UI interaction only; no provider-live call.
- Proof:
  `uv run pytest tests/test_tui_app.py -q -k 'tools_reference or fork_picker or trust_picker'`
  reported `13 passed, 442 deselected`; `uv run ruff check
  src/tau_coding/tui/app.py tests/test_tui_app.py` reported all checks passed;
  `uv run python -m py_compile src/tau_coding/tui/app.py
  tests/test_tui_app.py` exited with no output.
- Render proof:
  `/tmp/tau-pi-tui-tools-search-jk-proof-1785016868/proof.json` with screenshots
  `/tmp/tau-pi-tui-tools-search-jk-proof-1785016868/tau-tools-search-keeps-jk.svg`
  and
  `/tmp/tau-pi-tui-tools-search-jk-proof-1785016868/tau-tools-list-jk-navigation.svg`;
  earlier selector screenshots remain at
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
