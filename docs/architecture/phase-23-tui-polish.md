# Phase 23: Advanced TUI and Product Polish

Phase 23 improves the Textual frontend while keeping the reusable agent harness
independent of UI concerns.

The boundary remains:

```text
CodingSession emits AgentEvent values
        ↓
TuiEventAdapter updates TuiState
        ↓
Textual widgets render the transcript and controls
```

## Current polish slices

Live tool results now render successful output previews in the transcript,
matching restored session history. The TUI shows the first few lines and a
preview hint when additional content is hidden, so large `read` or `bash`
results do not flood the conversation while the durable session still keeps the
complete tool result for model context and replay.

Transcript blocks now render fenced code and persisted edit patches with Rich
syntax renderables inside the same Pi-style stacked message blocks. The
transcript state still stores plain role/text items; this is renderer-only
polish in the Textual frontend.

Assistant transcript blocks now also render common Markdown constructs such as
headings, bullets, blockquotes, links, inline code, and emphasis through Rich
Markdown. User, tool, status, and error blocks stay literal unless they are
handled by the explicit code or patch renderers, which keeps pasted prompts and
tool output predictable.

Live `edit` tool results now include their unified patch in the tool block. This
provides an inline diff view for file edits while keeping the event adapter and
Textual widgets decoupled. Tool-result metadata is now preserved in
`ToolResultMessage`, so restored session history can render the same edit patch
blocks from persisted JSONL entries.

The TUI also has a command-palette entry point. Pressing `Ctrl+K` focuses the
prompt, inserts `/`, and shows all slash-command completions using the existing
completion engine. Selection uses the same `Tab`, `Up`, and `Down` bindings as
ordinary slash-command autocomplete. Pressing `Enter` while a highlighted
completion would change the prompt now applies that completion without
submitting the prompt, matching common terminal picker behavior.

Slash-command output is now transient UI instead of transcript content. Short
command results use Textual notifications, and multi-line output such as
`/help`, `/skills`, `/sessions`, `/status`, `/resources`, and `/context` opens a
dismissible modal. This keeps command reference material out of the agent
conversation while preserving access to the information.

The same completion engine now suggests available values for `/model` and
`/provider` arguments. This gives the prompt a lightweight picker for model and
provider switching without adding a separate modal UI.

The prompt also suggests indexed session ids for `/resume <session-id>`, giving
the TUI a lightweight session picker path through the same completion UI.
Those rows now include session metadata such as title, model, and working
directory while preserving newest-first order from `SessionManager`. Submitting
the command reloads the selected session through `CodingSession` and rebuilds
the visible transcript in place.

The TUI also has a small modal session picker bound to `Ctrl+R` by default.
It lists indexed sessions with the same metadata used by resume completions,
then resumes the selected session through `CodingSession.resume()`. The picker
lives entirely in the Textual frontend; the portable harness still has no
session-selection policy.

The built-in Textual frontend now reads optional keybinding settings from
`~/.tau/tui.json`. This lets users remap the command palette, completion
navigation, session picker, cancellation, and quit keys while keeping the
configuration in `tau_coding.tui` instead of the reusable agent harness.

The same TUI settings file now supports named built-in themes. `tau-dark`
remains the default, and `high-contrast` provides a brighter dark palette. The
default theme is inspired by Toad's Textual UI: a darker surface, transparent
chrome, muted separators, a focused bottom prompt, and stacked conversation rows
with slim left accents instead of boxed cards. Theme selection feeds Textual CSS
variables plus Rich transcript/sidebar renderers, so the app chrome and message
blocks stay visually consistent without adding UI policy to `tau_agent`.

The frontend boundary is now documented in [Building a Custom TUI](../custom-tui.md).
That guide describes how another terminal UI can consume `CodingSession`,
`AgentEvent`, `TuiState`, and `TuiEventAdapter` without coupling to Textual
internals.

## Boundaries

These changes live in `tau_coding.tui`. The command registry still owns command
metadata, and `tau_agent` remains unaware of Textual, keybindings, slash
commands, and rendering.

## Still deferred

Phase 21 extensions remain intentionally unimplemented. Future polish may add
more advanced picker surfaces, but the current Phase 23 checklist items now have
foundational implementations.

## Tests

Coverage lives in:

```text
tests/test_tui_adapter.py
tests/test_tui_app.py
tests/test_tui_config.py
```
