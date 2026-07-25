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
| Tau workflows | None | `/workflows` | `TAU-ONLY` | Canonical Tau DAG launcher; must not be replaced by Pi code. |
| Tau provider internals | None | `/scillm` | `TAU-ONLY` | SciLLM remains Tau's local LLM proxy surface. |
| Tau evidence gates | None | `/permissions`, approval receipts | `TAU-ONLY` | Preserve fail-closed permission and receipt model. |
| Tau resources/skills | Extension commands only | `/resources`, `/skills`, `/skill`, `/tools`, `/prompts` | `TAU-ONLY` | Required for memory-first and skill-driven operation. |

## Interactive Components

| Area | Pi component(s) | Tau surface | Status | Next action |
| --- | --- | --- | --- | --- |
| Prompt editor | `custom-editor`, `custom-entry`, `keybinding-hints` | `PromptInput`, extension input hooks | `PARTIAL` | Keep Pi-like keybindings; avoid replacing Tau extension input plumbing. |
| Model selector | `model-selector`, `scoped-models-selector` | `ModelPickerScreen` | `MATCHED` | Search, tabs, scoped membership, provider toggles, and reorder are present. |
| Session selector | `session-selector`, `session-selector-search` | `SessionPickerScreen` | `MATCHED` | Search, current/all, named-only, path toggle, sort, rename, delete are present. |
| Settings selector | `settings-selector`, related selectors | `SettingsPickerScreen` and picker screens | `PARTIAL` | Tau backs most daily settings; do not add dead Pi toggles without backing behavior. |
| Config selector | `config-selector` | `ConfigMapScreen` | `PARTIAL` | Scope tabs exist; package/write-scope editing still missing. |
| Login/OAuth | `login-dialog`, `oauth-selector` | login provider/method/OAuth screens | `PARTIAL` | Good enough for API/OAuth login, but daily auth readiness should be more visible. |
| Tool execution | `tool-execution`, `bash-execution`, `diff` | transcript renderers in `state.py` | `MUST/PARTIAL` | Tau mostly renders text; custom tool render hooks and richer diff/shell affordances remain. |
| Status/footer | `footer`, `status-indicator`, `countdown-timer` | Tau footer data provider and retry countdown | `PARTIAL` | Footer extensibility exists; first-screen run/auth readiness needs stronger visibility. |
| Extension UI | `extension-selector`, `extension-input`, `extension-editor`, custom UI | Tau extension screens and chrome hooks | `MUST/PARTIAL` | Preserve current Tau extension API; add missing Pi-compatible behavior incrementally. |
| Images | `show-images-selector`, image component | Tau image visibility setting and image payload rendering | `MATCHED` | Retain current terminal-safe image controls. |
| Workflow/DAG progress | None in Pi | `WorkflowPickerScreen`, DAG/workflow receipts | `TAU-ONLY/MUST` | This is Tau's differentiator and must remain first-class in the TUI. |

## Tomorrow-Usability Ranking

1. `MUST`: Live harness readiness in the TUI. A user should see provider,
   model, auth/readiness, cwd, context, queued commands, and Tau DAG/workflow
   entry points without archaeology.
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

Port the highest-value daily-use gap that is still local and bounded:

`TUI readiness panel`: expose the active provider, model, credential/readiness
state, cwd, context budget, queued messages, and Tau DAG workflow entry point
together in the existing session sidebar/footer area. This moves Tau toward
tomorrow use without touching SciLLM internals or replacing Tau-only DAG
features.
